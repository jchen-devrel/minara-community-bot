#!/usr/bin/env python3
"""
一键：新建飞书多维表格 → 建空表 → 逐列添加文本字段（避免批量加列限流）→ 插入 mock 一行。

前置：本机已 lark-cli auth login（用户身份）。

用法：
  python3 feishu_complete_setup.py
  FEISHU_BASE_NAME="我的测试表" python3 feishu_complete_setup.py
  python3 feishu_complete_setup.py --field-delay 3.0   # 仍报限流时加大间隔（秒）
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
MOCK_FILE = SCRIPTS / "mock_bitable_fields.json"


def lark_bin() -> str:
    local = Path.home() / ".local" / "bin" / "lark-cli"
    if local.is_file() and os.access(local, os.X_OK):
        return str(local)
    return "lark-cli"


def run_lark(argv: list[str]) -> dict:
    cmd = [lark_bin(), *argv]
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        sys.stderr.write(p.stderr or p.stdout or "(no output)\n")
        sys.exit(p.returncode)
    raw = (p.stdout or "").strip()
    if not raw:
        print("lark-cli 无输出", file=sys.stderr)
        sys.exit(1)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        sys.stderr.write(raw[:2000])
        sys.exit(1)


def find_token(obj, prefix: str) -> str | None:
    if isinstance(obj, dict):
        for v in obj.values():
            t = find_token(v, prefix)
            if t:
                return t
    elif isinstance(obj, list):
        for x in obj:
            t = find_token(x, prefix)
            if t:
                return t
    elif isinstance(obj, str) and obj.startswith(prefix) and len(obj) > 8:
        return obj
    return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--field-delay",
        type=float,
        default=2.5,
        help="两次 field-create 之间的间隔秒数，遇 800004135 可改为 4～6",
    )
    args = ap.parse_args()

    if not MOCK_FILE.is_file():
        print(f"缺少 {MOCK_FILE}", file=sys.stderr)
        sys.exit(1)

    with open(MOCK_FILE, encoding="utf-8") as f:
        mock_fields = json.load(f)

    col_names = list(mock_fields.keys())
    base_name = os.environ.get("FEISHU_BASE_NAME", "Discord 采集 Mock")

    print("1/4 创建多维表格 (Base)...")
    env = run_lark(
        [
            "base",
            "+base-create",
            "--name",
            base_name,
            "--time-zone",
            "Asia/Shanghai",
        ]
    )
    if not env.get("ok"):
        print(json.dumps(env, ensure_ascii=False, indent=2))
        sys.exit(1)

    data = env.get("data") or {}
    base_obj = data.get("base") or {}
    base_token = (
        base_obj.get("base_token")
        or base_obj.get("app_token")
        or find_token(base_obj, "basc")
        or find_token(data, "basc")
    )
    base_url = base_obj.get("url") or ""

    if not base_token:
        print("无法解析 base_token", file=sys.stderr)
        print(json.dumps(env, ensure_ascii=False, indent=2), file=sys.stderr)
        sys.exit(1)

    print("2/4 创建空数据表...")
    env2 = run_lark(
        [
            "base",
            "+table-create",
            "--base-token",
            base_token,
            "--name",
            "discord_feedback",
        ]
    )
    if not env2.get("ok"):
        print(json.dumps(env2, ensure_ascii=False, indent=2))
        sys.exit(1)

    d2 = env2.get("data") or {}
    table = d2.get("table") or {}
    table_id = table.get("table_id") or table.get("id") or find_token(table, "tbl")
    if not table_id:
        print("无法解析 table_id：", json.dumps(env2, ensure_ascii=False, indent=2), file=sys.stderr)
        sys.exit(1)

    print(f"3/4 添加 {len(col_names)} 个文本列（间隔 {args.field_delay}s，防限流）...")
    for i, name in enumerate(col_names):
        if i:
            time.sleep(args.field_delay)
        spec = json.dumps({"name": name, "type": "text"}, ensure_ascii=False)
        fe = run_lark(
            [
                "base",
                "+field-create",
                "--base-token",
                base_token,
                "--table-id",
                table_id,
                "--json",
                spec,
            ]
        )
        if not fe.get("ok"):
            print(json.dumps(fe, ensure_ascii=False, indent=2))
            sys.exit(1)
        print(f"  + 列: {name}")

    # Base v3 +record-upsert 的 --json 为「列名 -> 值」平铺对象，不要包 {"fields": ...}
    row: dict[str, str] = {}
    for k, v in mock_fields.items():
        if isinstance(v, (dict, list)):
            row[k] = json.dumps(v, ensure_ascii=False)
        else:
            row[k] = str(v) if v is not None else ""

    body = json.dumps(row, ensure_ascii=False)

    print("4/4 插入 mock 记录...")
    env3 = run_lark(
        [
            "base",
            "+record-upsert",
            "--base-token",
            base_token,
            "--table-id",
            table_id,
            "--json",
            body,
        ]
    )
    if not env3.get("ok"):
        print(json.dumps(env3, ensure_ascii=False, indent=2))
        sys.exit(1)

    print()
    print("—— 完成 ——")
    if base_url:
        print(f"表格链接: {base_url}")
    print("若未自动打开，请到飞书云文档里找新建的多维表格。")
    print()
    print("供 AstrBot / push_mock_to_feishu.sh 使用：")
    print(f'  export FEISHU_BASE_TOKEN="{base_token}"')
    print(f'  export FEISHU_TABLE_ID="{table_id}"')
    print()
    print("feishu_config.json：app_token ← 上面 BASE_TOKEN，table_id ← 上面 TABLE_ID。")


if __name__ == "__main__":
    main()
