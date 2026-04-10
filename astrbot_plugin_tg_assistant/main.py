"""
AstrBot Telegram Assistant

- Collects ALL messages from groups/channels the bot is in
- Detects @mentions + DevRel bug/feedback (even without @) → LLM draft → owner DM
- Owner approves/edits/skips → bot replies to original chat
- Daily summary of all chats via LLM
- All data saved locally (JSONL) and synced to Feishu Bitable
"""

import asyncio
import json
import re
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star
from astrbot.api import logger

try:
    import aiohttp
except ImportError:
    aiohttp = None

# ── Paths ──────────────────────────────────────────────────────────
PLUGIN_DIR = Path(__file__).resolve().parent
CONFIG_PATH = PLUGIN_DIR / "config.json"
DATA_DIR = PLUGIN_DIR / "data"

# ── Constants ──────────────────────────────────────────────────────
TG_API = "https://api.telegram.org"
FEISHU_TOKEN_URL = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
CONTEXT_BUFFER_SIZE = 30

# 默认 Bug / 技术反馈关键词（可与 config 中 devrel_bug_keywords 合并）
_DEFAULT_BUG_KEYWORDS = [
    # English
    "bug", "crash", "crashed", "broken", "not working", "doesn't work",
    "doesnt work", "don't work", "dont work", "error", "failed", "failure",
    "frozen", "freeze", "stuck", "unable", "cannot", "can't", "cant",
    "issue with", "something wrong", "glitch", "exception", "timeout",
    "not loading", "won't load", "wont load", "404", "500 error",
    # 中文
    "崩溃", "闪退", "卡死", "卡住了", "无法", "不能", "用不了", "不能用",
    "报错", "错误代码", "出错了", "失败了", "失败", "异常", "有 bug",
    "有bug", "bug了", "坏了", "不正常", "收不到", "上不去", "连不上",
    "登不上", "加载不了", "没反应", "黑屏", "白屏", "闪了一下",
]

# ── Module-level state ─────────────────────────────────────────────
_feishu_token_value: str | None = None
_feishu_token_expires: float = 0.0
_seen_msg_ids: set[str] = set()
_mention_counter: int = 0


# ═══════════════════════════════════════════════════════════════════
#  Helpers – config / local storage
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


def _devrel_cfg(cfg: dict) -> dict:
    return cfg.get("devrel") or {}


def _bug_keywords(cfg: dict) -> list[str]:
    d = _devrel_cfg(cfg)
    extra = d.get("bug_feedback_keywords") or []
    seen: set[str] = set()
    out: list[str] = []
    for kw in _DEFAULT_BUG_KEYWORDS + list(extra):
        k = (kw or "").strip()
        if k and k.lower() not in seen:
            seen.add(k.lower())
            out.append(k)
    return out


def _feishu_trigger_display(raw: str | None) -> str:
    if not raw:
        return "无"
    parts = raw.split("+")
    m = {"mention": "@提及", "bug": "Bug反馈"}
    return "+".join(m.get(p, p) for p in parts)


# 「bug」单独做整词匹配，避免 debugging 等误触；其余仍用子串（中英文）
_EN_BOUNDARY_KEYWORDS = frozenset({"bug"})


def _keyword_matches(text: str, kw: str) -> bool:
    if not kw:
        return False
    low = text.lower()
    k = kw.lower()
    if k in _EN_BOUNDARY_KEYWORDS:
        return bool(re.search(rf"(?<![a-zA-Z]){re.escape(k)}(?![a-zA-Z])", low))
    return k in low or kw in text


def _text_matches_bug_keywords(text: str, keywords: list[str]) -> bool:
    if not text or not keywords:
        return False
    return any(_keyword_matches(text, kw) for kw in keywords)


# ═══════════════════════════════════════════════════════════════════
#  Helpers – Feishu Bitable
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
#  Helpers – Telegram Bot API
# ═══════════════════════════════════════════════════════════════════

