#!/usr/bin/env python3
"""
批处理脚本：
  1. 拉取飞书表 A（Discord 采集表）的消息
  2. 用 LLM（OpenRouter）分析每条消息
  3. 将原始消息 + 分析结果写入飞书表 B
  4. 通过 Discord bot 将分析结果回复到对应 channel

用法:
  # 设置环境变量（或写入 .env）
  export OPENROUTER_API_KEY="sk-or-..."
  export DISCORD_BOT_TOKEN="..."

  python3 scripts/process_feedback.py
"""
from __future__ import annotations

import json
import os
import ssl
import sys
import time
import urllib.request
from pathlib import Path

FEISHU_CFG = Path(__file__).resolve().parent.parent / "astrbot_plugin_dc_user_collector" / "feishu_config.json"

TABLE_A_ID = "tbltXccis1dknilp"
TABLE_B_ID = "tblyBf1Wfzh1fDZ2"

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_MODEL = "openai/gpt-4o-mini"

ANALYSIS_PROMPT = """\
你是一个产品反馈分析助手。请对以下用户反馈消息进行分析，输出一个简短的 JSON 对象（不要 markdown 包裹），包含：
- "category": 分类（bug / feature_request / question / praise / other）
- "priority": 优先级（high / medium / low）
- "summary": 一句话中文摘要（不超过50字）
- "suggested_reply": 建议的中文回复（不超过100字，语气友好专业）

用户消息：
{content}
"""


def _ssl_ctx() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _http(method: str, url: str, headers: dict, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, context=_ssl_ctx()) as resp:
        return json.loads(resp.read())


# ── Feishu helpers ──

def feishu_token(app_id: str, app_secret: str) -> str:
    r = _http("POST", "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
              {"Content-Type": "application/json"}, {"app_id": app_id, "app_secret": app_secret})
    if r.get("code") != 0:
        raise RuntimeError(f"飞书 token 失败: {r}")
    return r["tenant_access_token"]


def feishu_list_records(token: str, app_token: str, table_id: str) -> list[dict]:
    url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records?page_size=100"
    r = _http("GET", url, {"Authorization": f"Bearer {token}"})
    if r.get("code") != 0:
        raise RuntimeError(f"飞书读取失败: {r}")
    return r.get("data", {}).get("items", [])


def feishu_create_record(token: str, app_token: str, table_id: str, fields: dict) -> None:
    url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records"
    r = _http("POST", url, {"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
              {"fields": fields})
    if r.get("code") != 0:
        raise RuntimeError(f"飞书写入失败: {r}")


# ── LLM helper ──

def llm_analyze(content: str, api_key: str) -> dict:
    prompt = ANALYSIS_PROMPT.format(content=content)
    body = {
        "model": OPENROUTER_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.3,
    }
    r = _http("POST", OPENROUTER_URL,
              {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}, body)
    text = r["choices"][0]["message"]["content"].strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    return json.loads(text)


# ── Discord helper ──

def discord_send(bot_token: str, channel_id: str, message: str) -> None:
    url = f"https://discord.com/api/v10/channels/{channel_id}/messages"
    _http("POST", url, {"Authorization": f"Bot {bot_token}", "Content-Type": "application/json"},
          {"content": message})


# ── Main ──

def main() -> None:
    openrouter_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    discord_token = os.environ.get("DISCORD_BOT_TOKEN", "").strip()

    if not openrouter_key:
        print("请设置 OPENROUTER_API_KEY 环境变量"); sys.exit(1)
    if not discord_token:
        print("请设置 DISCORD_BOT_TOKEN 环境变量"); sys.exit(1)

    if not FEISHU_CFG.is_file():
        print(f"找不到 {FEISHU_CFG}"); sys.exit(1)
    with open(FEISHU_CFG, encoding="utf-8") as f:
        cfg = json.load(f)

    app_id = cfg["app_id"]
    app_secret = cfg["app_secret"]
    app_token = cfg["app_token"]

    print("1/4 获取飞书 token...")
    token = feishu_token(app_id, app_secret)

    print("2/4 拉取表 A 数据...")
    records = feishu_list_records(token, app_token, TABLE_A_ID)
    if not records:
        print("表 A 没有数据"); return

    print(f"   共 {len(records)} 条记录")

    for i, rec in enumerate(records):
        fields = rec.get("fields", {})
        raw_text = ""
        text_field = fields.get("文本")
        if isinstance(text_field, list):
            raw_text = "".join(seg.get("text", "") for seg in text_field if isinstance(seg, dict))
        elif isinstance(text_field, str):
            raw_text = text_field
        else:
            raw_text = str(text_field or "")

        if not raw_text.strip():
            print(f"   [{i+1}/{len(records)}] 跳过空记录")
            continue

        # Parse user/channel/content from the formatted text
        # Format: "[Hazel] 消息内容\n频道: 🔹│feedback | 时间: 2026-04-03 ..."
        user = ""
        content = raw_text
        channel_name = ""
        timestamp = ""

        if raw_text.startswith("[") and "]" in raw_text:
            bracket_end = raw_text.index("]")
            user = raw_text[1:bracket_end]
            rest = raw_text[bracket_end + 2:]
            if "\n" in rest:
                content, meta = rest.split("\n", 1)
                for part in meta.split("|"):
                    part = part.strip()
                    if part.startswith("频道:"):
                        channel_name = part[3:].strip()
                    elif part.startswith("时间:"):
                        timestamp = part[3:].strip()
            else:
                content = rest

        print(f"\n   [{i+1}/{len(records)}] 分析: {content[:50]}...")

        # Step 3: LLM analysis
        try:
            analysis = llm_analyze(content, openrouter_key)
        except Exception as e:
            print(f"   LLM 分析失败: {e}")
            analysis = {"category": "unknown", "priority": "low", "summary": "分析失败", "suggested_reply": ""}

        analysis_text = (
            f"分类: {analysis.get('category', '?')}\n"
            f"优先级: {analysis.get('priority', '?')}\n"
            f"摘要: {analysis.get('summary', '?')}"
        )
        suggested_reply = analysis.get("suggested_reply", "")

        print(f"   结果: {analysis.get('category')} / {analysis.get('priority')} — {analysis.get('summary', '')[:40]}")

        # Step 3: Write to table B
        b_fields = {
            "原始消息": content,
            "用户": user,
            "频道": channel_name,
            "时间": timestamp,
            "LLM分析": analysis_text,
        }
        try:
            feishu_create_record(token, app_token, TABLE_B_ID, b_fields)
            print(f"   写入飞书表 B 成功")
        except Exception as e:
            print(f"   写入飞书表 B 失败: {e}")

        # Step 4: Discord reply
        if suggested_reply and discord_token:
            # We need the channel_id to reply. Extract from the original record if available.
            # For now, use a configurable default channel ID.
            discord_channel_id = os.environ.get("DISCORD_CHANNEL_ID", "").strip()
            if discord_channel_id:
                reply_msg = f"📋 **反馈分析** (来自 {user}):\n{analysis_text}\n\n💬 {suggested_reply}"
                try:
                    discord_send(discord_token, discord_channel_id, reply_msg)
                    print(f"   Discord 回复成功")
                except Exception as e:
                    print(f"   Discord 回复失败: {e}")
            else:
                print(f"   跳过 Discord 回复（未设置 DISCORD_CHANNEL_ID）")

        time.sleep(1)

    print("\n完成！")


if __name__ == "__main__":
    main()
