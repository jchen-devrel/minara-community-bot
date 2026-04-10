#!/usr/bin/env bash
# 使用 lark-cli 将 JSON 中的一条「字段对象」写入飞书多维表格。
#
# 安装 lark-cli（任选其一）：
#   npm install -g @larksuite/cli && npx skills add larksuite/cli -y -g
#   或 git clone https://github.com/larksuite/cli.git && cd cli && go build -o lark-cli .
#        && install -m755 lark-cli ~/.local/bin/   （若 make 失败可跳过 fetch_meta 直接 go build）
#
# 前置：
#   1) lark-cli 在 PATH 中，或存在于 ~/.local/bin/lark-cli
#   2) lark-cli config init --new（按提示浏览器完成）
#   3) lark-cli auth login --recommend
#   4) 多维表格已把你的应用添加为「文档应用」且列名与 JSON 的 key 一致
#
# 用法：
#   export FEISHU_BASE_TOKEN="bascxxxxxxxx"   # 浏览器打开 base 后 URL 里 /base/ 后一段
#   export FEISHU_TABLE_ID="tblxxxxxxxx"      # URL 里 table= 后一段
#   ./push_mock_to_feishu.sh
#
# 可选：指定其它 JSON 文件（内容须为「列名 -> 值」，与多维表格列名完全一致）
#   ./push_mock_to_feishu.sh /path/to/fields.json

set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
JSON_FILE="${1:-$ROOT/mock_bitable_fields.json}"

# 优先使用 ~/.local/bin/lark-cli（避免 PATH 里损坏的旧全局 npm 包）
[[ -x "$HOME/.local/bin/lark-cli" ]] && export PATH="$HOME/.local/bin:$PATH"

if ! command -v lark-cli >/dev/null 2>&1; then
  echo "未找到 lark-cli。请安装：https://github.com/larksuite/cli"
  echo "  npm:  npm install -g @larksuite/cli"
  echo "  源码: git clone ... && make install PREFIX=\$HOME/.local"
  exit 1
fi

: "${FEISHU_BASE_TOKEN:?请设置环境变量 FEISHU_BASE_TOKEN（多维表格 app_token）}"
: "${FEISHU_TABLE_ID:?请设置环境变量 FEISHU_TABLE_ID（数据表 table_id）}"

if [[ ! -f "$JSON_FILE" ]]; then
  echo "文件不存在: $JSON_FILE"
  exit 1
fi

# lark-cli Base v3 的 +record-upsert：--json 为平铺「列名 -> 值」，与 mock_bitable_fields.json 一致即可
BODY="$(python3 -c '
import json, sys
path = sys.argv[1]
with open(path, "r", encoding="utf-8") as f:
    o = json.load(f)
print(json.dumps(o, ensure_ascii=False))
' "$JSON_FILE")"

echo "写入中... base=$FEISHU_BASE_TOKEN table=$FEISHU_TABLE_ID"
lark-cli base +record-upsert \
  --base-token "$FEISHU_BASE_TOKEN" \
  --table-id "$FEISHU_TABLE_ID" \
  --json "$BODY"

echo "完成。"
