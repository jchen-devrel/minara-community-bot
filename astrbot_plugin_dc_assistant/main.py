"""
AstrBot Discord DevRel Assistant

- Collects messages from watched channels / forums
- Detects @mentions + keyword matches → LLM draft → local pending queue
- All data saved locally (JSONL) and synced to Feishu Bitable
- Pairs with dc-devrel-reviewer/ for Excel review → batch reply workflow
"""

import asyncio
import json
import re
import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star
from astrbot.api import logger

try:
    import aiohttp
except ImportError:
    aiohttp = None

PLUGIN_DIR = Path(__file__).resolve().parent
CONFIG_PATH = PLUGIN_DIR / "config.json"
DATA_DIR = PLUGIN_DIR / "data"

DISCORD_API = "https://discord.com/api/v10"
FEISHU_TOKEN_URL = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
CONTEXT_BUFFER_SIZE = 30

_feishu_token_value: str | None = None
_feishu_token_expires: float = 0.0
_seen_msg_ids: set[str] = set()
_mention_counter: int = 0


# ═══════════════════════════════════════════════════════════════════
#  Helpers — config / local storage
# ═══════════════════════════════════════════════════════════════════

def _load_config() -> dict[str, Any]:
    if not CONFIG_PATH.is_file():
        return {}
    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


def _ensure_data_dir():
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def _append_jsonl(filename: str, record: dict):
    _ensure_data_dir()
    with (DATA_DIR / filename).open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def _read_jsonl(filename: str) -> list[dict]:
    path = DATA_DIR / filename
    if not path.is_file():
        return []
    records = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return records


# ═══════════════════════════════════════════════════════════════════
#  Helpers — Feishu
# ═══════════════════════════════════════════════════════════════════

async def _get_feishu_token(session: "aiohttp.ClientSession",
                            app_id: str, app_secret: str) -> str:
    global _feishu_token_value, _feishu_token_expires
    now = time.time()
    if _feishu_token_value and now < _feishu_token_expires - 120:
        return _feishu_token_value

    async with session.post(
        FEISHU_TOKEN_URL,
        json={"app_id": app_id, "app_secret": app_secret},
        headers={"Content-Type": "application/json; charset=utf-8"},
    ) as resp:
        body = await resp.json()
    if body.get("code") != 0:
        raise RuntimeError(f"飞书 token 失败: {body}")
    _feishu_token_value = body["tenant_access_token"]
    _feishu_token_expires = now + int(body.get("expire", 7200))
    return _feishu_token_value


async def _feishu_create_record(session: "aiohttp.ClientSession", token: str,
                                app_token: str, table_id: str,
                                fields: dict) -> None:
    url = (f"https://open.feishu.cn/open-apis/bitable/v1"
           f"/apps/{app_token}/tables/{table_id}/records")
    async with session.post(
        url,
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json; charset=utf-8"},
        json={"fields": fields},
    ) as resp:
        body = await resp.json()
    if body.get("code") != 0:
        raise RuntimeError(f"飞书写入失败: {body}")


# ═══════════════════════════════════════════════════════════════════
#  Helpers — Discord REST
# ═══════════════════════════════════════════════════════════════════

async def _discord_reply(session: "aiohttp.ClientSession",
                         bot_token: str, channel_id: str, text: str,
                         *, reply_to: str | None = None) -> None:
    url = f"{DISCORD_API}/channels/{channel_id}/messages"
    payload: dict[str, Any] = {"content": text}
    if reply_to:
        payload["message_reference"] = {"message_id": reply_to}
    async with session.post(
        url,
        headers={"Authorization": f"Bot {bot_token}",
                 "Content-Type": "application/json"},
        json=payload,
    ) as resp:
        if resp.status >= 400:
            body = await resp.text()
            logger.error(f"[dc_assistant] Discord 回复失败: {resp.status} {body}")


# ═══════════════════════════════════════════════════════════════════
#  Helpers — LLM (OpenRouter)
# ═══════════════════════════════════════════════════════════════════

