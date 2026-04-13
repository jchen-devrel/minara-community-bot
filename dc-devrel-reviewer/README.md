# Discord DevRel Reviewer

CLI workflow for reviewing & replying to Discord messages collected by the `astrbot_plugin_dc_assistant` plugin.

## Setup

1. Ensure `astrbot_plugin_dc_assistant` is deployed (or use `fetch` to pull messages directly)
2. Config auto-resolves: `./config.json` → `../astrbot_plugin_dc_assistant/config.json`
3. `pip install openpyxl`

## Config Keys

| Key | Description |
|-----|-------------|
| `discord_bot_token` | Bot token (required for fetch / send / ack) |
| `my_usernames` | Your Discord usernames for @mention detection |
| `my_discord_ids` | Your Discord user IDs |
| `hazel_discord_ids` | Hazel/BD team member Discord IDs |
| `hazel_usernames` | Hazel username patterns (default: `["hazel"]`) |
| `watch_channels` | Channel IDs to monitor (empty = all) |
| `watch_forums` | Forum channel IDs to monitor |
| `fetch_extra_channel_ids` | Extra IDs merged by `--from-config` |
| `match_keywords` | Keywords that trigger mention flagging |
| `auto_ack.enabled` | Enable English auto-reply on trigger |
| `auto_ack.mode` | `all` / `mention` / `hazel` |
| `auto_ack.message` | Custom ack message text |
| `openrouter_api_key` | For LLM draft generation |
| `feishu.*` | Feishu Bitable sync credentials |

See `../astrbt_plugin_dc_assistant/config.json.example` for the full template.

## Commands

```bash
# Fetch messages from config-defined channels (dedupes against existing JSONL)
python3 dc_review.py fetch --from-config --limit 200

# Fetch from explicit channel IDs
python3 dc_review.py fetch --channels 1234567890 9876543210

# Fetch + send auto-ack English reply to matched messages
python3 dc_review.py fetch --from-config --ack

# Scan stored messages for @mentions, @Hazel, keywords → LLM drafts
python3 dc_review.py scan

# Export pending items to Excel
python3 dc_review.py export

# Edit review.xlsx:
#   A列(操作): approve / edit / skip
#   I列(AI草稿): modify reply text if "edit"
#   Save the file

# Batch reply to Discord
python3 dc_review.py send --dry-run    # preview first
python3 dc_review.py send

# Sync to Feishu Bitable
python3 dc_review.py sync
python3 dc_review.py sync --tables messages   # only messages table
```

## Detection Rules

| Trigger | Condition |
|---------|-----------|
| `@mention` | Message contains `@your_username`, `<@your_id>`, or Discord mention object |
| `@Hazel/BD` | Message @-mentions Hazel by username/ID (configurable) |
| `keyword` | Message text matches any `match_keywords` entry |

Messages matching **any** trigger enter `mentions.jsonl` and the Excel review queue.

## Data Files

| File | Description |
|------|-------------|
| `../astrbot_plugin_dc_assistant/data/messages.jsonl` | All collected messages |
| `../astrbot_plugin_dc_assistant/data/mentions.jsonl` | Matched mentions/keywords |
| `../astrbot_plugin_dc_assistant/data/auto_ack_sent_ids.txt` | Dedup log for auto-ack |
| `review.xlsx` | Generated review spreadsheet |
| `replies.jsonl` | Sent reply log |