async def _tg_api_call(session: "aiohttp.ClientSession",
                       bot_token: str, method: str, **params) -> dict:
    url = f"{TG_API}/bot{bot_token}/{method}"
    async with session.post(url, json=params) as resp:
        body = await resp.json()
    if not body.get("ok"):
        logger.error(f"[tg_assistant] TG API {method}: {body}")
    return body


async def _tg_send(session: "aiohttp.ClientSession", bot_token: str,
                   chat_id: str | int, text: str, *,
                   reply_to: int | None = None) -> dict:
    params: dict[str, Any] = {"chat_id": chat_id, "text": text}
    if reply_to:
        params["reply_parameters"] = {"message_id": reply_to}
    return await _tg_api_call(session, bot_token, "sendMessage", **params)


# ═══════════════════════════════════════════════════════════════════
#  Helpers – LLM (OpenRouter)
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


async def _llm_is_bug_report(
    session: "aiohttp.ClientSession",
    api_key: str, model: str, text: str,
) -> bool:
    sys_p = (
        "You classify Telegram messages for a DevRel engineer. "
        "Answer ONLY yes or no (lowercase)."
    )
    usr_p = (
        "Is this message reporting a software bug, crash, broken feature, "
        "or serious technical failure of a product/app? "
        "Ignore general chat, greetings, trading talk without errors, and pure questions.\n\n"
        f"Message:\n{text[:1500]}"
    )
    raw = await _llm_generate(session, api_key, model, sys_p, usr_p)
    return raw.strip().lower().startswith("y")


# ═══════════════════════════════════════════════════════════════════
#  Plugin class
# ═══════════════════════════════════════════════════════════════════

