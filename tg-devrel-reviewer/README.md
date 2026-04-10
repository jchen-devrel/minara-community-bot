# TG DevRel Reviewer

用 Excel 审核 Telegram 群里的 @提及 和 Bug 反馈，批量回复。

## 它能干什么

```
群里的消息 → AstrBot 插件自动收集 → AI 生成草稿
                                        ↓
              你在 Excel 里审核（approve / edit / skip）
                                        ↓
              一键批量发送回复到各个群 → 数据同步到飞书
```

## 三步走

### 第 1 步：导出待审核到 Excel

```bash
python3 tg_review.py export
```

会自动打开 `review.xlsx`，里面长这样：

| 操作 | ID | 群组 | 触发类型 | 发送者 | 原始消息 | AI 草稿 |
|------|----|------|----------|--------|----------|---------|
| | m_xxx | 产品群 | @提及 | Rocky | workflow 报错 | 感谢反馈！请问... |
| | m_yyy | 技术群 | Bug反馈 | Alex | app crash了 | 了解！请提供... |

### 第 2 步：在 Excel 里编辑

在 **A 列（操作）** 填：

| 填什么 | 效果 |
|--------|------|
| `approve` | 用 AI 草稿原样发到群 |
| `edit` | 你改完 **I 列（AI草稿）** 后发到群 |
| `skip` | 不回复 |
| （留空） | 下次还会出现 |

如果要改回复内容，**直接改 I 列的文字**，然后 A 列填 `edit`。

保存 Excel。

### 第 3 步：发送

```bash
# 先预览（不真的发）
python3 tg_review.py send --dry-run

# 确认没问题后真发
python3 tg_review.py send
```

### 额外：同步到飞书

```bash
python3 tg_review.py sync
```

## 安装

```bash
pip install openpyxl
```

## 配置

把 `astrbot_plugin_tg_assistant/config.json` 复制到这个文件夹（或者脚本会自动去旁边的插件目录找）。

需要以下配置项：
- `telegram_bot_token` — 用来发消息
- `feishu.*` — 用来同步飞书（可选）

## 文件说明

```
tg-devrel-reviewer/
├── tg_review.py      # 主脚本
├── config.json       # 配置（从插件复制）
├── review.xlsx       # 审核表（export 生成）
├── SKILL.md          # Skill 定义
└── README.md         # 本文件
```
