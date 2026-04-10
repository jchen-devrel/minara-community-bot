# TG Partner Tracker

用你的 Telegram 个人账号拉取合作方群的**全部历史消息**，导出 Excel，推送飞书。

## 流程

```
1. login     → 首次登录（手机号 + TG 验证码，只需一次）
2. groups    → 列出你加入的所有群，找到合作方群 ID
3. fetch     → 拉取指定群的全部历史消息到本地
4. export    → 生成 Excel（每群一个 sheet + 汇总页）
5. (你编辑 Excel — 填进度备注)
6. sync      → 推送飞书
```

## 快速开始

### 1. 获取 API 凭证

去 [my.telegram.org/apps](https://my.telegram.org/apps)，用手机号登录，拿到 `api_id` 和 `api_hash`。

填入 `config.json`：
```json
{
  "api_id": "12345678",
  "api_hash": "abcdef1234567890abcdef1234567890"
}
```

### 2. 登录

```bash
python3 tg_history.py login
```
输入手机号 → 在 TG 客户端收验证码 → 输入。之后会生成 `tg_session.session` 文件，后续自动登录。

### 3. 查看群列表

```bash
python3 tg_history.py groups
```

### 4. 配置要跟踪的群

把群 ID 填入 `config.json`：
```json
{
  "partner_groups": [
    {"id": "-1001234567890", "name": "Pharos<>Minara"},
    {"id": "-1009876543210", "name": "Symbiotic<>Minara"}
  ]
}
```

### 5. 拉取历史

```bash
python3 tg_history.py fetch           # 全部历史
python3 tg_history.py fetch --since 2025-01-01  # 指定日期之后
```

### 6. 导出 Excel

```bash
python3 tg_history.py export
```

### 7. 推飞书

```bash
python3 tg_history.py sync
```

## 文件说明

```
tg-partner-tracker/
├── tg_history.py           # 主脚本
├── config.json             # 配置
├── tg_session.session      # Telegram 登录态（自动生成，勿删）
├── data/                   # 每群一个 JSONL 文件
│   ├── group_-100xxx.jsonl
│   └── group_-100yyy.jsonl
├── partner_history.xlsx    # 导出的 Excel
└── README.md
```
