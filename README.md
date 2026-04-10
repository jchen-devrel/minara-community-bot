# Minara Community Bot & DevRel Toolkit

End-to-end toolchain for managing community interactions across **Discord** and **Telegram** — from message collection and AI-powered triage, to human review, batch reply, and Feishu (Lark) Bitable dashboards.

## Architecture

```
┌──────────────────────────────────────────────────────────────────────┐
│                       Data Sources                                   │
│   Discord (channels, forums)              Telegram (groups)          │
└──────────┬──────────────────────────────────────┬────────────────────┘
           │                                      │
           ▼                                      ▼
┌─────────────────────────┐        ┌─────────────────────────────┐
│  astrbot_plugin_dc_     │        │  astrbot_plugin_tg_         │
│  assistant              │        │  assistant                  │
│  ─────────────────────  │        │  ─────────────────────────  │
│  • Watch channels/forums│        │  • Collect all group msgs   │
│  • @mention detection   │        │  • @mention + bug detection │
│  • Keyword matching     │        │  • LLM draft replies        │
│  • LLM draft replies    │        │  • Owner DM approval flow   │
│  • Local JSONL + Feishu │        │  • Daily summary            │
└──────────┬──────────────┘        │  • Local JSONL + Feishu     │
           │                       └──────────┬──────────────────┘
           ▼                                   ▼
┌─────────────────────────┐        ┌─────────────────────────────┐
│  dc-devrel-reviewer     │        │  tg-devrel-reviewer         │
│  ─────────────────────  │        │  ─────────────────────────  │
│  • fetch → Discord API  │        │  • export → Excel           │
│  • scan → match + LLM   │        │  • send → TG batch reply    │
│  • export → Excel       │        │  • sync → Feishu            │
│  • send → Discord reply │        │                             │
│  • sync → Feishu        │        │                             │
└─────────────────────────┘        └─────────────────────────────┘
           │                                   │
           └───────────────┬───────────────────┘
                           ▼
               ┌───────────────────────┐
               │   Feishu Bitable      │
               │   (消息表/审核表/…)    │
               └───────────────────────┘
```

## Modules

### AstrBot Plugins (real-time)

| Plugin | Platform | Key Features |
|--------|----------|-------------|
| [`astrbot_plugin_dc_assistant`](astrbot_plugin_dc_assistant/) | Discord | Channel/forum monitoring, @mention & keyword detection, LLM draft replies, Feishu sync |
| [`astrbot_plugin_tg_assistant`](astrbot_plugin_tg_assistant/) | Telegram | Group message logging, @mention & bug/feedback detection, LLM drafts, owner DM approval, daily summaries |
| [`astrbot_plugin_dc_user_collector`](astrbot_plugin_dc_user_collector/) | Discord | Narrow collector for a specific user's feedback messages |

### CLI Review Tools (batch workflow)

| Tool | Platform | Workflow |
|------|----------|---------|
| [`dc-devrel-reviewer`](dc-devrel-reviewer/) | Discord | `fetch` → `scan` → `export` (Excel) → review → `send` (batch reply) → `sync` (Feishu) |
| [`tg-devrel-reviewer`](tg-devrel-reviewer/) | Telegram | `export` (Excel) → review → `send` (batch reply) → `sync` (Feishu) |
| [`tg-partner-tracker`](tg-partner-tracker/) | Telegram | Partner group history → Excel → Feishu CRM kanban |

### Scripts (standalone pipelines)

| Script | Description |
|--------|-------------|
| [`scripts/sync_forum_to_feishu.py`](scripts/sync_forum_to_feishu.py) | Discord Forum → JSON export + Feishu Bitable |
| [`scripts/score_feature_requests.py`](scripts/score_feature_requests.py) | AI scoring pipeline for feature requests (user value, business impact, feasibility) |
| [`scripts/process_feedback.py`](scripts/process_feedback.py) | LLM-based feedback classification (bug/feature_request/question) + Discord reply |
| [`scripts/feishu_bug_report.py`](scripts/feishu_bug_report.py) | Query bugs from Feishu/local data → send via Feishu bot DM |
| [`temp/create_pages_doc.py`](temp/create_pages_doc.py) | Generate RTF/Pages document from scored feature requests |

## Feature Request Pipeline

The standout pipeline — an end-to-end flow from raw Discord forum posts to AI-scored, prioritized feature requests:

```
Discord Forum API                    User Profiles (optional)
       │                                    │
       ▼                                    ▼
sync_forum_to_feishu.py ──────► score_feature_requests.py
       │                                    │
       ▼                                    ▼
  forum_posts.json                scored_requests.json
       │                                    │
       └──────────────┬────────────────────-┘
                      ▼
              create_pages_doc.py → RTF document
              Feishu Bitable → dashboard view
```

**Scoring dimensions:**
- **User Value** (1-10): How many users benefit?
- **Business Impact** (1-10): Retention, revenue, growth potential
- **Feasibility** (1-10): Implementation complexity
- **Verdict**: `worth_it` / `maybe` / `not_worth_it` with bilingual reasoning

## Setup

### Prerequisites

```bash
pip install openpyxl aiohttp
```

### Configuration

Each module has a `config.json.example` — copy to `config.json` and fill in:

- **Discord Bot Token** — from [Discord Developer Portal](https://discord.com/developers/applications)
- **Telegram Bot Token** — from [@BotFather](https://t.me/BotFather)
- **OpenRouter API Key** — from [OpenRouter](https://openrouter.ai/) (for LLM features)
- **Feishu App Credentials** — from [Feishu Open Platform](https://open.feishu.cn/)

### Quick Start — Feature Request Scoring

```bash
# 1. Export forum posts from Discord
export DISCORD_BOT_TOKEN="your_token"
python3 scripts/sync_forum_to_feishu.py --channel-id FORUM_CHANNEL_ID --dry

# 2. AI-score each request
export OPENROUTER_API_KEY="your_key"
python3 scripts/score_feature_requests.py --dry

# 3. Generate document
python3 temp/create_pages_doc.py
```

### Quick Start — Discord DevRel Review

```bash
cd dc-devrel-reviewer

# Fetch → scan → review → reply → sync
python3 dc_review.py fetch --channels CHANNEL_ID
python3 dc_review.py scan
python3 dc_review.py export          # opens review.xlsx
# ... edit Excel ...
python3 dc_review.py send --dry-run  # preview
python3 dc_review.py send            # send replies
python3 dc_review.py sync            # push to Feishu
```

## Tech Stack

- **Bot Platform**: [AstrBot](https://github.com/Soulter/AstrBot) (plugin system)
- **LLM**: OpenRouter (GPT-4o-mini default, configurable)
- **Data**: Feishu/Lark Bitable (multi-table dashboards)
- **Discord**: REST API v10 (no discord.py dependency)
- **Telegram**: Bot API + Telethon (for partner tracker)
- **Local Storage**: JSONL files + Excel (openpyxl)

## License

Internal tooling for Minara DevRel team.
