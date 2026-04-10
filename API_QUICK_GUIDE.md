# Minara Admin API Quick Guide

Base URL: `https://xneuro-core-admin-api.prod.nftgo.dev`
Swagger: `https://xneuro-core-admin-api.dev.nftgo.dev/swagger`
认证: `Authorization: Bearer <JWT>`（从 Admin 后台 DevTools 获取，约 7 天有效）

## 调用方式

```bash
# 通用 GET
curl -s -H "Authorization: Bearer $TOKEN" \
  "https://xneuro-core-admin-api.prod.nftgo.dev/ENDPOINT"

# 通用 POST
curl -s -X POST -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"key":"value"}' \
  "https://xneuro-core-admin-api.prod.nftgo.dev/ENDPOINT"
```

> **注意**: Python urllib 会被 Cloudflare 拦截（1010），请用 `curl` 或 `subprocess` 调 curl。

---

## 常用 API 速查

### 用户管理 (Admin - User)

| 用途 | 方法 | 端点 |
|------|------|------|
| 用户列表 | POST | `/admin/user/list` |
| 用户详情视图 | GET | `/admin/user/view?userId={id}` |
| 用户聊天记录 | GET | `/admin/user/view/chats?userId={id}` |
| 支付活动 | GET | `/admin/user/payment/activity?userId={id}` |
| IP 日志 | GET | `/admin/user/ip/logs?userId={id}` |
| 聊天用量 | GET | `/admin/user/chat-usages?userId={id}` |
| Sparks 日明细 | GET | `/admin/user/points/daily?userId={id}&page=1&pageSize=7` |
| 付费用户指标 | GET | `/admin/user/pay-user-credits-metrics` |
| 邀请码 | GET | `/admin/user/invitation-code?userId={id}` |
| Copilot 成交记录 | GET | `/admin/user/users/copilot/fills?userId={id}` |

### 交易数据 (CrossChain)

| 用途 | 方法 | 端点 |
|------|------|------|
| 交易列表 | GET | `/v1/tx/cross-chain/transaction/list?userId={id}&api-key=dashboard_minara_2025` |
| 交易错误 | GET | `/v1/tx/cross-chain/transaction/errors` |
| 交易统计 | GET | `/v1/tx/cross-chain/transaction/statistics` |
| 交易量(按时间) | GET | `/v1/tx/cross-chain/transaction/volume/{timeRange}` |

### 运营指标 (Admin - Metrics)

| 用途 | 方法 | 端点 |
|------|------|------|
| Copilot 总览 | GET | `/admin/metrics/copilot/overview` |
| Copilot 日统计 | GET | `/admin/metrics/copilot/daily-stats` |
| Copilot 国家统计 | GET | `/admin/metrics/copilot/country-stats` |
| 用户分群 | GET | `/admin/metrics/user-metrics/segments` |
| Credits 消耗总览 | GET | `/admin/metrics/user-credits/credits-cost-metrics-overview` |
| 留存率 | GET | `/admin/metrics/user-credits/retention-rates` |
| 活跃用户图表 | GET | `/admin/metrics/user-credits/active-user-charts` |
| Credit 用量图表 | GET | `/admin/metrics/user-credits/credit-usage-chart` |
| 收入汇总 | GET | `/admin/metrics/payment/income-summary` |
| 收入图表 | GET | `/admin/metrics/payment/chart` |
| 月留存 | GET | `/admin/metrics/payment/monthly-retention` |
| 国家维度收入 | GET | `/admin/metrics/payment-total/country` |
| 历史 MRR | GET | `/admin/metrics/payment-total/historical-mrr` |

### 合约交易 (HyperliquidPerps)

| 用途 | 方法 | 端点 |
|------|------|------|
| 胜率分析 | GET | `/v1/tx/perps/analysis-winning-rate?userId={id}` |
| 权益历史 | GET | `/v1/tx/perps/equity-history-chart/all?userId={id}` |
| 已完成交易 | GET | `/v1/tx/perps/completed-trades/all?userId={id}` |
| 持仓 | GET | `/v1/tx/perps/positions/all?userId={id}` |
| 资金记录 | GET | `/v1/tx/perps/fund-records?userId={id}` |

### Workflow

| 用途 | 方法 | 端点 |
|------|------|------|
| Workflow 列表(Admin) | GET | `/admin/workflow/view` |
| Workflow 详情 | GET | `/admin/workflow/view/detail/{workflowId}` |
| 用户 Workflows(Ops) | GET | `/admin/ops/user/workflows?userId={id}` |
| Credit 使用明细(Ops) | GET | `/admin/ops/user/credit-usage?userId={id}` |

### 其他

| 用途 | 方法 | 端点 |
|------|------|------|
| Trending 代币 | GET | `/tokens/trending-tokens` |
| 搜索代币 | GET | `/tokens/search-tokens?keyword={q}` |
| 代币分析 | GET | `/tokens/token-analysis?symbol={symbol}` |
| Discover 事件 | GET | `/discover/events` |
| 恐慌贪婪指数 | GET | `/discover/fear-greed-index` |
| BTC 指标 | GET | `/discover/bitcoin-metrics` |
| 邀请指标 | GET | `/admin/invitation/metrics` |

---

## 快速示例

```bash
export TOKEN="Bearer eyJhbG..."

# 拉前 10 个用户
curl -s -X POST -H "Authorization: $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"email":"","username":"","sortBy":"createdAt","desc":true,"page":1,"limit":10}' \
  "https://xneuro-core-admin-api.prod.nftgo.dev/admin/user/list"

# 查看 Copilot 总览
curl -s -H "Authorization: $TOKEN" \
  "https://xneuro-core-admin-api.prod.nftgo.dev/admin/metrics/copilot/overview"

# 查看某用户的交易
curl -s -H "Authorization: $TOKEN" \
  "https://xneuro-core-admin-api.prod.nftgo.dev/v1/tx/cross-chain/transaction/list?userId=USER_ID&api-key=dashboard_minara_2025"

# 查看留存率
curl -s -H "Authorization: $TOKEN" \
  "https://xneuro-core-admin-api.prod.nftgo.dev/admin/metrics/user-credits/retention-rates"
```
