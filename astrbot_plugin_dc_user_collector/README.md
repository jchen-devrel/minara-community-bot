# Discord User Collector

在指定 Discord 频道中，采集指定用户（按用户名关键词匹配）的消息，**可选写入飞书多维表格**，并**可选**保留本地 JSONL 备份。

## 行为说明

- 监听 Discord 消息（`EventMessageType.ALL`）
- 仅处理频道名称为 **`feedback`** 的消息
- 仅处理用户名或显示名中包含 **`hazel`**（不区分大小写）的用户
- 忽略机器人消息
- 匹配后：按配置写入飞书、按配置追加 `data/discord_user_messages.jsonl`

## 本地筛选配置

在 `main.py` 顶部修改：

- `TARGET_CHANNEL_NAME`
- `TARGET_USERNAME_KEYWORD`

建议稳定后改为按 **用户 ID** 匹配。

## 飞书多维表格配置

1. 在[飞书开放平台](https://open.feishu.cn/)创建企业自建应用，开启权限（至少其一）：
   - **新增记录**（`base:record:create`），或
   - **查看、评论、编辑和管理多维表格**（`bitable:app`）
2. 发布应用版本，在目标租户安装。
3. 打开你的多维表格 → 右上角 **…** → **…更多** → **添加文档应用**，把该应用加进去并赋予**可管理**或能新增记录的权限（与官方文档一致）。
4. 从浏览器地址栏取 **`app_token`**（`/base/` 后第一段）和 **`table_id`**（`table=` 后）。
5. 在插件目录下复制示例并填写：

   ```bash
   cp feishu_config.json.example feishu_config.json
   ```

   编辑 `feishu_config.json`：`app_id`、`app_secret`、`app_token`、`table_id`。

6. **`field_mapping`**：键为代码里的字段名，值为多维表格**列名**（需与表格里完全一致；不确定时可调用[列出字段](https://open.feishu.cn/document/server-docs/docs/bitable-v1/app-table-field/list)核对）。

7. **`date_field_keys`**：哪些键对应飞书 **日期** 列，会按**毫秒时间戳**写入。默认 `timestamp`（Discord 消息时间，插件内按秒则自动乘 1000）。若某列是**文本**，请从 `date_field_keys` 中去掉对应键，这样会按字符串写入。

8. **`save_local_jsonl`**：`true` 仍写本地 jsonl；`false` 只写飞书。

9. **`enabled`**：`false` 时仅本地 jsonl（与未放置配置文件行为一致：仅 jsonl）。

### 并发说明

飞书同一数据表不建议并发写。插件内对写入做了 **asyncio 锁** 串行化，降低 `Write conflict` 概率。

## 依赖

写入飞书使用 **`aiohttp`** 发 HTTP 请求。使用 Discord 适配器的 AstrBot 环境一般已具备；若报错缺少 aiohttp，请在 AstrBot 运行环境中安装。

## 记录字段

`source_platform`、`guild_id`、`guild_name`、`channel_id`、`channel_name`、`message_id`、`user_id`、`username`、`display_name`、`content`、`timestamp`、`collected_at`。

## 平台

仅支持 **Discord**（见 `metadata.yaml` 中 `support_platforms`）。
