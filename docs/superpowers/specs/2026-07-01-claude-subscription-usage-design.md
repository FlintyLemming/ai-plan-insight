# Claude 订阅用量推送 — 设计文档

- 日期：2026-07-01
- 状态：已确认，待实现

## 目标

支持外部 agent 把 Claude 订阅（Pro/Max）的配额用量推送到 AI Plan Insight，在 web 面板上以两张 limit 卡片的形式展示，与 Antigravity 的双窗口配额展示视觉一致。

## 背景

Claude 订阅用量没有可从服务端查询的官方 API，只能由本地 agent 抓取后推送。这与 Cursor、MiMo、Antigravity 的处境相同，代码库中已存在成熟的 **push 模式**：外部 agent 调用 `POST /api/push/<provider>`，数据存入内存字典，30 分钟 TTL，由 `GET /api/usage` 合并返回后在前端渲染。

本设计完全复用该模式，不引入数据库、不新增持久化、不新增认证（与现有 push 端点一致）。

## 数据契约

### Agent 上报结构

Agent 只上报两个窗口（7 天配额、5 小时会话）的百分比与重置时间，不带账号/档位信息。provider 名称在服务端硬编码。

```json
{
  "seven_day": {
    "utilization": 45.2,
    "resets_at": "2026-07-08T12:00:00Z"
  },
  "five_hour": {
    "utilization": 12.8,
    "resets_at": "2026-07-01T15:00:00Z"
  }
}
```

字段说明：

| 字段 | 类型 | 说明 |
|---|---|---|
| `seven_day.utilization` | `float` | 7 天配额使用百分比，如 `45.2` 表示 45.2% |
| `seven_day.resets_at` | `str` | 7 天窗口重置时间，ISO 8601（带 `Z` 或时区偏移） |
| `five_hour.utilization` | `float` | 5 小时会话使用百分比 |
| `five_hour.resets_at` | `str` | 5 小时窗口重置时间，ISO 8601 |

`utilization` 取值范围 0–100，服务端不强制校验上界（允许略超 100 的极端情况如实反映）。

## 实现

### 1. 新增 Pydantic schema（`ai_plan_insight/api_schemas.py`）

```python
class ClaudeWindowPush(BaseModel):
    utilization: float
    resets_at: str


class ClaudePushRequest(BaseModel):
    seven_day: ClaudeWindowPush
    five_hour: ClaudeWindowPush
```

在文件顶部 `api_schemas.py` 的 import 中将 `ClaudePushRequest` 加入 `web.py` 的 import 列表（`ClaudeWindowPush` 仅作内部嵌套，无需在 web.py 导入）。

### 2. 新增端点（`ai_plan_insight/web.py`）

```python
@app.post("/api/push/claude")
async def push_claude(req: ClaudePushRequest):
    global _last_updated, _pushed_results, _pushed_at
    _pushed_results["claude"] = UsageResponse(
        provider="Claude 订阅",
        limits=[
            LimitResponse(
                duration=7,
                time_unit="天",
                limit="100",
                used=str(int(req.seven_day.utilization)),
                remaining=str(int(100 - req.seven_day.utilization)),
                reset_time=req.seven_day.resets_at,
            ),
            LimitResponse(
                duration=5,
                time_unit="小时",
                limit="100",
                used=str(int(req.five_hour.utilization)),
                remaining=str(int(100 - req.five_hour.utilization)),
                reset_time=req.five_hour.resets_at,
            ),
        ],
    )
    _last_updated = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    _pushed_at["claude"] = datetime.now().astimezone()
    return {"status": "ok"}
```

要点：
- provider 名固定为 `Claude 订阅`（不设 `membership_level`，card 右上角不显示档位）。
- 两个窗口各自转成一条 `LimitResponse`，完全沿用 **Antigravity 的写法**（非 `PERCENT` 类型）：`limit="100"`、`used`/`remaining` 为百分比整数字符串，不设 `limit_type`（默认空串，走前端的默认分支）。
- `duration` + `time_unit` 经前端 `formatTimeWindow` 渲染为窗口标签。`time_unit` 用 `TIME_UNIT_LABELS` 里的 key（"天"→"天"、"小时"→"小时"），配合 `duration` 拼成「7 天」/「5 小时」。
- **`used` 用整数**（`str(int(utilization))`），与 Antigravity 一致；前端默认分支用 `parseInt(l.used)*100/parseInt(l.limit)` 重算百分比，浮点会被截断，所以保留整数即可。若未来需要小数精度再切 `PERCENT` 类型（需同步调整 `time_unit` 为纯标签文本，因为 `PERCENT` 分支会把 `time_unit` 原样当标签、忽略 `duration`）。
- 存入 `_pushed_results["claude"]`，受现有 30 分钟 TTL 约束（`get_usage` 中的 `cutoff` 过滤）。

### 3. 排序（`ai_plan_insight/web.py` 的 `_provider_sort_key`）

在排序字典中加入 `"Claude 订阅": 24`，使其排在 Antigravity（22）之后、Cursor（25）之前。

### 4. 前端（`ai_plan_insight/index.html`）

**无需修改。** 现有 limit 卡片渲染逻辑会自动把这两条 limit 画成与 Antigravity 一致的两行（百分比 + 重置时间）。`PERCENT` 类型与 Cursor 共用同一渲染分支。

## curl 示例

```bash
curl -X POST http://localhost:8000/api/push/claude \
  -H "Content-Type: application/json" \
  -d '{
    "seven_day": {
      "utilization": 45.2,
      "resets_at": "2026-07-08T12:00:00Z"
    },
    "five_hour": {
      "utilization": 12.8,
      "resets_at": "2026-07-01T15:00:00Z"
    }
  }'
```

返回：`{"status":"ok"}`

数据在 30 分钟内有效；agent 需在 TTL 内周期性重推（建议每 5–10 分钟一次）。

## 测试

沿用现有 `tests/test_web_*.py` 的模式，新增 `tests/test_web_push_claude.py`：

1. POST 合法 payload 到 `/api/push/claude`，断言返回 `{"status":"ok"}`。
2. GET `/api/usage`，断言结果中存在 `provider == "Claude 订阅"`，且 `limits` 恰好两条：
   - 第一条 `duration=7, time_unit="天"`, `used="45"`, `remaining="55"`, `reset_time` 为上报值，`limit_type=""`。
   - 第二条 `duration=5, time_unit="小时"`, `used="13"`, `remaining="87"`。
3. TTL 过期测试：手动把 `_pushed_at["claude"]` 设为 31 分钟前，GET `/api/usage` 不应再包含该 provider（复用现有 TTL 测试写法）。

## 非目标

- 不引入数据库或持久化（与现有 push 端点一致，纯内存 + TTL）。
- 不新增认证（与现有 push 端点一致）。
- 不支持多账号/多档位区分（provider 名硬编码为单数）。
- 不做历史序列展示（Claude 数据只有当前快照，无时间序列，不接入 history_usage 通道）。
