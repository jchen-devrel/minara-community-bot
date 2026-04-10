#!/usr/bin/env python3
"""
飞书 Bot 私信 Bug List

功能:
  1. 从飞书多维表格读取审核表中的待处理/bug 记录
  2. 格式化成消息，通过飞书机器人私信发给你
  3. 也支持读取本地 JSONL 数据

用法:
  # 第一次: 获取你的 open_id
  python3 feishu_bug_report.py whoami --email your@email.com

  # 发送 bug list 到飞书私信
  python3 feishu_bug_report.py send --open-id ou_xxxxx

  # 从本地 JSONL 读取（不走飞书表）
  python3 feishu_bug_report.py send --open-id ou_xxxxx --local

  # 只看不发（预览）
  python3 feishu_bug_report.py preview
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
BASE_DIR = SCRIPT_DIR.parent

CONFIG_CANDIDATES = [
    BASE_DIR / "astrbot_plugin_dc_assistant" / "config.json",
    BASE_DIR / "astrbot_plugin_tg_assistant" / "config.json",
    BASE_DIR / "astrbot_plugin_dc_user_collector" / "feishu_config.json",
]

FEISHU_TOKEN_URL = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
FEISHU_API = "https://open.feishu.cn/open-apis"

_token: str | None = None
_token_exp: float = 0.0


def load_config() -> dict:
    for p in CONFIG_CANDIDATES:
        if p.is_file():
            cfg = json.loads(p.read_text("utf-8"))
            if cfg.get("feishu"):
                return cfg
            if cfg.get("app_id"):
                return {"feishu": cfg}
    print("❌ 找不到 config.json (尝试过:", [str(p) for p in CONFIG_CANDIDATES], ")")
    sys.exit(1)


def _curl(method: str, url: str, headers: dict,
          body: dict | None = None) -> dict:
    cmd = ["curl", "-s", "-X", method, url]
    for k, v in headers.items():
        cmd += ["-H", f"{k}: {v}"]
    if body is not None:
        cmd += ["-d", json.dumps(body)]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if not p.stdout.strip():
        return {}
    try:
        return json.loads(p.stdout)
    except json.JSONDecodeError:
        print(f"  ⚠️ JSON parse error: {p.stdout[:200]}")
        return {}


def get_token(fs: dict) -> str:
    global _token, _token_exp
    now = time.time()
    if _token and now < _token_exp - 120:
        return _token
    r = _curl("POST", FEISHU_TOKEN_URL,
              {"Content-Type": "application/json"},
              {"app_id": fs["app_id"], "app_secret": fs["app_secret"]})
    if r.get("code") != 0:
        raise RuntimeError(f"飞书 token 失败: {r}")
    _token = r["tenant_access_token"]
    _token_exp = now + int(r.get("expire", 7200))
    return _token


# ═══════════════════════════════════════════════════════════════════
#  whoami: 查找你的 open_id
# ═══════════════════════════════════════════════════════════════════

def do_whoami(fs: dict, email: str):
    """通过邮箱查找飞书 open_id"""
    token = get_token(fs)

    url = f"{FEISHU_API}/contact/v3/users/batch_get_id"
    r = _curl("POST", url,
              {"Authorization": f"Bearer {token}",
               "Content-Type": "application/json"},
              {"emails": [email]})

    if r.get("code") != 0:
        print(f"❌ 查询失败: {r}")
        print("\n📌 手动获取 open_id 的方法:")
        print("  1. 打开飞书开发者后台 → 你的应用 → 事件与回调")
        print("  2. 或者在飞书中搜索你的机器人，发一条消息")
        print("  3. 在事件日志中能看到你的 open_id")
        print(f"\n  也可以用 Feishu 管理后台查看用户信息")
        return

    users = r.get("data", {}).get("user_list", [])
    if users:
        for u in users:
            print(f"✅ 找到用户:")
            print(f"   open_id: {u.get('open_id', '?')}")
            print(f"   user_id: {u.get('user_id', '?')}")
    else:
        print(f"❌ 未找到邮箱 {email} 对应的用户")
        print("  确保该邮箱在飞书组织内，且应用有 contact:user.email:readonly 权限")


# ═══════════════════════════════════════════════════════════════════
#  读取 Bug List（从飞书表 或 本地 JSONL）
# ═══════════════════════════════════════════════════════════════════

def fetch_bugs_from_feishu(fs: dict) -> list[dict]:
    """从飞书审核表读取 pending / bug 记录"""
    token = get_token(fs)
    app_token = fs["app_token"]

    table_id = fs.get("review_table_id")
    if not table_id:
        print("⚠️  review_table_id 未配置，尝试 TG assistant 的表...")
        table_id = "tblCswElh7Spobaa"

    all_records = []
    page_token = None

    while True:
        url = (f"{FEISHU_API}/bitable/v1/apps/{app_token}"
               f"/tables/{table_id}/records?page_size=100")
        if page_token:
            url += f"&page_token={page_token}"

        r = _curl("GET", url, {"Authorization": f"Bearer {token}"})
        if r.get("code") != 0:
            raise RuntimeError(f"飞书读取失败: {r}")

        items = r.get("data", {}).get("items", [])
        all_records.extend(items)

        if not r.get("data", {}).get("has_more"):
            break
        page_token = r["data"].get("page_token")

    bugs = []
    for item in all_records:
        fields = item.get("fields", {})
        status = _extract_text(fields.get("状态", ""))
        trigger = _extract_text(fields.get("触发类型", ""))

        if status.lower() in ("sent", "skipped"):
            continue

        bugs.append({
            "record_id": item.get("record_id", ""),
            "mention_id": _extract_text(fields.get("mention_id", "")),
            "channel": _extract_text(fields.get("频道", "") or fields.get("群组", "")),
            "trigger": trigger,
            "sender": _extract_text(fields.get("发送者", "")),
            "message": _extract_text(fields.get("原始消息", "")),
            "draft": _extract_text(fields.get("AI草稿", "")),
            "status": status,
            "time": _extract_text(fields.get("时间", "")),
        })

    return bugs


def fetch_bugs_from_local() -> list[dict]:
    """从本地 mentions.jsonl 读取 pending 记录"""
    bugs = []
    for data_dir in [
        BASE_DIR / "astrbot_plugin_dc_assistant" / "data",
        BASE_DIR / "astrbot_plugin_tg_assistant" / "data",
    ]:
        mentions_path = data_dir / "mentions.jsonl"
        if not mentions_path.is_file():
            continue
        with mentions_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    m = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if m.get("status") in ("sent", "skipped"):
                    continue
                source = "DC" if "dc_" in (m.get("mention_id") or "") else "TG"
                bugs.append({
                    "mention_id": m.get("mention_id", ""),
                    "channel": m.get("channel_name", "") or m.get("chat_title", ""),
                    "trigger": m.get("trigger_label", ""),
                    "sender": m.get("author_name", "") or m.get("sender_name", ""),
                    "message": m.get("text", ""),
                    "draft": m.get("draft_reply", ""),
                    "status": m.get("status", "pending"),
                    "time": m.get("timestamp", ""),
                    "source": source,
                })
    return bugs


def _extract_text(val) -> str:
    """飞书字段值可能是 str, list[{text:...}], 或其他格式"""
    if isinstance(val, str):
        return val
    if isinstance(val, list):
        return "".join(
            seg.get("text", "") if isinstance(seg, dict) else str(seg)
            for seg in val)
    if isinstance(val, (int, float)):
        return str(val)
    return str(val) if val else ""


# ═══════════════════════════════════════════════════════════════════
#  格式化消息
# ═══════════════════════════════════════════════════════════════════

def format_bug_list(bugs: list[dict]) -> str:
    if not bugs:
        return "✅ 当前没有待处理的 Bug / 提及"

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [f"🐛 Bug & 待审核列表 ({len(bugs)} 条)  —  {now}\n"]

    for i, b in enumerate(bugs, 1):
        source = b.get("source", "")
        src_tag = f"[{source}] " if source else ""
        lines.append(f"{'─' * 40}")
        lines.append(f"#{i}  {src_tag}{b.get('trigger', '?')}")
        lines.append(f"频道: {b.get('channel', '?')}")
        lines.append(f"发送者: {b.get('sender', '?')}")
        lines.append(f"时间: {b.get('time', '?')}")
        lines.append(f"消息: {b.get('message', '')[:200]}")
        draft = b.get("draft", "")
        if draft:
            lines.append(f"AI草稿: {draft[:200]}")
        lines.append(f"状态: {b.get('status', '?')}")
        lines.append("")

    lines.append(f"{'─' * 40}")
    lines.append(f"总计 {len(bugs)} 条待处理")
    return "\n".join(lines)


def format_bug_list_rich(bugs: list[dict]) -> dict:
    """生成飞书富文本消息 (post 格式)"""
    if not bugs:
        return {
            "msg_type": "text",
            "content": json.dumps({"text": "✅ 当前没有待处理的 Bug / 提及"}),
        }

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    content_lines = []
    for i, b in enumerate(bugs, 1):
        source = b.get("source", "")
        src_tag = f"[{source}] " if source else ""
        line = [
            {"tag": "text", "text": f"#{i} {src_tag}"},
            {"tag": "text", "text": f"{b.get('trigger', '')} "},
            {"tag": "text", "text": f"| {b.get('channel', '?')} | "},
            {"tag": "text", "text": f"{b.get('sender', '?')}\n"},
            {"tag": "text", "text": f"  {b.get('message', '')[:150]}\n"},
        ]
        draft = b.get("draft", "")
        if draft:
            line.append({"tag": "text", "text": f"  💬 {draft[:100]}\n"})
        content_lines.append(line)

    post_content = {
        "zh_cn": {
            "title": f"🐛 Bug & 待审核 ({len(bugs)} 条) — {now}",
            "content": content_lines,
        }
    }

    return {
        "msg_type": "post",
        "content": json.dumps(post_content, ensure_ascii=False),
    }


# ═══════════════════════════════════════════════════════════════════
#  发送飞书私信
# ═══════════════════════════════════════════════════════════════════

def send_feishu_dm(fs: dict, open_id: str, bugs: list[dict]):
    """通过飞书机器人发送私信"""
    token = get_token(fs)

    if len(bugs) <= 10:
        msg = format_bug_list_rich(bugs)
    else:
        msg = {
            "msg_type": "text",
            "content": json.dumps(
                {"text": format_bug_list(bugs)}, ensure_ascii=False),
        }

    url = f"{FEISHU_API}/im/v1/messages?receive_id_type=open_id"
    payload = {
        "receive_id": open_id,
        **msg,
    }

    r = _curl("POST", url,
              {"Authorization": f"Bearer {token}",
               "Content-Type": "application/json"},
              payload)

    if r.get("code") == 0:
        print(f"✅ 已发送 {len(bugs)} 条 bug/提及到飞书私信")
    else:
        code = r.get("code")
        err_msg = r.get("msg", "")
        print(f"❌ 发送失败: code={code} msg={err_msg}")

        if code == 230001:
            print("\n📌 可能原因: 机器人没有与你建立会话")
            print("   解决: 在飞书中搜索你的机器人名称，点击进去发一条消息")
        elif code == 99991663:
            print("\n📌 可能原因: 应用没有 im:message 或 im:message:send_as_bot 权限")
            print("   解决: 飞书开发者后台 → 应用 → 权限管理 → 添加")
        elif code == 10003:
            print("\n📌 open_id 不正确，用 whoami 命令获取")


# ═══════════════════════════════════════════════════════════════════
#  Main
# ═══════════════════════════════════════════════════════════════════

def main():
    p = argparse.ArgumentParser(description="飞书 Bot 私信 Bug List")
    sub = p.add_subparsers(dest="cmd")

    w = sub.add_parser("whoami", help="通过邮箱查找你的 open_id")
    w.add_argument("--email", required=True, help="你的飞书邮箱")

    s = sub.add_parser("send", help="发送 bug list 到飞书私信")
    s.add_argument("--open-id", required=True, help="你的飞书 open_id")
    s.add_argument("--local", action="store_true",
                   help="从本地 JSONL 读取（不读飞书表）")

    sub.add_parser("preview", help="预览 bug list（不发送）")

    args = p.parse_args()
    if not args.cmd:
        p.print_help(); return

    cfg = load_config()
    fs = cfg.get("feishu") or cfg

    if args.cmd == "whoami":
        do_whoami(fs, args.email)

    elif args.cmd == "preview":
        print("📊 从飞书表读取...")
        try:
            bugs = fetch_bugs_from_feishu(fs)
        except Exception as e:
            print(f"  飞书表读取失败 ({e})，切换本地 JSONL...")
            bugs = fetch_bugs_from_local()
        print(format_bug_list(bugs))

    elif args.cmd == "send":
        if args.local:
            bugs = fetch_bugs_from_local()
        else:
            try:
                bugs = fetch_bugs_from_feishu(fs)
            except Exception as e:
                print(f"  飞书表读取失败 ({e})，切换本地...")
                bugs = fetch_bugs_from_local()
        print(format_bug_list(bugs))
        print()
        send_feishu_dm(fs, args.open_id, bugs)


if __name__ == "__main__":
    main()
