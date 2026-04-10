# Telegram Assistant (AstrBot Plugin)

Telegram 群组消息收集 + AI 自动回复草稿 + 人工审核 + 每日总结 + 飞书同步。

## 功能一览

| # | 功能 | 说明 |
|---|------|------|
| 1 | **全频道消息收集** | Bot 所在的所有群组/频道消息自动记录到本地 JSONL |
| 2 | **每日总结（可选）** | 仅发到你**私聊**，**不会发到任何群**。`daily_summary_enabled=false` 可完全关闭 |
| 3 | **DevRel 推送** | **@你** 必然推送；另按关键词（+ 可选 LLM）识别 **Bug/技术反馈**，**即使没有 @ 你** 也会推送，草稿按 DevRel 口吻（致谢、安抚、追问环境/复现） |
| 4 | **人工审核回复** | 在私聊中用 `/approve` `/edit` `/skip` 管理每条草稿 |
| 5 | **飞书同步** | 消息、提及、回复、日报 → 四张飞书多维表格 |

## 数据流

```
群组消息 ──→ 本地 data/messages.jsonl ──→ 飞书「消息表」
                                       ──→ 每日总结 → 飞书「日报表」

@提及 或 Bug/反馈 ──→ LLM 草稿 ──→ DM 发给你（触发类型会标在 DM 里）
                  ──→ data/mentions.jsonl ──→ 飞书「提及表」

你审核通过 ──→ Bot 回复到原群 ──→ data/replies.jsonl ──→ 飞书「回复表」
```

## 快速开始

### 1. 创建 Telegram Bot

1. 在 Telegram 搜索 **@BotFather**，发 `/newbot`
2. 按提示设置名字，拿到 **Bot Token**（格式 `123456:ABC-DEF...`）
3. 发 `/setprivacy` → 选你的 bot → **Disable**（让 bot 能看到群里所有消息）

### 2. 获取你的 Telegram User ID

私聊 **@userinfobot**，它会回复你的数字 ID。

### 3. AstrBot 配置 Telegram 平台

在 AstrBot 管理面板 → 平台配置 → 添加 Telegram → 填入 Bot Token。

### 4. 把 Bot 加入群组

把你的 bot 拉进你要监控的 Telegram 群。

### 5. 填写插件配置

编辑 `config.json`：

| 字段 | 说明 |
|------|------|
| `telegram_bot_token` | 和 AstrBot 平台配一样的 token |
| `owner_telegram_id` | 你的数字 ID |
| `my_usernames` | 你的 TG 用户名（用于检测 @提及） |
| `openrouter_api_key` | OpenRouter API key（用于 LLM） |
| `daily_summary_enabled` | `true` 才启用日报；`false` 则**不跑定时、不响应** `/summary` |
| `daily_summary_hour_utc` | **仅在** `daily_summary_enabled=true` 时有效：每天 **UTC 整点** 往你**私聊**发摘要（例如 `13` = UTC 13:00 = 北京时间 21:00）。**与群发无关** |

#### DevRel / Bug 推送（`devrel` 段）

| 字段 | 说明 |
|------|------|
| `bug_feedback_push_enabled` | 是否开启「无 @ 的 Bug 反馈」推送，默认 `true` |
| `min_text_length_for_bug` | 至少多少字才做 Bug 匹配，过滤 `ok`、`thanks` |
| `use_llm_bug_classifier` | 关键词没命中时是否再调用 LLM 判断是否为 Bug（**每条消息多一次 API**，默认 `false`） |
| `bug_feedback_keywords` | 额外关键词列表，与内置中英词库 **合并** |

内置关键词覆盖常见英文/中文（如 `bug`、`crash`、`报错`、`崩溃`、`无法`、`not working` 等），可在 `bug_feedback_keywords` 里加产品专有词。

### 6. 上传插件

```bash
cd bot_plugin
zip -r astrbot_plugin_tg_assistant.zip astrbot_plugin_tg_assistant/
```

在 AstrBot 管理面板 → 插件管理 → 上传 zip。

## 审核命令（在 Bot 私聊中使用）

| 命令 | 说明 |
|------|------|
| `/approve m_xxx` | 发送 AI 草稿到原群 |
| `/edit m_xxx 你的修改内容` | 用你的内容替代草稿并发送 |
| `/skip m_xxx` | 跳过这条 |
| `/pending` | 查看所有待审核项（@提及 + Bug 推送） |
| `/summary` | 立即触发一次每日总结 |

## 飞书同步（可选）

在 `config.json` 的 `feishu` 部分填入：

1. 飞书开放平台的 `app_id` / `app_secret`
2. 多维表格的 `app_token`
3. 四张表的 `table_id`：

| 表 | 字段 |
|----|------|
| 消息表 (`messages_table_id`) | 群组、发送者、用户名、消息内容、时间、是否@我、**DevRel触发**（无 / @提及 / Bug反馈 / 组合） |
| 提及表 (`mentions_table_id`) | mention_id、群组、发送者、消息内容、AI草稿、状态、时间、**触发类型** |
| 回复表 (`replies_table_id`) | mention_id、群组、原始消息、最终回复、回复时间 |
| 日报表 (`summary_table_id`) | 日期、消息总数、群组数、总结 |

在飞书中手动建好这四张表并添加对应字段，然后把 `table_id` 填入配置，`enabled` 设为 `true`。

## 本地数据

所有数据自动保存在插件目录下的 `data/` 文件夹：

```
data/
├── messages.jsonl         # 所有群组消息
├── mentions.jsonl         # @提及记录 + AI 草稿
├── replies.jsonl          # 已发送的回复
└── daily_summaries.jsonl  # 每日总结
```

## 注意事项

- **Bot 只能收到它所在群的消息**。如果需要监控某个群，必须先把 bot 拉进去。
- **Bot Privacy Mode 必须关闭**（`/setprivacy` → Disable），否则只能收到 /command 和 @bot 消息。
- Telegram 单条消息上限 4096 字符，超长的日报会自动分段发送。