async def _llm_generate(session: "aiohttp.ClientSession",
                        api_key: str, model: str,
                        system_prompt: str, user_prompt: str) -> str:
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.5,
    }
    async with session.post(
        "https://openrouter.ai/api/v1/chat/completions",
        headers={"Authorization": f"Bearer {api_key}",
                 "Content-Type": "application/json"},
        json=body,
    ) as resp:
        data = await resp.json()
    return data["choices"][0]["message"]["content"].strip()


# ═══════════════════════════════════════════════════════════════════
#  Channel / mention matching
# ═══════════════════════════════════════════════════════════════════

def _should_watch(channel_name: str, channel_id: str, cfg: dict) -> bool:
    """Return True if this channel/forum is in our watch list, or no watch list is set (watch all)."""
    watch_ch = cfg.get("watch_channels") or []
    watch_fm = cfg.get("watch_forums") or []
    watch_all = watch_ch + watch_fm
    if not watch_all:
        return True
    name_low = channel_name.lower()
    for w in watch_all:
        w = str(w).strip()
        if not w:
            continue
        if w == channel_id or w.lower() in name_low or name_low in w.lower():
            return True
    return False


def _at_hazel(full_text: str, raw_msg: Any, cfg: dict) -> bool:
    """正文或 mentions 是否指向 Hazel（用于单独标注 BD）。"""
    hazel_ids = [str(i).strip() for i in (cfg.get("hazel_discord_ids") or []) if i]
    hazel_names = [
        n.lower().strip().lstrip("@")
        for n in (cfg.get("hazel_usernames") or ["hazel"])
        if str(n).strip()
    ]
    for uid in hazel_ids:
        if f"<@{uid}>" in full_text or f"<@!{uid}>" in full_text:
            return True
    ft_low = full_text.lower()
    for n in hazel_names:
        if n and (f"@{n}" in ft_low or f"<@{n}>" in ft_low):
            return True
    mentions_list = getattr(raw_msg, "mentions", None) or []
    if isinstance(mentions_list, list):
        for m in mentions_list:
            mid = str(getattr(m, "id", "") or "")
            if mid and mid in hazel_ids:
                return True
            un = (getattr(m, "name", "") or "").lower()
            for n in hazel_names:
                if n and un == n:
                    return True
    return False


def _is_mention(full_text: str, raw_msg: Any, cfg: dict) -> bool:
    """Check if the message mentions us by username, ID, or Discord @."""
    my_names = [u.lower().strip().lstrip("@")
                for u in (cfg.get("my_usernames") or []) if u]
    my_ids = [str(i).strip() for i in (cfg.get("my_discord_ids") or []) if i]

    ft_low = full_text.lower()

    for u in my_names:
        if f"@{u}" in ft_low or u in ft_low:
            return True

    mentions_list = getattr(raw_msg, "mentions", None) or []
    if isinstance(mentions_list, list):
        for m in mentions_list:
            mid = str(getattr(m, "id", "") or "")
            if mid and mid in my_ids:
                return True

    for uid in my_ids:
        if f"<@{uid}>" in full_text or f"<@!{uid}>" in full_text:
            return True

    return False


def _matches_keywords(text: str, keywords: list[str]) -> list[str]:
    """Return list of matched keywords."""
    if not text or not keywords:
        return []
    matched = []
    text_low = text.lower()
    for kw in keywords:
        kw = (kw or "").strip()
        if not kw:
            continue
        if kw.lower() in text_low:
            matched.append(kw)
    return matched


# ═══════════════════════════════════════════════════════════════════
#  Extract Discord message metadata from AstrBot event
# ═══════════════════════════════════════════════════════════════════

