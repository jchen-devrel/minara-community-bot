#!/usr/bin/env python3
"""
Discord Forum 频道 → 飞书多维表格 同步工具

将 features-request 论坛频道的所有帖子（标题+标签+正文+回复）同步到飞书表格。

用法:
  export DISCORD_BOT_TOKEN="MTQ4OTE..."
  python3 sync_forum_to_feishu.py                           # 全量同步
  python3 sync_forum_to_feishu.py --since 2026-03-01        # 某日期之后
  python3 sync_forum_to_feishu.py --since 2026-03-01 --dry  # 只拉取不写飞书
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import ssl
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

DISCORD_API = "https://discord.com/api/v10"

FEISHU_CFG_PATH = Path(__file__).resolve().parent.parent / "astrbot_plugin_dc_user_collector" / "feishu_config.json"
FEISHU_TOKEN_URL = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"

OUTPUT_DIR = Path(__file__).resolve().parent / "forum_export"


def _ssl_ctx() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _curl_get(url: str, token: str) -> dict | list:
    p = subprocess.run(
        ["curl", "-s", "-H", f"Authorization: Bot {token}", url],
        capture_output=True, text=True, timeout=30,
    )
    if not p.stdout.strip():
        print(f"  curl empty response for {url}", file=sys.stderr)
        return {}
    try:
        return json.loads(p.stdout)
    except json.JSONDecodeError:
        print(f"  curl parse error: {p.stdout[:200]}", file=sys.stderr)
        return {}


def _discord_get(path: str, token: str) -> dict | list:
    url = f"{DISCORD_API}{path}"
    return _curl_get(url, token)


def _feishu_request(method: str, url: str, token: str, body: dict | None = None) -> dict:
    cmd = ["curl", "-s", "-X", method, url]
    if token:
        cmd += ["-H", f"Authorization: Bearer {token}"]
    cmd += ["-H", "Content-Type: application/json; charset=utf-8"]
    if body:
        cmd += ["-d", json.dumps(body)]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    return json.loads(p.stdout) if p.stdout.strip() else {}


def feishu_token(app_id: str, app_secret: str) -> str:
    r = _feishu_request("POST", FEISHU_TOKEN_URL, "", {"app_id": app_id, "app_secret": app_secret})
    if r.get("code") != 0:
        raise RuntimeError(f"飞书 token 失败: {r}")
    return r["tenant_access_token"]


def feishu_create_record(token: str, app_token: str, table_id: str, fields: dict) -> None:
    url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records"
    r = _feishu_request("POST", url, token, {"fields": fields})
    if r.get("code") != 0:
        raise RuntimeError(f"飞书写入失败: {r}")


def snowflake_to_datetime(snowflake_id: str) -> datetime:
    """Discord snowflake ID → datetime"""
    ts = (int(snowflake_id) >> 22) + 1420070400000
    return datetime.fromtimestamp(ts / 1000, tz=timezone.utc)


# ── Discord Forum API ──

def fetch_forum_tags(channel_id: str, token: str) -> dict[str, str]:
    """Get tag_id → tag_name mapping for the forum channel."""
    ch = _discord_get(f"/channels/{channel_id}", token)
    tags = ch.get("available_tags") or []
    return {str(t["id"]): t["name"] for t in tags}


def fetch_forum_threads(channel_id: str, token: str) -> list[dict]:
    """Fetch all archived + active threads from a forum channel."""
    all_threads = []

    # Active threads (from guild)
    ch_info = _discord_get(f"/channels/{channel_id}", token)
    guild_id = ch_info.get("guild_id")
    if guild_id:
        active = _discord_get(f"/guilds/{guild_id}/threads/active", token)
        for t in active.get("threads") or []:
            if str(t.get("parent_id")) == str(channel_id):
                all_threads.append(t)
        print(f"  活跃帖子: {len(all_threads)}")

    # Public archived threads (paginated)
    archived_count = 0
    before = None
    while True:
        path = f"/channels/{channel_id}/threads/archived/public?limit=100"
        if before:
            path += f"&before={before}"
        data = _discord_get(path, token)
        threads = data.get("threads") or []
        if not threads:
            break
        all_threads.extend(threads)
        archived_count += len(threads)
        if not data.get("has_more"):
            break
        before = threads[-1].get("thread_metadata", {}).get("archive_timestamp")
        time.sleep(0.5)

    print(f"  归档帖子: {archived_count}")
    print(f"  总计: {len(all_threads)} 个帖子")
    return all_threads


def fetch_thread_messages(thread_id: str, token: str) -> list[dict]:
    """Fetch all messages in a thread (paginated)."""
    all_msgs = []
    after = None
    while True:
        path = f"/channels/{thread_id}/messages?limit=100"
        if after:
            path += f"&after={after}"
        msgs = _discord_get(path, token)
        if not isinstance(msgs, list) or not msgs:
            break
        all_msgs.extend(msgs)
        if len(msgs) < 100:
            break
        after = msgs[-1]["id"]
        time.sleep(0.5)
    all_msgs.sort(key=lambda m: m["id"])
    return all_msgs


# ── Main ──

def main() -> None:
    ap = argparse.ArgumentParser(description="Discord Forum → 飞书多维表格")
    ap.add_argument("--channel-id", type=str, default="", help="Forum 频道 ID")
    ap.add_argument("--since", type=str, default="", help="只同步此日期之后的帖子 (YYYY-MM-DD)")
    ap.add_argument("--dry", action="store_true", help="只拉取导出 JSON，不写飞书")
    ap.add_argument("--feishu-table-id", type=str, default="", help="飞书目标表 ID（留空则自动创建）")
    args = ap.parse_args()

    discord_token = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
    if not discord_token:
        print("请设置 DISCORD_BOT_TOKEN 环境变量"); sys.exit(1)

    channel_id = args.channel_id or os.environ.get("DISCORD_FORUM_CHANNEL_ID", "").strip()
    if not channel_id:
        print("请提供 --channel-id 或设置 DISCORD_FORUM_CHANNEL_ID"); sys.exit(1)

    since_dt = None
    if args.since:
        since_dt = datetime.strptime(args.since, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        print(f"筛选日期: {args.since} 之后")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # Step 1: Get forum tags
    print("\n=== 获取论坛标签 ===")
    tag_map = fetch_forum_tags(channel_id, discord_token)
    print(f"  标签: {list(tag_map.values())}")

    # Step 2: Fetch all threads
    print("\n=== 拉取帖子列表 ===")
    threads = fetch_forum_threads(channel_id, discord_token)

    # Step 3: Filter by date & fetch messages
    print("\n=== 获取帖子内容 ===")
    posts = []
    for i, thread in enumerate(threads):
        thread_id = thread["id"]
        created_at = snowflake_to_datetime(thread_id)

        if since_dt and created_at < since_dt:
            continue

        title = thread.get("name") or "(无标题)"
        tag_ids = thread.get("applied_tags") or []
        tags = [tag_map.get(str(tid), f"unknown:{tid}") for tid in tag_ids]
        owner_id = thread.get("owner_id") or ""

        print(f"  [{len(posts)+1}] {title[:50]} ({', '.join(tags) or 'no tag'})...", end=" ", flush=True)

        messages = fetch_thread_messages(thread_id, discord_token)
        time.sleep(0.3)

        # First message is the post content
        first_msg = messages[0] if messages else {}
        author = first_msg.get("author", {})
        author_name = author.get("global_name") or author.get("username") or ""
        content = first_msg.get("content") or ""

        # Replies (all messages except the first)
        replies = []
        for msg in messages[1:]:
            r_author = msg.get("author", {})
            r_name = r_author.get("global_name") or r_author.get("username") or ""
            r_content = msg.get("content") or ""
            r_time = msg.get("timestamp") or ""
            if r_content:
                replies.append({"author": r_name, "content": r_content, "timestamp": r_time})

        post = {
            "thread_id": thread_id,
            "title": title,
            "tags": tags,
            "author": author_name,
            "content": content,
            "reply_count": len(replies),
            "replies": replies,
            "created_at": created_at.isoformat(),
            "is_archived": thread.get("thread_metadata", {}).get("archived", False),
            "is_locked": thread.get("thread_metadata", {}).get("locked", False),
        }
        posts.append(post)
        print(f"replies={len(replies)}")

    print(f"\n  共 {len(posts)} 个帖子待同步")

    # Save JSON locally
    with open(OUTPUT_DIR / "forum_posts.json", "w", encoding="utf-8") as f:
        json.dump(posts, f, ensure_ascii=False, indent=2)
    print(f"  → {OUTPUT_DIR / 'forum_posts.json'}")

    if args.dry:
        print("\n--dry 模式，跳过飞书写入")
        return

    # Step 4: Write to Feishu
    print("\n=== 写入飞书多维表格 ===")
    if not FEISHU_CFG_PATH.is_file():
        print(f"找不到 {FEISHU_CFG_PATH}"); sys.exit(1)
    with open(FEISHU_CFG_PATH, encoding="utf-8") as f:
        cfg = json.load(f)

    app_id = cfg["app_id"]
    app_secret = cfg["app_secret"]
    app_token = cfg["app_token"]
    table_id = args.feishu_table_id

    # Create table if not specified
    if not table_id:
        print("  未指定 --feishu-table-id，请先创建飞书表并提供 table_id")
        print("  或使用 lark-cli 创建:")
        print(f'    lark-cli base +table-create --base-token {app_token} --name "forum_features_request"')
        sys.exit(1)

    token = feishu_token(app_id, app_secret)

    for i, post in enumerate(posts):
        replies_text = ""
        for r in post["replies"]:
            replies_text += f"[{r['author']}] {r['content']}\n"

        fields = {
            "标题": post["title"],
            "标签": ", ".join(post["tags"]),
            "作者": post["author"],
            "正文": post["content"][:2000],
            "回复": replies_text[:2000] if replies_text else "(无回复)",
            "回复数": str(post["reply_count"]),
            "创建时间": post["created_at"],
            "状态": "Resolved" if "Resolved" in post["tags"] else "Open",
        }

        try:
            feishu_create_record(token, app_token, table_id, fields)
            print(f"  [{i+1}/{len(posts)}] ✓ {post['title'][:40]}")
        except Exception as e:
            print(f"  [{i+1}/{len(posts)}] ✗ {post['title'][:40]}: {e}")
        time.sleep(0.3)

    print(f"\n完成！共写入 {len(posts)} 条记录")


if __name__ == "__main__":
    main()
