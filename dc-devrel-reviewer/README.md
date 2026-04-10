# Discord DevRel Reviewer

CLI workflow for reviewing & replying to Discord messages collected by the `astrbot_plugin_dc_assistant` plugin.

## Setup

1. Ensure `astrbot_plugin_dc_assistant` is deployed (or use `fetch` to pull directly)
2. Config auto-resolves from `../astrbot_plugin_dc_assistant/config.json`
3. `pip install openpyxl`

## Commands

```bash
# Step 1: Fetch messages from Discord channels / forums
python3 dc_review.py fetch --channels 1234567890 9876543210

# Step 2: Scan stored messages for @mentions and keyword matches → LLM drafts
python3 dc_review.py scan

# Step 3: Export pending items to Excel
python3 dc_review.py export

# Step 4: Edit review.xlsx
#   - A列(操作): approve / edit / skip
#   - I列(AI草稿): modify reply text if "edit"
#   - Save

# Step 5: Batch reply to Discord
python3 dc_review.py send
python3 dc_review.py send --dry-run    # preview first

# Step 6: Sync to Feishu Bitable
python3 dc_review.py sync
python3 dc_review.py sync --tables messages   # only messages
```

## Data Files

- `../astrbot_plugin_dc_assistant/data/messages.jsonl` — all collected messages
- `../astrbot_plugin_dc_assistant/data/mentions.jsonl` — matched mentions/keywords
- `review.xlsx` — generated review spreadsheet
- `replies.jsonl` — sent reply log