def _extract_dc_info(msg, raw) -> dict[str, Any]:
    """Pull Discord-specific fields from AstrBot message objects."""
    channel = getattr(raw, "channel", None)
    author = getattr(raw, "author", None)
    thread = getattr(raw, "thread", None)

    channel_id = str(getattr(channel, "id", "") or msg.group_id or "")
    channel_name = getattr(channel, "name", "") or ""
    guild_id = str(getattr(channel, "guild_id", "") or
                   getattr(getattr(channel, "guild", None), "id", "") or "")

    is_thread = bool(getattr(channel, "parent_id", None))
    parent_id = str(getattr(channel, "parent_id", "") or "")
    if thread:
        is_thread = True
        parent_id = parent_id or str(getattr(thread, "parent_id", "") or "")

    author_id = str(getattr(author, "id", "") or "")
    author_name = (getattr(author, "global_name", "") or
                   getattr(author, "display_name", "") or
                   getattr(author, "name", "") or "")
    author_username = getattr(author, "name", "") or ""
    is_bot = getattr(author, "bot", False)

    msg_id = str(getattr(raw, "id", "") or msg.message_id or "")
    content = msg.message_str or getattr(raw, "content", "") or ""

    if not channel_name and msg.group_id:
        channel_name = str(msg.group_id)
    if not author_name and msg.sender:
        author_name = getattr(msg.sender, "nickname", "") or ""

    return {
        "channel_id": channel_id,
        "channel_name": channel_name,
        "guild_id": guild_id,
        "is_thread": is_thread,
        "parent_id": parent_id,
        "author_id": author_id,
        "author_name": author_name,
        "author_username": author_username,
        "is_bot": is_bot,
        "msg_id": msg_id,
        "content": content,
    }


# ═══════════════════════════════════════════════════════════════════
#  Plugin class
# ═══════════════════════════════════════════════════════════════════

def _load_ack_sent_ids() -> set[str]:
    p = DATA_DIR / "auto_ack_sent_ids.txt"
    if not p.is_file():
        return set()
    return {ln.strip() for ln in p.read_text("utf-8").splitlines() if ln.strip()}


def _persist_ack_sent_id(msg_id: str) -> None:
    _ensure_data_dir()
    with (DATA_DIR / "auto_ack_sent_ids.txt").open("a", encoding="utf-8") as f:
        f.write(msg_id + "\n")