class TgAssistant(Star):

    def __init__(self, context: Context):
        super().__init__(context)
        self._lock = asyncio.Lock()
        self._msg_buffer: dict[str, deque] = defaultdict(
            lambda: deque(maxlen=CONTEXT_BUFFER_SIZE))
        self._pending: dict[str, dict] = {}
        self._daily_task: asyncio.Task | None = None
        self._cfg = _load_config()
        _ensure_data_dir()
        logger.info("[tg_assistant] 插件已加载")

    # ── background daily-summary（仅私聊发给你；可 daily_summary_enabled=false 全关） ──
    def _start_daily_task(self):
        self._cfg = _load_config()
        if not self._cfg.get("daily_summary_enabled", True):
            return
        if self._daily_task is None or self._daily_task.done():
            self._daily_task = asyncio.create_task(self._daily_loop())

    # ─────────────────────────────────────────────────────────
    #  Entry point: every incoming message
    # ─────────────────────────────────────────────────────────

    @filter.event_message_type(filter.EventMessageType.ALL, priority=100)
    async def on_message(self, event: AstrMessageEvent):
        self._start_daily_task()

        msg = event.message_obj
        raw = msg.raw_message

        chat_id   = str(msg.group_id or _attr(raw, "chat.id", ""))
        chat_type = _attr(raw, "chat.type", "")
        chat_title = (_attr(raw, "chat.title", "")
                      or _attr(raw, "chat.first_name", "")
                      or chat_id)

        _from_id = (_attr(raw, "from_user.id", "")
                    or _attr(raw, "from.id", ""))
        _sender_fallback = (getattr(msg.sender, "user_id", "")
                            if msg.sender else "")
        sender_id = str(_from_id or _sender_fallback or "")

        _from_un = (_attr(raw, "from_user.username", "")
                    or _attr(raw, "from.username", ""))
        _sender_un_fb = (getattr(msg.sender, "nickname", "")
                         if msg.sender else "")
        sender_un = _from_un or _sender_un_fb or ""

        sender_name = _tg_display_name(raw, msg)
        text       = msg.message_str or ""
        msg_id     = str(msg.message_id or _attr(raw, "message_id", ""))

        # AstrBot 可能从 message_str 剥掉 [At:xxx]，从 raw 和 message chain 补回来
        raw_text = str(getattr(raw, "text", "") or "")
        _msg_chain = getattr(msg, "message", None) or []
        _chain_str = ""
        if _msg_chain:
            try:
                _chain_str = " ".join(
                    str(getattr(seg, "data", "") or getattr(seg, "text", "") or seg)
                    for seg in _msg_chain)
            except Exception:
                _chain_str = str(_msg_chain)
        full_text = f"{raw_text} {text} {_chain_str}"

        logger.info(
            f"[tg_assistant] on_message: chat_type={chat_type!r} "
            f"chat_id={chat_id} sender_id={sender_id} "
            f"text={text[:80]!r} raw_text={raw_text[:80]!r} msg_id={msg_id}")

        if _attr(raw, "from_user.is_bot", False) or _attr(raw, "from.is_bot", False):
            logger.info("[tg_assistant] skip: is_bot")
            return

        if msg_id and msg_id in _seen_msg_ids:
            return
        if msg_id:
            _seen_msg_ids.add(msg_id)
            if len(_seen_msg_ids) > 10000:
                _seen_msg_ids.clear()

        owner_id = str(self._cfg.get("owner_telegram_id", ""))

        # ── Private DM from owner → review commands ──
        has_group = bool(msg.group_id) or chat_id.startswith("-")
        is_private = (chat_type == "private") or (not has_group and chat_type in ("", "private"))
        is_owner = sender_id == owner_id and owner_id
        logger.info(
            f"[tg_assistant] check: has_group={has_group} "
            f"is_private={is_private} is_owner={is_owner} "
            f"owner_id={owner_id!r}")

        if is_private and is_owner and not has_group:
            await self._handle_command(text)
            return

        # ── Collect group / supergroup / channel message ──
        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        record = {
            "msg_id": msg_id,
            "chat_id": chat_id,
            "chat_title": chat_title,
            "chat_type": chat_type,
            "sender_id": sender_id,
            "sender_username": sender_un,
            "sender_name": sender_name,
            "text": text,
            "timestamp": now_str,
            "is_mention": False,
            "is_bug_feedback": False,
            "devrel_trigger": "",
        }
        self._msg_buffer[chat_id].append(record)
        _append_jsonl("messages.jsonl", record)

        # ── @mention + DevRel bug/feedback（无 @ 也推送） ──
        my_names = [u.lower().lstrip("@")
                    for u in (self._cfg.get("my_usernames") or []) if u]
        ft_low = full_text.lower()
        is_mention = any(
            f"@{u}" in ft_low or f"[at:{u}]" in ft_low
            or f"at:{u}" in ft_low
            for u in my_names)

        drel = _devrel_cfg(self._cfg)
        bug_push_on = drel.get("bug_feedback_push_enabled", True)
        is_bug = False
        if bug_push_on and has_group:
            min_len = int(drel.get("min_text_length_for_bug", 12))
            stripped = full_text.strip()
            if len(stripped) >= min_len and not text.strip().startswith("/"):
                kws = _bug_keywords(self._cfg)
                is_bug = _text_matches_bug_keywords(full_text, kws)
                if (not is_bug) and drel.get("use_llm_bug_classifier"):
                    lk = (self._cfg.get("openrouter_api_key") or "").strip()
                    lm = self._cfg.get("openrouter_model", "openai/gpt-4o-mini")
                    if lk and aiohttp:
                        try:
                            async with aiohttp.ClientSession() as s:
                                is_bug = await _llm_is_bug_report(s, lk, lm, text)
                        except Exception as e:
                            logger.error(f"[tg_assistant] LLM bug classify: {e}")

        logger.info(
            f"[tg_assistant] detect: is_mention={is_mention} "
            f"is_bug={is_bug} full_text={full_text[:100]!r}")

        triggers: list[str] = []
        if is_mention:
            triggers.append("mention")
        if is_bug:
            triggers.append("bug")

        record["is_mention"] = is_mention
        record["is_bug_feedback"] = is_bug
        record["devrel_trigger"] = "+".join(triggers) if triggers else ""

        if triggers:
            await self._handle_devrel_notification(record, triggers)

        # ── Feishu sync (messages table) ──
        fs = self._cfg.get("feishu") or {}
        if fs.get("enabled") and fs.get("messages_table_id"):
            try:
                await self._fs_sync_message(record, fs)
            except Exception as e:
                logger.error(f"[tg_assistant] 飞书消息同步: {e}")

    # ─────────────────────────────────────────────────────────
    #  @mention / DevRel bug → LLM draft → DM owner
    # ─────────────────────────────────────────────────────────

    async def _handle_devrel_notification(self, record: dict, triggers: list[str]):
        global _mention_counter
        _mention_counter += 1
        mid = f"m_{int(time.time())}_{_mention_counter}"

        cfg = self._cfg
        bot_token = (cfg.get("telegram_bot_token") or "").strip()
        owner_id  = str(cfg.get("owner_telegram_id", ""))
        llm_key   = (cfg.get("openrouter_api_key") or "").strip()
        llm_model = cfg.get("openrouter_model", "openai/gpt-4o-mini")

        ctx_msgs = list(self._msg_buffer[record["chat_id"]])
        ctx_text = "\n".join(
            f"[{m['sender_name']}]: {m['text']}" for m in ctx_msgs[-15:])

        has_m = "mention" in triggers
        has_b = "bug" in triggers

        draft = ""
        if llm_key and aiohttp:
            if has_m and has_b:
                sys_p = (
                    "You are DevRel for a crypto/AI product. The user was @mentioned "
                    "AND the message looks like a bug or technical issue. "
                    "Write ONE concise reply in the SAME LANGUAGE as the message: "
                    "acknowledge, show empathy, offer to help / ask 1–2 triage questions "
                    "(version, device, steps). Professional and friendly."
                )
                usr_p = (
                    f"Group: {record['chat_title']}\n"
                    f"Recent context:\n{ctx_text}\n\n"
                    f"Message:\n[{record['sender_name']}]: {record['text']}\n\n"
                    f"Generate a reply for me to send after I approve."
                )
            elif has_b:
                sys_p = (
                    "You are DevRel. A community member reported a bug or technical issue "
                    "but did NOT @mention you — you still need to respond as the team. "
                    "Reply in the SAME LANGUAGE as the message. "
                    "Thank them, acknowledge the issue, ask brief triage "
                    "(environment, repro steps, screenshots if any). "
                    "Do not promise a fix date unless generic."
                )
                usr_p = (
                    f"Group: {record['chat_title']}\n"
                    f"Recent context:\n{ctx_text}\n\n"
                    f"Bug/feedback message:\n[{record['sender_name']}]: {record['text']}\n\n"
                    f"Generate a draft reply for me."
                )
            else:
                sys_p = (
                    "You are replying on behalf of the user in a Telegram group. "
                    "Reply in the SAME LANGUAGE as the message that mentions the user. "
                    "Be concise, professional, and friendly."
                )
                usr_p = (
                    f"Group: {record['chat_title']}\n"
                    f"Recent context:\n{ctx_text}\n\n"
                    f"Latest message mentioning me:\n"
                    f"[{record['sender_name']}]: {record['text']}\n\n"
                    f"Generate a reply for me."
                )
            try:
                async with aiohttp.ClientSession() as session:
                    draft = await _llm_generate(
                        session, llm_key, llm_model, sys_p, usr_p)
            except Exception as e:
                logger.error(f"[tg_assistant] LLM draft: {e}")
                draft = f"[LLM error: {e}]"

        trigger_zh = []
        if has_m:
            trigger_zh.append("@提及")
        if has_b:
            trigger_zh.append("Bug/反馈")
        trigger_line = " · ".join(trigger_zh)

        mention_rec = {
            "mention_id": mid,
            "msg_id": record["msg_id"],
            "chat_id": record["chat_id"],
            "chat_title": record["chat_title"],
            "sender_id": record["sender_id"],
            "sender_username": record["sender_username"],
            "sender_name": record["sender_name"],
            "text": record["text"],
            "context": [m["text"] for m in ctx_msgs[-10:]],
            "draft_reply": draft,
            "status": "pending",
            "final_reply": "",
            "reply_timestamp": "",
            "timestamp": record["timestamp"],
            "triggers": triggers,
            "trigger_label": trigger_line,
        }
        self._pending[mid] = mention_rec
        _append_jsonl("mentions.jsonl", mention_rec)

        if bot_token and owner_id and aiohttp:
            dm = (
                f"🔔 DevRel 待处理 [{record['chat_title']}]\n"
                f"触发: {trigger_line}\n"
                f"From: {record['sender_name']} "
                f"(@{record['sender_username']})\n"
                f"Message: {record['text']}\n\n"
                f"📝 Draft reply:\n{draft}\n\n"
                f"Commands:\n"
                f"  /approve {mid}\n"
                f"  /edit {mid} <your text>\n"
                f"  /skip {mid}"
            )
            try:
                async with aiohttp.ClientSession() as session:
                    await _tg_send(session, bot_token, owner_id, dm)
            except Exception as e:
                logger.error(f"[tg_assistant] DM to owner: {e}")

        fs = self._cfg.get("feishu") or {}
        if fs.get("enabled") and fs.get("mentions_table_id"):
            try:
                await self._fs_sync_mention(mention_rec, fs)
            except Exception as e:
                logger.error(f"[tg_assistant] 飞书mention同步: {e}")

        if fs.get("enabled") and fs.get("review_table_id"):
            try:
                await self._fs_sync_review(mention_rec, fs)
            except Exception as e:
                logger.error(f"[tg_assistant] 飞书审核表同步: {e}")

    # ─────────────────────────────────────────────────────────
    #  Owner DM commands: /approve /edit /skip /pending /summary
    # ─────────────────────────────────────────────────────────

    async def _handle_command(self, text: str):
        cfg = self._cfg
        bot_token = (cfg.get("telegram_bot_token") or "").strip()
        owner_id  = str(cfg.get("owner_telegram_id", ""))
        if not bot_token or not aiohttp:
            return

        parts = text.strip().split(maxsplit=2)
        cmd = parts[0].lower() if parts else ""

        if cmd == "/approve" and len(parts) >= 2:
            await self._do_approve(parts[1], edited=None)
        elif cmd == "/edit" and len(parts) >= 3:
            await self._do_approve(parts[1], edited=parts[2])
        elif cmd == "/skip" and len(parts) >= 2:
            await self._do_skip(parts[1])
        elif cmd == "/pending":
            await self._do_list_pending()
        elif cmd == "/export":
            await self._do_export()
        elif cmd == "/summary":
            self._cfg = _load_config()
            if not self._cfg.get("daily_summary_enabled", True):
                async with aiohttp.ClientSession() as s:
                    await _tg_send(s, bot_token, owner_id,
                                   "日报功能已关闭（config 里 daily_summary_enabled=false）。"
                                   "不会往任何群发消息。")
                return
            await self._trigger_summary()
        else:
            async with aiohttp.ClientSession() as s:
                await _tg_send(s, bot_token, owner_id,
                               "Commands:\n"
                               "/approve <id> – send draft\n"
                               "/edit <id> <text> – edit & send\n"
                               "/skip <id> – skip\n"
                               "/pending – list pending\n"
                               "/export – 导出 pending JSON 文件\n"
                               "/summary – daily summary")

    async def _do_approve(self, mid: str, *, edited: str | None):
        cfg = self._cfg
        bot_token = (cfg.get("telegram_bot_token") or "").strip()
        owner_id  = str(cfg.get("owner_telegram_id", ""))

        rec = self._pending.get(mid)
        if not rec:
            async with aiohttp.ClientSession() as s:
                await _tg_send(s, bot_token, owner_id,
                               f"❌ {mid} not found or already handled.")
            return

        final = edited if edited else rec["draft_reply"]
        reply_to = int(rec["msg_id"]) if rec["msg_id"].isdigit() else None

        try:
            async with aiohttp.ClientSession() as s:
                await _tg_send(s, bot_token, rec["chat_id"], final,
                               reply_to=reply_to)

                rec["status"] = "sent"
                rec["final_reply"] = final
                rec["reply_timestamp"] = datetime.now(
                    timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
                _append_jsonl("replies.jsonl", rec)
                del self._pending[mid]

                await _tg_send(s, bot_token, owner_id,
                               f"✅ Sent to [{rec['chat_title']}]:\n{final}")

            fs = self._cfg.get("feishu") or {}
            if fs.get("enabled") and fs.get("replies_table_id"):
                try:
                    await self._fs_sync_reply(rec, fs)
                except Exception as e:
                    logger.error(f"[tg_assistant] 飞书回复同步: {e}")
        except Exception as e:
            logger.error(f"[tg_assistant] approve send: {e}")
            async with aiohttp.ClientSession() as s:
                await _tg_send(s, bot_token, owner_id,
                               f"❌ Send failed: {e}")

    async def _do_skip(self, mid: str):
        cfg = self._cfg
        bot_token = (cfg.get("telegram_bot_token") or "").strip()
        owner_id  = str(cfg.get("owner_telegram_id", ""))

        rec = self._pending.get(mid)
        if not rec:
            async with aiohttp.ClientSession() as s:
                await _tg_send(s, bot_token, owner_id,
                               f"❌ {mid} not found.")
            return

        rec["status"] = "skipped"
        _append_jsonl("mentions.jsonl", rec)
        del self._pending[mid]

        async with aiohttp.ClientSession() as s:
            await _tg_send(s, bot_token, owner_id,
                           f"⏭ Skipped {mid}")

    async def _do_list_pending(self):
        cfg = self._cfg
        bot_token = (cfg.get("telegram_bot_token") or "").strip()
        owner_id  = str(cfg.get("owner_telegram_id", ""))

        if not self._pending:
            async with aiohttp.ClientSession() as s:
                await _tg_send(s, bot_token, owner_id,
                               "No pending DevRel items.")
            return

        lines = ["📋 Pending DevRel (@提及 / Bug反馈):\n"]
        for mid, m in self._pending.items():
            lines.append(
                f"• {mid} — [{m['chat_title']}] "
                f"{m['sender_name']}: {m['text'][:60]}")
        async with aiohttp.ClientSession() as s:
            await _tg_send(s, bot_token, owner_id, "\n".join(lines))

    # ─────────────────────────────────────────────────────────
    #  /export → 把 pending 打包成 JSON 文件发到私聊
    # ─────────────────────────────────────────────────────────

    async def _do_export(self):
        cfg = self._cfg
        bot_token = (cfg.get("telegram_bot_token") or "").strip()
        owner_id  = str(cfg.get("owner_telegram_id", ""))

        if not self._pending:
            async with aiohttp.ClientSession() as s:
                await _tg_send(s, bot_token, owner_id,
                               "No pending items to export.")
            return

        records = list(self._pending.values())
        payload = json.dumps(records, ensure_ascii=False, indent=2)

        export_path = DATA_DIR / "export_pending.json"
        _ensure_data_dir()
        with export_path.open("w", encoding="utf-8") as f:
            f.write(payload)

        url = f"{TG_API}/bot{bot_token}/sendDocument"
        async with aiohttp.ClientSession() as s:
            form = aiohttp.FormData()
            form.add_field("chat_id", owner_id)
            form.add_field("caption",
                           f"📦 {len(records)} pending items\n"
                           f"下载后放到 tg-devrel-reviewer/ 目录，"
                           f"然后运行 python3 tg_review.py export")
            form.add_field("document",
                           payload.encode("utf-8"),
                           filename="pending_mentions.json",
                           content_type="application/json")
            async with s.post(url, data=form) as resp:
                body = await resp.json()
                if not body.get("ok"):
                    logger.error(f"[tg_assistant] export send: {body}")
                    await _tg_send(s, bot_token, owner_id,
                                   f"❌ Export failed: {body.get('description','')}")
                else:
                    await _tg_send(s, bot_token, owner_id,
                                   f"✅ Exported {len(records)} items.")

    # ─────────────────────────────────────────────────────────
    #  Daily summary (background loop + on-demand)
    # ─────────────────────────────────────────────────────────

    async def _daily_loop(self):
        while True:
            self._cfg = _load_config()
            if not self._cfg.get("daily_summary_enabled", True):
                logger.info("[tg_assistant] daily summary disabled, loop exit")
                return
            hour = int(self._cfg.get("daily_summary_hour_utc", 21))
            now = datetime.now(timezone.utc)
            target = now.replace(hour=hour, minute=0, second=0, microsecond=0)
            if now >= target:
                target += timedelta(days=1)
            wait = (target - now).total_seconds()
            logger.info(f"[tg_assistant] next summary: {target.isoformat()} "
                        f"(wait {wait:.0f}s)")
            await asyncio.sleep(wait)
            try:
                await self._trigger_summary()
            except Exception as e:
                logger.error(f"[tg_assistant] daily summary: {e}")

    async def _trigger_summary(self):
        cfg = self._cfg
        bot_token = (cfg.get("telegram_bot_token") or "").strip()
        owner_id  = str(cfg.get("owner_telegram_id", ""))
        llm_key   = (cfg.get("openrouter_api_key") or "").strip()
        llm_model = cfg.get("openrouter_model", "openai/gpt-4o-mini")

        if not all([bot_token, owner_id, llm_key, aiohttp]):
            logger.warning("[tg_assistant] summary: missing config")
            return

        cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)
                  ).strftime("%Y-%m-%d %H:%M:%S UTC")
        today = [m for m in _read_jsonl("messages.jsonl")
                 if m.get("timestamp", "") >= cutoff]

        if not today:
            async with aiohttp.ClientSession() as s:
                await _tg_send(s, bot_token, owner_id,
                               "📊 Daily summary: no messages in past 24 h.")
            return

        by_chat: dict[str, list] = defaultdict(list)
        for m in today:
            by_chat[m.get("chat_title", "?")].append(m)

        parts = []
        for title, msgs in by_chat.items():
            parts.append(f"\n## {title} ({len(msgs)} msgs)")
            for m in msgs[-50:]:
                parts.append(
                    f"[{m.get('sender_name', '?')}]: "
                    f"{m.get('text', '')[:200]}")

        sys_p = (
            "Summarize the user's Telegram group chats from the past 24h. "
            "Per group: key topics, decisions/action items, "
            "messages directed at the user. "
            "Use the same language as the majority in each group. Be concise."
        )
        usr_p = f"Messages:\n{''.join(parts)}"

        try:
            async with aiohttp.ClientSession() as s:
                summary = await _llm_generate(
                    s, llm_key, llm_model, sys_p, usr_p)

                header = (
                    f"📊 Daily Summary — "
                    f"{datetime.now(timezone.utc).strftime('%Y-%m-%d')}\n"
                    f"Total: {len(today)} msgs / {len(by_chat)} chats\n\n")
                full = header + summary

                for i in range(0, len(full), 4000):
                    await _tg_send(s, bot_token, owner_id, full[i:i + 4000])

                rec = {
                    "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                    "total_messages": len(today),
                    "total_chats": len(by_chat),
                    "summary": summary,
                    "timestamp": datetime.now(
                        timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
                }
                _append_jsonl("daily_summaries.jsonl", rec)

                fs = self._cfg.get("feishu") or {}
                if fs.get("enabled") and fs.get("summary_table_id"):
                    try:
                        await self._fs_sync_summary(rec, fs)
                    except Exception as e:
                        logger.error(f"[tg_assistant] 飞书日报同步: {e}")
        except Exception as e:
            logger.error(f"[tg_assistant] summary gen: {e}")

    # ─────────────────────────────────────────────────────────
    #  Feishu sync helpers
    # ─────────────────────────────────────────────────────────

    async def _fs_sync_message(self, rec: dict, fs: dict):
        async with aiohttp.ClientSession() as s:
            tk = await _get_feishu_token(s, fs["app_id"], fs["app_secret"])
            await _feishu_create_record(s, tk, fs["app_token"],
                                        fs["messages_table_id"], {
                "群组":     rec["chat_title"],
                "发送者":   rec["sender_name"],
                "用户名":   f"@{rec['sender_username']}" if rec["sender_username"] else "",
                "消息内容": rec["text"][:2000],
                "时间":     rec["timestamp"],
                "是否@我":  "是" if rec.get("is_mention") else "否",
                "DevRel触发": _feishu_trigger_display(rec.get("devrel_trigger")),
            })

    async def _fs_sync_mention(self, rec: dict, fs: dict):
        async with aiohttp.ClientSession() as s:
            tk = await _get_feishu_token(s, fs["app_id"], fs["app_secret"])
            await _feishu_create_record(s, tk, fs["app_token"],
                                        fs["mentions_table_id"], {
                "mention_id": rec["mention_id"],
                "群组":       rec["chat_title"],
                "发送者":     rec["sender_name"],
                "消息内容":   rec["text"][:2000],
                "AI草稿":     rec["draft_reply"][:2000],
                "状态":       rec["status"],
                "时间":       rec["timestamp"],
                "触发类型":   rec.get("trigger_label", ""),
            })

    async def _fs_sync_reply(self, rec: dict, fs: dict):
        async with aiohttp.ClientSession() as s:
            tk = await _get_feishu_token(s, fs["app_id"], fs["app_secret"])
            await _feishu_create_record(s, tk, fs["app_token"],
                                        fs["replies_table_id"], {
                "mention_id": rec["mention_id"],
                "群组":       rec["chat_title"],
                "原始消息":   rec["text"][:2000],
                "最终回复":   rec["final_reply"][:2000],
                "回复时间":   rec["reply_timestamp"],
            })

    async def _fs_sync_review(self, rec: dict, fs: dict):
        ctx = rec.get("context") or []
        ctx_str = "\n".join(ctx[-5:])
        async with aiohttp.ClientSession() as s:
            tk = await _get_feishu_token(s, fs["app_id"], fs["app_secret"])
            await _feishu_create_record(s, tk, fs["app_token"],
                                        fs["review_table_id"], {
                "mention_id":   rec["mention_id"],
                "群组":         rec["chat_title"],
                "触发类型":     rec.get("trigger_label", ""),
                "发送者":       rec["sender_name"],
                "用户名":       f"@{rec['sender_username']}" if rec.get("sender_username") else "",
                "原始消息":     rec["text"][:2000],
                "上下文":       ctx_str[:2000],
                "AI草稿":       (rec.get("draft_reply") or "")[:2000],
                "操作":         "",
                "最终回复":     "",
                "状态":         "pending",
                "chat_id":      rec["chat_id"],
                "msg_id":       rec["msg_id"],
                "时间":         rec["timestamp"],
            })

    async def _fs_sync_summary(self, rec: dict, fs: dict):
        async with aiohttp.ClientSession() as s:
            tk = await _get_feishu_token(s, fs["app_id"], fs["app_secret"])
            await _feishu_create_record(s, tk, fs["app_token"],
                                        fs["summary_table_id"], {
                "日期":     rec["date"],
                "消息总数": rec["total_messages"],
                "群组数":   rec["total_chats"],
                "总结":     rec["summary"][:5000],
            })


# ═══════════════════════════════════════════════════════════════════
#  Utility functions (outside class to keep it clean)
# ═══════════════════════════════════════════════════════════════════

def _attr(obj: Any, dotted_path: str, default: Any = "") -> Any:
    """Safely traverse nested attributes like 'chat.title'."""
    for part in dotted_path.split("."):
        obj = getattr(obj, part, None)
        if obj is None:
            return default
    return obj


def _tg_display_name(raw: Any, msg: Any) -> str:
    from_user = getattr(raw, "from_user", None) or getattr(raw, "from", None)
    if from_user:
        first = getattr(from_user, "first_name", "") or ""
        last = getattr(from_user, "last_name", "") or ""
        name = f"{first} {last}".strip()
        return name or getattr(from_user, "username", "") or ""
    if msg.sender:
        return getattr(msg.sender, "nickname", "") or ""
    return ""
