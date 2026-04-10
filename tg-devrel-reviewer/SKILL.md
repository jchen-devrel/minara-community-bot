# TG DevRel Reviewer

## Description

Excel-based review workflow for Telegram DevRel messages collected by the `astrbot_plugin_tg_assistant` plugin. Export pending @mentions and bug reports to a spreadsheet, review/edit replies locally, then batch-send approved replies back to Telegram groups. Optionally sync all data to Feishu Bitable.

## When to use

- User wants to review Telegram mentions or bug reports in Excel/Numbers
- User wants to batch-approve or edit AI-generated reply drafts
- User wants to send approved replies to Telegram groups
- User wants to sync Telegram messages/mentions/replies to Feishu

## Setup

1. Ensure `astrbot_plugin_tg_assistant` is deployed and collecting data
2. Copy or symlink the plugin's `config.json` into this folder (or the script will auto-find the plugin's config in the sibling directory)
3. Ensure the plugin's `data/` folder has JSONL files (messages, mentions, replies)
4. Install dependency: `pip install openpyxl`

## Commands

```bash
# Step 1: Export pending items to review.xlsx (auto-opens in Excel/Numbers)
python3 tg_review.py export

# Step 2: Edit review.xlsx
#   - Column A (操作): fill "approve", "edit", or "skip"
#   - Column I (AI草稿): modify reply text if action is "edit"
#   - Save the file

# Step 3: Send approved replies to Telegram
python3 tg_review.py send
python3 tg_review.py send --dry-run    # preview first

# Step 4: Sync all local data to Feishu Bitable
python3 tg_review.py sync
```

## Files

- `tg_review.py` — main script
- `config.json` — copy of plugin config (with bot token + feishu credentials)
- `review.xlsx` — generated review spreadsheet (created by `export`)
- `data/` — symlink or copy of plugin data directory