class DcAssistant(Star):

    def __init__(self, context: Context):
        super().__init__(context)
        self._lock = asyncio.Lock()
        self._msg_buffer: dict[str, deque] = defaultdict(
            lambda: deque(maxlen=CONTEXT_BUFFER_SIZE))
        self._pending: dict[str, dict] = {}
        self._cfg = _load_config()
        self._ack_sent: set[str] = _load_ack_sent_ids()
        _ensure_data_dir()
        logger.info("[dc_assistant] 插件已加载")

    # ─────────────────────────────────────────────────────────
    #  Entry: every incoming Discord message
    # ─────────────────────────────────────────────────────────

    @filter.event_message_type(filter.EventMessageType.ALL, priority=100)
    async def on_message(self, event: AstrMessageEvent):
        self._cfg = _load_config()
        cfg = self._cfg

        msg = event.message_obj
        raw = msg.raw_message
        info = _extract_dc_info(msg, raw)

        if info["is_bot"]:
            return

        if not _should_watch(info["channel_name"], info["channel_id"], cfg):
            return

        mid = info["msg_id"]
        if mid and mid in _seen_msg_ids:
            return
        if mid:
            _seen_msg_ids.add(mid)
            if len(_seen_msg_ids) > 10000:
                _seen_msg_ids.clear()

        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        text = info["content"]

        mention = _is_mention(text, raw, cfg)
        match_kws = cfg.get("match_keywords") or []
        matched = _matches_keywords(text, match_kws)
        at_hazel = _at_hazel(text, raw, cfg)

        record = {
            "msg_id": mid,
            "channel_id": info["channel_id"],
            "channel_name": info["channel_name"],
            "guild_id": info["guild_id"],
            "is_thread": info["is_thread"],
            "parent_id": info["parent_id"],
            "author_id": info["author_id"],
            "author_name": info["author_name"],
            "author_username": info["author_username"],
            "text": text,
            "timestamp": now_str,
            "is_mention": mention,
            "at_hazel": at_hazel,
            "matched_keywords": matched,
        }

        self._msg_buffer[info["channel_id"]].append(record)
        _append_jsonl("messages.jsonl", record)

        logger.info(
            f"[dc_assistant] msg: ch={info['channel_name']!r} "
            f"author={info['author_name']!r} text={text[:80]!r}")

        triggers: list[str] = []
        if mention:
            triggers.append("mention")
        if matched:
            triggers.append("keyword")
        if at_hazel:
            triggers.append("hazel")

        if triggers:
            await self._handle_notification(record, triggers, matched)

        # ── Auto-ack（英文收录回执，可选）──
        ack = cfg.get("auto_ack") or {}
        if (
            ack.get("enabled")
            and aiohttp
            and mid
            and mid not in self._ack_sent
        ):
            mode = (ack.get("mode") or "all").strip().lower()
            should_ack = mode == "all" or (
                mode == "mention" and (mention or at_hazel)
            ) or (mode == "hazel" and at_hazel)
            if should_ack:
                token = (cfg.get("discord_bot_token") or "").strip()
                tpl = (ack.get("message") or "").strip() or (
                    "Thanks for your message — it has been logged and our team "
                    "will look into it as soon as possible."
                )
                mention_author = ack.get("mention_author", True)
                body = tpl
                if mention_author and info["author_id"]:
                    body = f"<@{info['author_id']}> {body}"
                try:
                    async with aiohttp.ClientSession() as session:
                        await _discord_reply(
                            session, token, info["channel_id"], body,
                            reply_to=mid,
                        )
                    self._ack_sent.add(mid)
                    _persist_ack_sent_id(mid)
                    logger.info(f"[dc_assistant] auto_ack sent for msg {mid}")
                except Exception as e:
                    logger.error(f"[dc_assistant] auto_ack: {e}")

        # ── Feishu sync: messages table ──
        fs = cfg.get("feishu") or {}
        if fs.get("enabled") and fs.get("messages_table_id"):
            try:
                await self._fs_sync_message(record, fs)
            except Exception as e:
                logger.error(f"[dc_assistant] 飞书消息同步: {e}")

    # ─────────────────────────────────────────────────────────
    #  @mention / keyword match → LLM draft → save pending
    # ─────────────────────────────────────────────────────────

    async def _handle_notification(self, record: dict,
                                   triggers: list[str],
                                   matched_kws: list[str]):
        global _mention_counter
        _mention_counter += 1
        mid = f"dc_{int(time.time())}_{_mention_counter}"

        cfg = self._cfg
        llm_key = (cfg.get("openrouter_api_key") or "").strip()
        llm_model = cfg.get("openrouter_model", "openai/gpt-4o-mini")

        ctx_msgs = list(self._msg_buffer[record["channel_id"]])
        ctx_text = "\n".join(
            f"[{m['author_name']}]: {m['text']}" for m in ctx_msgs[-15:])

        has_mention = "mention" in triggers
        has_kw = "keyword" in triggers
        has_hazel = "hazel" in triggers

        draft = ""
        if llm_key and aiohttp:
            if has_mention:
                sys_p = (
                    "You are a DevRel engineer replying in a Discord channel. "
                    "Someone mentioned you. Write a concise, professional, friendly reply "
                    "in the SAME LANGUAGE as the message."
                )
            elif has_hazel:
                sys_p = (
                    "You are a DevRel / BD engineer. The message tags or concerns "
                    "team member Hazel. Write a concise, professional draft reply "
                    "in the SAME LANGUAGE as the message."
                )
            else:
                sys_p = (
                    "You are a DevRel engineer monitoring Discord. "
                    "A message matched your watch keywords. "
                    "Write a concise, professional, helpful reply "
                    "in the SAME LANGUAGE as the message."
                )

            kw_line = f"\nMatched keywords: {', '.join(matched_kws)}" if matched_kws else ""
            usr_p = (
                f"Channel: #{record['channel_name']}\n"
                f"Recent context:\n{ctx_text}\n\n"
                f"Target message:\n[{record['author_name']}]: {record['text']}"
                f"{kw_line}\n\n"
                f"Generate a draft reply for me to review before sending."
            )
            try:
                async with aiohttp.ClientSession() as session:
                    draft = await _llm_generate(
                        session, llm_key, llm_model, sys_p, usr_p)
            except Exception as e:
                logger.error(f"[dc_assistant] LLM draft: {e}")
                draft = f"[LLM error: {e}]"

        trigger_labels = []
        if has_mention:
            trigger_labels.append("@提及")
        if has_hazel:
            trigger_labels.append("@Hazel/BD")
        if has_kw:
            trigger_labels.append(f"关键词({', '.join(matched_kws)})")
        trigger_line = " · ".join(trigger_labels)

        mention_rec = {
            "mention_id": mid,
            "msg_id": record["msg_id"],
            "channel_id": record["channel_id"],
            "channel_name": record["channel_name"],
            "guild_id": record["guild_id"],
            "is_thread": record["is_thread"],
            "parent_id": record["parent_id"],
            "author_id": record["author_id"],
            "author_name": record["author_name"],
            "author_username": record["author_username"],
            "text": record["text"],
            "context": [m["text"] for m in ctx_msgs[-10:]],
            "draft_reply": draft,
            "status": "pending",
            "final_reply": "",
            "reply_timestamp": "",
            "timestamp": record["timestamp"],
            "triggers": triggers,
            "trigger_label": trigger_line,
            "matched_keywords": matched_kws,
            "at_hazel": bool(record.get("at_hazel")),
        }
        self._pending[mid] = mention_rec
        _append_jsonl("mentions.jsonl", mention_rec)

        logger.info(
            f"[dc_assistant] 🔔 新待审核: {mid} "
            f"trigger={trigger_line} "
            f"from={record['author_name']} "
            f"text={record['text'][:60]}")

        # ── Feishu: mentions + review table ──
        fs = cfg.get("feishu") or {}
        if fs.get("enabled") and aiohttp:
            try:
                async with aiohttp.ClientSession() as session:
                    tk = await _get_feishu_token(
                        session, fs["app_id"], fs["app_secret"])

                    if fs.get("mentions_table_id"):
                        await _feishu_create_record(
                            session, tk, fs["app_token"],
                            fs["mentions_table_id"], {
                                "mention_id": mid,
                                "频道": record["channel_name"],
                                "发送者": record["author_name"],
                                "消息内容": record["text"][:2000],
                                "AI草稿": draft[:2000],
                                "状态": "pending",
                                "触发类型": trigger_line,
                                "时间": record["timestamp"],
                            })

                    if fs.get("review_table_id"):
                        ctx = mention_rec.get("context") or []
                        await _feishu_create_record(
                            session, tk, fs["app_token"],
                            fs["review_table_id"], {
                                "mention_id": mid,
                                "频道": record["channel_name"],
                                "触发类型": trigger_line,
                                "发送者": record["author_name"],
                                "用户名": record["author_username"],
                                "原始消息": record["text"][:2000],
                                "上下文": "\n".join(ctx[-5:])[:2000],
                                "AI草稿": draft[:2000],
                                "操作": "",
                                "最终回复": "",
                                "状态": "pending",
                                "channel_id": record["channel_id"],
                                "msg_id": record["msg_id"],
                                "时间": record["timestamp"],
                            })
            except Exception as e:
                logger.error(f"[dc_assistant] 飞书审核同步: {e}")

    # ─────────────────────────────────────────────────────────
    #  Feishu sync: messages table
    # ─────────────────────────────────────────────────────────

    async def _fs_sync_message(self, rec: dict, fs: dict):
        async with aiohttp.ClientSession() as s:
            tk = await _get_feishu_token(s, fs["app_id"], fs["app_secret"])
            kw_str = ", ".join(rec.get("matched_keywords") or [])
            await _feishu_create_record(s, tk, fs["app_token"],
                                        fs["messages_table_id"], {
                "频道": rec["channel_name"],
                "发送者": rec["author_name"],
                "用户名": rec["author_username"],
                "消息内容": rec["text"][:2000],
                "时间": rec["timestamp"],
                "是否@我": "是" if rec.get("is_mention") else "否",
                "匹配关键词": kw_str or "无",
            })
