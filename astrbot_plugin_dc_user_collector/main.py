import asyncio
import json
import time
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


TARGET_CHANNEL_NAME = "feedback"
TARGET_USERNAME_KEYWORD = "hazel"

PLUGIN_DIR = Path(__file__).resolve().parent
FEISHU_CONFIG_PATH = PLUGIN_DIR / "feishu_config.json"

FEISHU_TOKEN_URL = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"

_token_value: str | None = None
_token_expires_at: float = 0.0

_seen_msg_ids: set[str] = set()


def _load_feishu_config() -> dict[str, Any] | None:
    if not FEISHU_CONFIG_PATH.is_file():
        return None
    try:
        with FEISHU_CONFIG_PATH.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        logger.error(f"[dc_user_collector] 读取 feishu_config.json 失败: {e}")
        return None


async def _get_token(session: "aiohttp.ClientSession", app_id: str, app_secret: str) -> str:
    global _token_value, _token_expires_at
    now = time.time()
    if _token_value and now < _token_expires_at - 120:
        return _token_value

    async with session.post(
        FEISHU_TOKEN_URL,
        json={"app_id": app_id, "app_secret": app_secret},
        headers={"Content-Type": "application/json; charset=utf-8"},
    ) as resp:
        body = await resp.json()
    if body.get("code") != 0:
        raise RuntimeError(f"飞书 token 失败: {body}")
    _token_value = body["tenant_access_token"]
    _token_expires_at = now + int(body.get("expire", 7200))
    return _token_value


async def _create_record(
    session: "aiohttp.ClientSession", token: str,
    app_token: str, table_id: str, fields: dict,
) -> None:
    url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records"
    async with session.post(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
        },
        json={"fields": fields},
    ) as resp:
        body = await resp.json()
    if body.get("code") != 0:
        raise RuntimeError(f"飞书写入失败: {body}")


async def _discord_reply(session: "aiohttp.ClientSession", bot_token: str, channel_id: str, text: str) -> None:
    url = f"https://discord.com/api/v10/channels/{channel_id}/messages"
    async with session.post(
        url,
        headers={
            "Authorization": f"Bot {bot_token}",
            "Content-Type": "application/json",
        },
        json={"content": text},
    ) as resp:
        if resp.status >= 400:
            body = await resp.text()
            logger.error(f"[dc_user_collector] Discord 回复失败: {resp.status} {body}")


class DcUserCollector(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        self._lock = asyncio.Lock()
        logger.info("[dc_user_collector] 插件已加载")

    @filter.event_message_type(filter.EventMessageType.ALL, priority=100)
    async def collect_message(self, event: AstrMessageEvent):
        msg = event.message_obj
        raw = msg.raw_message
        sender = msg.sender

        channel = getattr(raw, "channel", None)
        author = getattr(raw, "author", None)

        channel_name = getattr(channel, "name", "") or ""
        username = getattr(author, "name", "") or ""
        display_name = getattr(author, "display_name", "") or getattr(author, "global_name", "") or ""
        is_bot = getattr(author, "bot", False)

        if not channel_name and msg.group_id:
            channel_name = msg.group_id
        if not username and sender:
            username = getattr(sender, "nickname", "") or ""

        if TARGET_CHANNEL_NAME.lower() not in channel_name.lower():
            return

        if is_bot:
            return

        text_for_match = f"{username} {display_name}".lower()
        if TARGET_USERNAME_KEYWORD.lower() not in text_for_match:
            return

        mid = str(getattr(raw, "id", "") or msg.message_id or "")
        if mid and mid in _seen_msg_ids:
            return
        if mid:
            _seen_msg_ids.add(mid)
            if len(_seen_msg_ids) > 5000:
                _seen_msg_ids.clear()

        content = msg.message_str or ""
        text_for_feishu = (
            f"[{display_name or username}] {content}\n"
            f"频道: {channel_name} | 时间: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}"
        )

        logger.info(f"[dc_user_collector] 匹配成功，准备写入飞书: {content[:60]}")

        cfg = _load_feishu_config()
        if not cfg or not cfg.get("enabled") or aiohttp is None:
            logger.info("[dc_user_collector] 飞书未启用或缺少 aiohttp，跳过")
            return

        app_id = (cfg.get("app_id") or "").strip()
        app_secret = (cfg.get("app_secret") or "").strip()
        app_token = (cfg.get("app_token") or "").strip()
        table_id = (cfg.get("table_id") or "").strip()

        if not all([app_id, app_secret, app_token, table_id]):
            logger.error("[dc_user_collector] feishu_config.json 配置不完整")
            return

        fields = {"文本": text_for_feishu}

        discord_bot_token = (cfg.get("discord_bot_token") or "").strip()
        channel_id = str(getattr(channel, "id", ""))

        try:
            async with self._lock:
                async with aiohttp.ClientSession() as session:
                    token = await _get_token(session, app_id, app_secret)
                    await _create_record(session, token, app_token, table_id, fields)
                    if discord_bot_token and channel_id:
                        reply = f"✅ Thanks {display_name or username}, your feedback has been recorded!"
                        await _discord_reply(session, discord_bot_token, channel_id, reply)
            logger.info(f"[dc_user_collector] 写入飞书+回复成功: {content[:60]}")
        except Exception as e:
            logger.error(f"[dc_user_collector] 异常: {e}")
