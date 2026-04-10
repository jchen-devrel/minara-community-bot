#!/usr/bin/env python3
"""
Feature Request 打分器

读取论坛帖子 + 用户画像 → AI 打分（值不值得做 + 原因）→ 写回飞书表格新列

用法:
  export OPENROUTER_API_KEY="sk-or-..."
  python3 scripts/score_feature_requests.py                    # 全量打分
  python3 scripts/score_feature_requests.py --dry              # 只打分不写飞书
  python3 scripts/score_feature_requests.py --max 10           # 只处理前10条
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

FORUM_POSTS_PATH = Path(__file__).resolve().parent / "forum_export" / "forum_posts.json"
USER_PROFILES_PATH = Path(__file__).resolve().parent.parent / "xneuro-user-profiler" / "output" / "user_profiles.json"
FEISHU_CFG_PATH = Path(__file__).resolve().parent.parent / "astrbot_plugin_dc_user_collector" / "feishu_config.json"

FEISHU_BASE_TOKEN = "Uh7ubxFcLajsuJsPPCUctjTKnqd"
FEISHU_TABLE_ID = "tblSZIoQxwiqDVSe"

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_MODEL = "openai/gpt-4o-mini"

OUTPUT_PATH = Path(__file__).resolve().parent / "forum_export" / "scored_requests.json"

SCORING_PROMPT = """\
You are a product manager at Minara, a crypto trading platform with AI copilot, autopilot trading, workflows, and smart wallet features.

## Your Task
Score this feature request on a 1-10 scale and explain whether it's worth building.

## Scoring Criteria
- **User Value (1-10)**: How many users would benefit? How much does it improve their experience?
- **Business Impact (1-10)**: Does it drive retention, revenue, or growth?
- **Feasibility (1-10)**: How complex is it to build? (10 = easy, 1 = extremely hard)
- **Overall Score (1-10)**: Weighted average — prioritize user value and business impact.

## Context About the Requester
{user_context}

## Community Signal
- Replies: {reply_count}
- Tags: {tags}
{replies_summary}

## Feature Request
**Title**: {title}
**Author**: {author}
**Content**:
{content}

