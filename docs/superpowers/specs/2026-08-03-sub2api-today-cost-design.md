# Sub2API 今日花费 Design

## Goal

在 Sub2API 相关订阅卡片上展示「今日花费」，数据来自已有 `GET /v1/usage` 响应，不改 Sub2API 服务端。

## Background

Sub2API `GET /v1/usage` 在所有模式（`quota_limited` / 套餐 `unrestricted` / 钱包 `unrestricted`）下都会 best-effort 附带：

```json
"usage": {
  "today": {
    "actual_cost": 0.12,
    "cost": 0.15
  },
  "total": { "actual_cost": 1.23, "cost": 1.5 }
}
```

`ai-plan-insight` 的 `Sub2ApiProvider` / `CodexProvider` 目前：

- 套餐账号：只展示日/周/月订阅窗口额度，未用 `usage.today`
- 余额账号：进度条 `used` 为累计总花费
- Codex unrestricted：进度条 `used` 已是今日花费

套餐的「日额度」是订阅窗口用量，与日历「今日花费」不完全相同，两者可并存。

## Design

### Data selection

优先 `usage.today.actual_cost`，否则回退 `usage.today.cost`。  
两者皆无、`usage` 缺失或无法解析为数字时：**不写入**该 balance 行（不写 `$0.00` 以免与真实零花费混淆）。

格式：`$` + 两位小数，例如 `$0.12`。

### Provider changes

File: `ai_plan_insight/providers/codex.py`

1. 增加 helper（实例方法或模块级均可）：从 `raw_data` 提取今日花费，返回 `dict[str, str]`；有值时为 `{"今日花费": "$x.xx"}`，否则 `{}`。
2. 在以下解析路径 `balances.update(...)`：
   - `CodexProvider._parse_quota_limited`
   - `Sub2ApiProvider._parse_plan`
   - `Sub2ApiProvider._parse_wallet`
3. **不改** `CodexProvider._parse_unrestricted`：该路径已把今日花费放进进度条 `used`，避免重复展示。

### Frontend

不改 `index.html`。现有 `balance-row` 会直接渲染中文 key「今日花费」。

### Out of scope

- 不改 Sub2API 源码 / API / DB
- 不加累计花费字段
- 不改钱包进度条语义（仍为累计总花费）

## Tests

`tests/test_codex_provider.py`（新建）：

| Case | Expectation |
|------|-------------|
| plan + `actual_cost` | `balances["今日花费"] == "$0.12"` |
| wallet + only `cost` | 回退显示 `$0.15` |
| quota + no `usage.today` | 无 `今日花费` key |
| Codex unrestricted + today | 仍无 balances 中的 `今日花费`（仅进度条 used） |
