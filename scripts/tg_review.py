#!/usr/bin/env python3
"""
本地审核脚本：批量查看 / 编辑 / 批量发送 TG mentions

用法:
  python3 scripts/tg_review.py                # 交互式审核
  python3 scripts/tg_review.py --list         # 仅列出待审核
  python3 scripts/tg_review.py --approve-all  # 全部用 AI 草稿发送
  python3 scripts/tg_review.py --sync-feishu  # 仅同步数据到飞书（不发送）
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parent.parent / "astrbot_plugin_tg_assistant"
CONFIG_PATH = PLUGIN_DIR / "config.json"
DATA_DIR = PLUGIN_DIR / "data"

MENTIONS_FILE = DATA_DIR / "mentions.jsonl"
REPLIES_FILE  = DATA_DIR / "replies.jsonl"
MESSAGES_FILE = DATA_DIR / "messages.jsonl"

TG_API = "https://api.telegram.org"
FEISHU_TOKEN_URL = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"

_feishu_token: str | None = None
_feishu_token_exp: float = 0.0


# ═══════════════════════════════════════════════════════════════════
#  Config / Data helpers
# ═══════════════════════════════════════════════════════════════════

def load_config() -> dict:
    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


def read_jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    out = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return out


def append_jsonl(path: Path, rec: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def get_pending() -> list[dict]:
    all_mentions = read_jsonl(MENTIONS_FILE)
    replied_ids = {r["mention_id"] for r in read_jsonl(REPLIES_FILE)
                   if r.get("status") == "sent"}
    skipped_ids = set()
    for m in all_mentions:
        if m.get("status") == "skipped":
            skipped_ids.add(m["mention_id"])

    seen = set()
    pending = []
    for m in reversed(all_mentions):
        mid = m["mention_id"]
        if mid in seen or mid in replied_ids or mid in skipped_ids:
            continue
        if m.get("status") == "pending":
            seen.add(mid)
            pending.append(m)
    return list(reversed(pending))


# ═══════════════════════════════════════════════════════════════════
#  Telegram API (via curl for reliability)
# ═══════════════════════════════════════════════════════════════════

def tg_send(bot_token: str, chat_id: str, text: str,
            reply_to: int | None = None) -> bool:
    payload: dict = {"chat_id": chat_id, "text": text}
    if reply_to:
        payload["reply_parameters"] = {"message_id": reply_to}
    url = f"{TG_API}/bot{bot_token}/sendMessage"
    p = subprocess.run(
        ["curl", "-s", "-X", "POST", url,
         "-H", "Content-Type: application/json",
         "-d", json.dumps(payload)],
        capture_output=True, text=True, timeout=15,
    )
    try:
        resp = json.loads(p.stdout)
        if not resp.get("ok"):
            print(f"  ❌ TG error: {resp}", file=sys.stderr)
            return False
        return True
    except (json.JSONDecodeError, KeyError):
        print(f"  ❌ TG raw: {p.stdout[:300]}", file=sys.stderr)
        return False


# ═══════════════════════════════════════════════════════════════════
#  Feishu helpers
# ═══════════════════════════════════════════════════════════════════

def _curl_post(url: str, headers: dict, body: dict | None = None) -> dict:
    cmd = ["curl", "-s", "-X", "POST", url]
    for k, v in headers.items():
        cmd += ["-H", f"{k}: {v}"]
    if body:
        cmd += ["-d", json.dumps(body)]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    return json.loads(p.stdout) if p.stdout.strip() else {}


def feishu_token(app_id: str, app_secret: str) -> str:
    global _feishu_token, _feishu_token_exp
    now = time.time()
    if _feishu_token and now < _feishu_token_exp - 120:
        return _feishu_token
    r = _curl_post(FEISHU_TOKEN_URL,
                   {"Content-Type": "application/json"},
                   {"app_id": app_id, "app_secret": app_secret})
    if r.get("code") != 0:
        raise RuntimeError(f"飞书 token 失败: {r}")
    _feishu_token = r["tenant_access_token"]
    _feishu_token_exp = now + int(r.get("expire", 7200))
    return _feishu_token


def feishu_create(token: str, app_token: str, table_id: str, fields: dict):
    url = (f"https://open.feishu.cn/open-apis/bitable/v1"
           f"/apps/{app_token}/tables/{table_id}/records")
    r = _curl_post(url,
                   {"Authorization": f"Bearer {token}",
                    "Content-Type": "application/json"},
                   {"fields": fields})
    if r.get("code") != 0:
        raise RuntimeError(f"飞书写入失败: {r}")


# ═══════════════════════════════════════════════════════════════════
#  Feishu batch sync
# ═══════════════════════════════════════════════════════════════════

def sync_to_feishu(cfg: dict):
    fs = cfg.get("feishu") or {}
    if not fs.get("enabled"):
        print("⚠️  飞书未启用 (feishu.enabled=false)")
        return

    app_id = fs["app_id"]
    app_secret = fs["app_secret"]
    app_token = fs["app_token"]
    tk = feishu_token(app_id, app_secret)

    # messages
    msg_tid = fs.get("messages_table_id")
    if msg_tid:
        msgs = read_jsonl(MESSAGES_FILE)
        print(f"📤 同步 {len(msgs)} 条消息到飞书 ...")
        for m in msgs:
            trigger_raw = m.get("devrel_trigger") or ""
            parts = trigger_raw.split("+")
            tr_map = {"mention": "@提及", "bug": "Bug反馈"}
            trigger_display = "+".join(tr_map.get(p, p) for p in parts) if trigger_raw else "无"
            try:
                feishu_create(tk, app_token, msg_tid, {
                    "群组":       m.get("chat_title", ""),
                    "发送者":     m.get("sender_name", ""),
                    "用户名":     f"@{m['sender_username']}" if m.get("sender_username") else "",
                    "消息内容":   (m.get("text") or "")[:2000],
                    "时间":       m.get("timestamp", ""),
                    "是否@我":    "是" if m.get("is_mention") else "否",
                    "DevRel触发": trigger_display,
                })
            except Exception as e:
                print(f"  ⚠️ 消息写入失败: {e}")
        print(f"  ✅ 消息同步完成")

    # mentions
    mention_tid = fs.get("mentions_table_id")
    if mention_tid:
        mentions = read_jsonl(MENTIONS_FILE)
        print(f"📤 同步 {len(mentions)} 条提及到飞书 ...")
        for m in mentions:
            try:
                feishu_create(tk, app_token, mention_tid, {
                    "mention_id": m.get("mention_id", ""),
                    "群组":       m.get("chat_title", ""),
                    "发送者":     m.get("sender_name", ""),
                    "消息内容":   (m.get("text") or "")[:2000],
                    "AI草稿":     (m.get("draft_reply") or "")[:2000],
                    "状态":       m.get("status", ""),
                    "时间":       m.get("timestamp", ""),
                    "触发类型":   m.get("trigger_label", ""),
                })
            except Exception as e:
                print(f"  ⚠️ 提及写入失败: {e}")
        print(f"  ✅ 提及同步完成")

    # replies
    reply_tid = fs.get("replies_table_id")
    if reply_tid:
        replies = read_jsonl(REPLIES_FILE)
        print(f"📤 同步 {len(replies)} 条回复到飞书 ...")
        for r in replies:
            try:
                feishu_create(tk, app_token, reply_tid, {
                    "mention_id": r.get("mention_id", ""),
                    "群组":       r.get("chat_title", ""),
                    "原始消息":   (r.get("text") or "")[:2000],
                    "最终回复":   (r.get("final_reply") or "")[:2000],
                    "回复时间":   r.get("reply_timestamp", ""),
                })
            except Exception as e:
                print(f"  ⚠️ 回复写入失败: {e}")
        print(f"  ✅ 回复同步完成")


# ═══════════════════════════════════════════════════════════════════
#  Display
# ═══════════════════════════════════════════════════════════════════

DIVIDER = "─" * 60

def print_mention(i: int, m: dict):
    print(f"\n{DIVIDER}")
    print(f"  [{i+1}] {m['mention_id']}")
    print(f"  群组: {m['chat_title']}")
    print(f"  触发: {m.get('trigger_label', '?')}")
    print(f"  发送者: {m['sender_name']} (@{m['sender_username']})")
    print(f"  时间: {m['timestamp']}")
    print(f"  消息: {m['text']}")
    print()
    ctx = m.get("context") or []
    if ctx:
        print("  📎 上下文 (最近几条):")
        for c in ctx[-5:]:
            print(f"    > {c[:120]}")
        print()
    print(f"  📝 AI 草稿:")
    print(f"    {m['draft_reply']}")
    print(DIVIDER)


# ═══════════════════════════════════════════════════════════════════
#  Interactive review
# ═══════════════════════════════════════════════════════════════════

def interactive_review(cfg: dict, pending: list[dict]):
    bot_token = cfg.get("telegram_bot_token", "").strip()
    if not bot_token:
        print("❌ config.json 里没有 telegram_bot_token")
        return

    approved = []
    skipped = []

    for i, m in enumerate(pending):
        print_mention(i, m)
        while True:
            choice = input(
                f"\n  [{i+1}/{len(pending)}] "
                f"(a)pprove / (e)dit / (s)kip / (q)uit > ").strip().lower()

            if choice in ("a", "approve"):
                reply_to = int(m["msg_id"]) if m["msg_id"].isdigit() else None
                ok = tg_send(bot_token, m["chat_id"], m["draft_reply"],
                             reply_to=reply_to)
                if ok:
                    m["status"] = "sent"
                    m["final_reply"] = m["draft_reply"]
                    m["reply_timestamp"] = datetime.now(
                        timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
                    append_jsonl(REPLIES_FILE, m)
                    approved.append(m["mention_id"])
                    print(f"  ✅ 已发送到 [{m['chat_title']}]")
                else:
                    print("  ❌ 发送失败，请重试")
                    continue
                break

            elif choice in ("e", "edit"):
                new_text = input("  输入修改后的回复:\n  > ").strip()
                if not new_text:
                    print("  ⚠️ 内容为空，重试")
                    continue
                reply_to = int(m["msg_id"]) if m["msg_id"].isdigit() else None
                ok = tg_send(bot_token, m["chat_id"], new_text,
                             reply_to=reply_to)
                if ok:
                    m["status"] = "sent"
                    m["final_reply"] = new_text
                    m["reply_timestamp"] = datetime.now(
                        timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
                    append_jsonl(REPLIES_FILE, m)
                    approved.append(m["mention_id"])
                    print(f"  ✅ 已发送到 [{m['chat_title']}]")
                else:
                    print("  ❌ 发送失败，请重试")
                    continue
                break

            elif choice in ("s", "skip"):
                m["status"] = "skipped"
                append_jsonl(MENTIONS_FILE, m)
                skipped.append(m["mention_id"])
                print("  ⏭ 已跳过")
                break

            elif choice in ("q", "quit"):
                print(f"\n📊 本次: 发送 {len(approved)}, 跳过 {len(skipped)}, "
                      f"剩余 {len(pending) - i - len(approved) - len(skipped)}")
                return

            else:
                print("  输入 a / e / s / q")

    print(f"\n📊 全部处理完: 发送 {len(approved)}, 跳过 {len(skipped)}")


def batch_approve(cfg: dict, pending: list[dict]):
    bot_token = cfg.get("telegram_bot_token", "").strip()
    if not bot_token:
        print("❌ config.json 里没有 telegram_bot_token"); return

    print(f"🚀 批量发送 {len(pending)} 条 AI 草稿 ...\n")
    ok_count = 0
    for m in pending:
        reply_to = int(m["msg_id"]) if m["msg_id"].isdigit() else None
        ok = tg_send(bot_token, m["chat_id"], m["draft_reply"],
                     reply_to=reply_to)
        if ok:
            m["status"] = "sent"
            m["final_reply"] = m["draft_reply"]
            m["reply_timestamp"] = datetime.now(
                timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
            append_jsonl(REPLIES_FILE, m)
            ok_count += 1
            print(f"  ✅ [{m['chat_title']}] → {m['draft_reply'][:60]}")
        else:
            print(f"  ❌ [{m['chat_title']}] 发送失败")
        time.sleep(0.5)

    print(f"\n📊 批量发送完成: {ok_count}/{len(pending)} 成功")


# ═══════════════════════════════════════════════════════════════════
#  Main
# ═══════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="TG Assistant 本地审核工具")
    parser.add_argument("--list", action="store_true",
                        help="仅列出待审核项")
    parser.add_argument("--approve-all", action="store_true",
                        help="批量发送所有 AI 草稿")
    parser.add_argument("--sync-feishu", action="store_true",
                        help="同步本地数据到飞书")
    args = parser.parse_args()

    if not CONFIG_PATH.is_file():
        print(f"❌ 找不到 {CONFIG_PATH}"); sys.exit(1)
    cfg = load_config()

    if args.sync_feishu:
        sync_to_feishu(cfg)
        return

    pending = get_pending()
    print(f"\n📋 待审核: {len(pending)} 条")

    if not pending:
        print("没有待审核的消息。")
        if args.sync_feishu:
            sync_to_feishu(cfg)
        return

    if args.list:
        for i, m in enumerate(pending):
            print_mention(i, m)
        return

    if args.approve_all:
        confirm = input(f"确认批量发送 {len(pending)} 条？(y/N) > ").strip().lower()
        if confirm != "y":
            print("已取消"); return
        batch_approve(cfg, pending)
    else:
        interactive_review(cfg, pending)

    # 审核完后提示是否同步飞书
    fs = cfg.get("feishu") or {}
    if fs.get("enabled"):
        sync = input("\n是否同步到飞书？(y/N) > ").strip().lower()
        if sync == "y":
            sync_to_feishu(cfg)


if __name__ == "__main__":
    main()