## Output Format (JSON only, no markdown)
{{
  "overall_score": <1-10>,
  "user_value": <1-10>,
  "business_impact": <1-10>,
  "feasibility": <1-10>,
  "verdict": "<worth_it | maybe | not_worth_it>",
  "reason_zh": "<2-3句中文解释为什么值得/不值得做>",
  "reason_en": "<2-3 sentence English explanation>"
}}
"""


def _curl_post(url: str, headers: dict, body: dict) -> dict:
    cmd = ["curl", "-s", "-X", "POST", url]
    for k, v in headers.items():
        cmd += ["-H", f"{k}: {v}"]
    cmd += ["-d", json.dumps(body)]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    try:
        return json.loads(p.stdout) if p.stdout.strip() else {}
    except json.JSONDecodeError:
        print(f"  JSON parse error: {p.stdout[:200]}", file=sys.stderr)
        return {}


def load_user_profiles() -> dict[str, dict]:
    """Load user profiles indexed by username (lowercased)."""
    if not USER_PROFILES_PATH.is_file():
        print(f"  用户画像文件不存在: {USER_PROFILES_PATH}")
        return {}
    with open(USER_PROFILES_PATH, encoding="utf-8") as f:
        profiles = json.load(f)
    index = {}
    for p in profiles:
        name = (p.get("username") or "").lower()
        if name:
            index[name] = p
        display = (p.get("display_name") or "").lower()
        if display and display != name:
            index[display] = p
    return index


def build_user_context(author: str, profiles: dict[str, dict]) -> str:
    profile = profiles.get(author.lower())
    if not profile:
        return "Unknown user — no profile data available."

    segment = profile.get("segment", "unknown")
    tags = profile.get("tags", [])
    trades = profile.get("total_trades", 0)
    volume = profile.get("total_volume_usd", 0)
    plan = profile.get("plan_name", "Free")
    credits_pct = profile.get("credits_usage_pct", 0)
    days = profile.get("days_since_registration", "?")

    lines = [
        f"Segment: {segment}",
        f"Tags: {', '.join(tags) if tags else 'none'}",
        f"Plan: {plan}",
        f"Registered: {days} days ago",
        f"Trades: {trades} (volume: ${volume:.2f})",
        f"Credits usage: {credits_pct}%",
    ]
    if profile.get("is_paying"):
        lines.append("** This is a PAYING user — their feedback carries extra weight **")
    if profile.get("is_active_trader"):
        lines.append("** Active trader (5+ trades in 30 days) — experienced user **")
    return "\n".join(lines)


def ai_score(prompt: str, api_key: str) -> dict:
    body = {
        "model": OPENROUTER_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.3,
    }
    r = _curl_post(OPENROUTER_URL, {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }, body)

    text = r.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    return json.loads(text)


def feishu_update_record(record_id: str, fields: dict) -> None:
    """Update existing record in Feishu table."""
    with open(FEISHU_CFG_PATH, encoding="utf-8") as f:
        cfg = json.load(f)

    # Get token
    token_resp = _curl_post(
        "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
        {"Content-Type": "application/json"},
        {"app_id": cfg["app_id"], "app_secret": cfg["app_secret"]},
    )
    token = token_resp.get("tenant_access_token", "")

    url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{FEISHU_BASE_TOKEN}/tables/{FEISHU_TABLE_ID}/records/{record_id}"
    cmd = ["curl", "-s", "-X", "PUT", url,
           "-H", f"Authorization: Bearer {token}",
           "-H", "Content-Type: application/json",
           "-d", json.dumps({"fields": fields})]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    resp = json.loads(p.stdout) if p.stdout.strip() else {}
    if resp.get("code") != 0:
        raise RuntimeError(f"飞书更新失败: {resp}")


def feishu_list_records() -> list[dict]:
    """List all records from Feishu table to get record_ids."""
    with open(FEISHU_CFG_PATH, encoding="utf-8") as f:
        cfg = json.load(f)

    token_resp = _curl_post(
        "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
        {"Content-Type": "application/json"},
        {"app_id": cfg["app_id"], "app_secret": cfg["app_secret"]},
    )
    token = token_resp.get("tenant_access_token", "")

    all_records = []
    page_token = ""
    while True:
        url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{FEISHU_BASE_TOKEN}/tables/{FEISHU_TABLE_ID}/records?page_size=100"
        if page_token:
            url += f"&page_token={page_token}"
        cmd = ["curl", "-s", url, "-H", f"Authorization: Bearer {token}"]
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        resp = json.loads(p.stdout) if p.stdout.strip() else {}
        items = resp.get("data", {}).get("items", [])
        all_records.extend(items)
        if not resp.get("data", {}).get("has_more"):
            break
        page_token = resp.get("data", {}).get("page_token", "")
    return all_records


def main() -> None:
    ap = argparse.ArgumentParser(description="Feature Request AI 打分")
    ap.add_argument("--dry", action="store_true", help="只打分不写飞书")
    ap.add_argument("--max", type=int, default=0, help="最多处理几条")
    args = ap.parse_args()

    api_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not api_key:
        print("请设置 OPENROUTER_API_KEY 环境变量"); sys.exit(1)

    # Load data
    print("=== 加载数据 ===")
    with open(FORUM_POSTS_PATH, encoding="utf-8") as f:
        posts = json.load(f)
    print(f"  帖子: {len(posts)} 条")

    profiles = load_user_profiles()
    print(f"  用户画像: {len(profiles)} 个")

    if args.max:
        posts = posts[:args.max]
        print(f"  限制处理: {len(posts)} 条")

    # Get Feishu record IDs for matching
    feishu_records = []
    if not args.dry:
        print("\n=== 获取飞书记录 ===")
        feishu_records = feishu_list_records()
        print(f"  飞书记录: {len(feishu_records)} 条")

    # Score each post
    print("\n=== AI 打分 ===")
    scored = []
    for i, post in enumerate(posts):
        title = post["title"]
        author = post["author"]
        content = post["content"] or "(no content)"
        tags = ", ".join(post["tags"]) or "none"
        reply_count = post["reply_count"]

        replies_text = ""
        for r in post.get("replies", [])[:5]:
            replies_text += f"  - {r['author']}: {r['content'][:150]}\n"
        replies_summary = f"Top replies:\n{replies_text}" if replies_text else ""

        user_context = build_user_context(author, profiles)

        prompt = SCORING_PROMPT.format(
            title=title, author=author, content=content[:1500],
            tags=tags, reply_count=reply_count,
            user_context=user_context, replies_summary=replies_summary,
        )

        print(f"  [{i+1}/{len(posts)}] {title[:45]}...", end=" ", flush=True)

        try:
            result = ai_score(prompt, api_key)
            result["title"] = title
            result["author"] = author
            result["tags"] = post["tags"]
            scored.append(result)
            print(f"→ {result['overall_score']}/10 ({result['verdict']})")
        except Exception as e:
            print(f"→ 失败: {e}")
            scored.append({"title": title, "overall_score": 0, "verdict": "error", "reason_zh": str(e)})

        time.sleep(1)

    # Save locally
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(scored, f, ensure_ascii=False, indent=2)
    print(f"\n  → {OUTPUT_PATH}")

    # Write to Feishu
    if not args.dry and feishu_records:
        print("\n=== 写入飞书 ===")

        # First add score columns if not exist
        print("  添加评分列...")
        for col in ['{"name":"AI评分","type":"text"}', '{"name":"verdict","type":"text"}', '{"name":"评分理由","type":"text"}']:
            cmd = ["curl", "-s", "-X", "POST",
                   f"https://open.feishu.cn/open-apis/bitable/v1/apps/{FEISHU_BASE_TOKEN}/tables/{FEISHU_TABLE_ID}/fields"]
            # Get fresh token
            with open(FEISHU_CFG_PATH) as f:
                cfg = json.load(f)
            tr = _curl_post("https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
                           {"Content-Type": "application/json"},
                           {"app_id": cfg["app_id"], "app_secret": cfg["app_secret"]})
            tok = tr.get("tenant_access_token", "")
            field_data = json.loads(col)
            cmd = ["curl", "-s", "-X", "POST",
                   f"https://open.feishu.cn/open-apis/bitable/v1/apps/{FEISHU_BASE_TOKEN}/tables/{FEISHU_TABLE_ID}/fields",
                   "-H", f"Authorization: Bearer {tok}",
                   "-H", "Content-Type: application/json",
                   "-d", json.dumps({"field_name": field_data["name"], "type": 1})]
            subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            time.sleep(1)

        # Match scored posts to Feishu records by title
        record_by_title = {}
        for rec in feishu_records:
            fields = rec.get("fields", {})
            t = fields.get("标题", "")
            if isinstance(t, list):
                t = "".join(seg.get("text", "") for seg in t if isinstance(seg, dict))
            record_by_title[t.strip()] = rec["record_id"]

        updated = 0
        for s in scored:
            title = s.get("title", "")
            record_id = record_by_title.get(title)
            if not record_id:
                continue

            score_text = f"{s.get('overall_score','?')}/10 (用户价值:{s.get('user_value','?')} 商业影响:{s.get('business_impact','?')} 可行性:{s.get('feasibility','?')})"
            fields = {
                "AI评分": score_text,
                "verdict": s.get("verdict", ""),
                "评分理由": s.get("reason_zh", ""),
            }
            try:
                feishu_update_record(record_id, fields)
                updated += 1
                print(f"  ✓ {title[:40]}")
            except Exception as e:
                print(f"  ✗ {title[:40]}: {e}")
            time.sleep(0.3)

        print(f"\n  更新 {updated}/{len(scored)} 条记录")

    # Summary
    print("\n=== 打分摘要 ===")
    if scored:
        valid = [s for s in scored if s.get("overall_score", 0) > 0]
        if valid:
            avg = sum(s["overall_score"] for s in valid) / len(valid)
            worth = [s for s in valid if s.get("verdict") == "worth_it"]
            maybe = [s for s in valid if s.get("verdict") == "maybe"]
            not_worth = [s for s in valid if s.get("verdict") == "not_worth_it"]
            print(f"  平均分: {avg:.1f}/10")
            print(f"  值得做: {len(worth)}")
            print(f"  待定: {len(maybe)}")
            print(f"  不值得: {len(not_worth)}")
            print(f"\n  TOP 5 最值得做:")
            for s in sorted(valid, key=lambda x: x["overall_score"], reverse=True)[:5]:
                print(f"    {s['overall_score']}/10 [{s['verdict']}] {s['title'][:50]}")
                print(f"          {s.get('reason_zh','')[:80]}")


if __name__ == "__main__":
    main()
