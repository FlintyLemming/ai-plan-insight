# 设备离线提醒（Stale Source Alert）设计

日期：2026-07-11

## 背景与目标

「模型用量」页面的数据由各设备上的 ai-usage-agent 通过 `/api/usage/report` 推送。Agent 可能因为机器关机、进程崩溃、token 失效等原因停止工作，目前面板上没有任何提示。

目标：当一个 token 用量上报设备**连续超过 24 小时**没有任何上报时，在「模型用量」页面最下方显示提醒，并提供「不再提示」按钮。

关键语义：本功能检测的是**推送 Agent 是否存活**，不是用量是否为零。`source.last_seen` 在每次 `/api/usage/report` 时无条件更新（即使 payload 里 0 个数据点），因此即便模型用量为 0，只要收到过上报就算存活。Agent 端无需任何改动。

## 范围

- 仅监控通过 `/api/usage/report` 上报的设备（`source` 表中 `last_seen IS NOT NULL` 的行）。
- 卡片推送 Agent（Cursor / Claude / Grok / Antigravity / MiMo）不在监控范围内。它们经 `_ensure_source_row` 写入的桩行 `last_seen` 为 NULL，自然被排除。

## 数据模型

`source` 表新增一列（沿用 `init_schema` 现有的 `PRAGMA table_info` + `ALTER TABLE` 就地迁移模式）：

- `stale_dismissed_at TEXT`：用户点击「不再提示」的时间（带时区 ISO 字符串）；NULL 表示未屏蔽。

自动解除屏蔽：`upsert_points` 的 source UPSERT 中把 `stale_dismissed_at` 置回 NULL。设备恢复上报即清除屏蔽，下次再断连会重新提醒。

## Store 层（usage_store.py）

- `get_stale_sources(conn, now=None) -> list[dict]`：返回所有满足以下条件的行：
  - `last_seen IS NOT NULL`
  - `stale_dismissed_at IS NULL`
  - `last_seen` 距 `now` 超过 24 小时（`last_seen` 是带时区的 ISO 字符串，在 Python 侧用 `datetime.fromisoformat` 解析比较；解析失败的行跳过）
  - 返回字段：`source_id`、`label`、`last_seen`
- `dismiss_stale_source(conn, source_id, now) -> bool`：`UPDATE source SET stale_dismissed_at = ? WHERE source_id = ?`，返回是否命中行（用于 404）。

## API 层（web.py）

- `GET /api/sources/stale`：返回 `[{source_id, label, last_seen}]`。DB 异常时返回空列表并记日志（提醒功能不应把页面搞挂）。
- `POST /api/sources/{source_id}/dismiss-stale`：写入屏蔽时间，source 不存在时返回 404。与现有面板端点一致，不做鉴权（面板本身无鉴权，与 `/api/admin/sources` 同级）。

## 前端（index.html）

- `#usage-table-container` 下方新增 `#stale-sources-container`。
- `refreshUsageChart` 中并行 fetch `/api/sources/stale`（失败静默，不影响图表）。
- 每个离线设备渲染一条警告条：
  - 文案：`⚠️ 设备「{label 或 source_id}」已超过 {N} 天未上传数据（最后上报：{MM月DD日 HH:mm}），推送 Agent 可能已停止工作`，N 为向下取整的天数（≥1）。
  - 右侧「不再提示」按钮：POST dismiss 成功后从列表移除该条。
- 样式沿用页面暗色卡片风格，警告色 `#f59e0b` 左边框。

## 测试

- store 层（test_usage_store.py 或新文件）：
  - 超过 24h 未上报且未屏蔽 → 返回；不足 24h → 不返回。
  - `last_seen` 为 NULL 的桩行 → 不返回。
  - dismiss 后 → 不返回；dismiss 不存在的 source → 返回 False。
  - dismiss 后设备再次 `upsert_points` 上报、然后再超期 → 重新返回。
- 端点层：GET 返回结构、POST 404 分支。

## 错误处理

- `last_seen` 解析失败的行跳过（不因单行脏数据阻塞列表）。
- 前端 stale 请求失败时静默隐藏容器。
