# Claude 订阅用量推送 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 支持外部 agent 通过 `POST /api/push/claude` 推送 Claude 订阅（Pro/Max）的 7 天配额和 5 小时会话用量，前端自动以两张 limit 卡片展示。

**Architecture:** 完全复用现有 push 模式：外部 agent 推送 → Pydantic schema 校验 → 转成 `UsageResponse` 存入内存字典 `_pushed_results["claude"]` → 受 30 分钟 TTL 约束 → 由 `GET /api/usage` 合并返回。前端无需改动。不引入数据库、不新增认证。

**Tech Stack:** Python 3.12+、FastAPI、Pydantic v2、pytest（使用 `fastapi.testclient.TestClient`）。

---

## File Structure

- **Modify** `ai_plan_insight/api_schemas.py` — 新增 `ClaudeWindowPush` 与 `ClaudePushRequest` 两个 Pydantic 模型。
- **Modify** `ai_plan_insight/web.py` — import `ClaudePushRequest`；新增 `push_claude` 端点；在 `_provider_sort_key` 的 order 字典中加入 `"Claude 订阅": 24`。
- **Create** `tests/test_web_push_claude.py` — 三个测试：合法推送返回 ok、`GET /api/usage` 能取回正确的两条 limit、TTL 过期后不再返回。

---

### Task 1: 新增 Pydantic schema

**Files:**
- Modify: `ai_plan_insight/api_schemas.py`（文件末尾，紧跟 `MimoPushRequest` 之后）

- [ ] **Step 1: 在 `api_schemas.py` 末尾追加两个模型**

在 `ai_plan_insight/api_schemas.py` 文件末尾（`MimoPushRequest` 类定义之后）追加：

```python


class ClaudeWindowPush(BaseModel):
    utilization: float
    resets_at: str


class ClaudePushRequest(BaseModel):
    seven_day: ClaudeWindowPush
    five_hour: ClaudeWindowPush
```

- [ ] **Step 2: 验证 schema 可被导入且校验正确**

Run（在仓库根目录）:

```bash
python -c "
from ai_plan_insight.api_schemas import ClaudePushRequest, ClaudeWindowPush

req = ClaudePushRequest(
    seven_day=ClaudeWindowPush(utilization=45.2, resets_at='2026-07-08T12:00:00Z'),
    five_hour=ClaudeWindowPush(utilization=12.8, resets_at='2026-07-01T15:00:00Z'),
)
assert req.seven_day.utilization == 45.2
assert req.five_hour.resets_at == '2026-07-01T15:00:00Z'
print('schema OK')
"
```

Expected: 输出 `schema OK`，无异常。

- [ ] **Step 3: Commit**

```bash
git add ai_plan_insight/api_schemas.py
git commit -m "feat: add ClaudePushRequest pydantic schema"
```

---

### Task 2: 在 web.py import ClaudePushRequest

**Files:**
- Modify: `ai_plan_insight/web.py:22-33`（`from .api_schemas import (...)` 块）

- [ ] **Step 1: 将 `ClaudePushRequest` 加入 import 列表**

在 `ai_plan_insight/web.py` 第 22–33 行的 import 块中，在 `MimoPushRequest,` 之后新增一行 `ClaudePushRequest,`。修改后该块应为：

```python
from .api_schemas import (
    UsageResponse,
    LimitResponse,
    UsageDetailResponse,
    TokenUsageResponse,
    HistoryModelUsageResponse,
    HistoryUsagePeriodResponse,
    ModelStatResponse,
    AntigravityPushRequest,
    CursorPushRequest,
    MimoPushRequest,
    ClaudePushRequest,
)
```

（注意：`ClaudeWindowPush` 仅作内部嵌套，无需在此导入。）

- [ ] **Step 2: 验证 web 模块仍可正常导入**

Run:

```bash
python -c "import ai_plan_insight.web as web; print('web import OK')"
```

Expected: 输出 `web import OK`，无异常。

- [ ] **Step 3: Commit**

```bash
git add ai_plan_insight/web.py
git commit -m "feat: import ClaudePushRequest in web module"
```

---

### Task 3: 新增 push_claude 端点

**Files:**
- Modify: `ai_plan_insight/web.py`（在 `push_mimo` 端点之后、文件末尾追加）

- [ ] **Step 1: 在 `web.py` 文件末尾追加 `push_claude` 端点**

在 `push_mimo` 函数定义之后追加：

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

要点（与 Antigravity 写法一致，非 `PERCENT` 类型）：
- `provider` 固定为 `Claude 订阅`，不设 `membership_level`（卡片右上角不显示档位）。
- 两条 limit 均不设 `limit_type`（默认空串，走前端默认分支）。
- `used` 用 `str(int(utilization))` 取整（前端默认分支 `parseInt(l.used)*100/parseInt(l.limit)` 会截断浮点，与 Antigravity 一致）。
- `remaining` = `str(int(100 - utilization))`。
- 存入 `_pushed_results["claude"]`，受 `get_usage` 中 30 分钟 TTL 过滤约束。

- [ ] **Step 2: 验证 web 模块导入与路由注册正常**

Run:

```bash
python -c "
import ai_plan_insight.web as web
paths = [r.path for r in web.app.routes if getattr(r, 'path', None) == '/api/push/claude']
assert paths == ['/api/push/claude'], paths
print('route registered OK')
"
```

Expected: 输出 `route registered OK`，无异常。

- [ ] **Step 3: Commit**

```bash
git add ai_plan_insight/web.py
git commit -m "feat: add POST /api/push/claude endpoint"
```

---

### Task 4: 调整 provider 排序

**Files:**
- Modify: `ai_plan_insight/web.py:200-214`（`_provider_sort_key` 函数内的 `order` 字典）

- [ ] **Step 1: 在 order 字典中加入 `"Claude 订阅": 24`**

在 `_provider_sort_key` 函数的 `order` 字典中，在 `"Antigravity": 22,` 之后新增 `"Claude 订阅": 24,`。使其排在 Antigravity (22) 与 Cursor (25) 之间。修改后该字典前部应为：

```python
    order = {
        "自购 Codex 中转站": 10,
        "白嫖 Codex Security 中转": 11,
        "火山方舟 Coding Plan": 12,
        "GLM Coding Plan": 20,
        "白嫖 GLM Coding Plan 国际版": 21,
        "Antigravity": 22,
        "小米 MiMo Token Plan": 23,
        "Claude 订阅": 24,
        "Cursor": 25,
        "Kimi Coding Plan": 30,
        "华为云余额": 35,
        "AIPing": 50,
    }
```

- [ ] **Step 2: 验证排序键正确**

Run:

```bash
python -c "
import ai_plan_insight.web as web
from ai_plan_insight.api_schemas import UsageResponse
assert web._provider_sort_key(UsageResponse(provider='Claude 订阅')) == 24
assert web._provider_sort_key(UsageResponse(provider='Antigravity')) == 22
assert web._provider_sort_key(UsageResponse(provider='Cursor')) == 25
print('sort key OK')
"
```

Expected: 输出 `sort key OK`，无异常。

- [ ] **Step 3: Commit**

```bash
git add ai_plan_insight/web.py
git commit -m "feat: order Claude 订阅 between Antigravity and Cursor"
```

---

### Task 5: 编写端点测试（先写失败测试）

**Files:**
- Create: `tests/test_web_push_claude.py`

- [ ] **Step 1: 创建测试文件，写入三个失败测试**

创建 `tests/test_web_push_claude.py`：

```python
from datetime import datetime, timedelta

from fastapi.testclient import TestClient

import ai_plan_insight.web as web


VALID_PAYLOAD = {
    "seven_day": {
        "utilization": 45.2,
        "resets_at": "2026-07-08T12:00:00Z",
    },
    "five_hour": {
        "utilization": 12.8,
        "resets_at": "2026-07-01T15:00:00Z",
    },
}


def _reset_push_state():
    web._pushed_results.clear()
    web._pushed_at.clear()


def test_push_claude_returns_ok():
    _reset_push_state()
    client = TestClient(web.app)

    resp = client.post("/api/push/claude", json=VALID_PAYLOAD)

    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_usage_returns_two_claude_limits_after_push():
    _reset_push_state()
    client = TestClient(web.app)

    client.post("/api/push/claude", json=VALID_PAYLOAD)
    resp = client.get("/api/usage")

    assert resp.status_code == 200
    providers = [u for u in resp.json() if u["provider"] == "Claude 订阅"]
    assert len(providers) == 1
    limits = providers[0]["limits"]
    assert len(limits) == 2

    seven = limits[0]
    assert seven["duration"] == 7
    assert seven["time_unit"] == "天"
    assert seven["limit"] == "100"
    assert seven["used"] == "45"
    # remaining = str(int(100 - 45.2)) = str(int(54.8)) = "54"（沿用 Antigravity 的截断写法）
    # 注意：设计文档测试叙述里写的 "55" 是笔误，与设计给出的实现代码矛盾，此处以实现为准。
    assert seven["remaining"] == "54"
    assert seven["reset_time"] == "2026-07-08T12:00:00Z"
    assert seven["limit_type"] == ""

    five = limits[1]
    assert five["duration"] == 5
    assert five["time_unit"] == "小时"
    assert five["limit"] == "100"
    # used = str(int(12.8)) = "12"（截断，非四舍五入；设计文档叙述里的 "13" 是笔误）
    assert five["used"] == "12"
    assert five["remaining"] == "87"
    assert five["reset_time"] == "2026-07-01T15:00:00Z"
    assert five["limit_type"] == ""


def test_expired_push_is_not_returned():
    _reset_push_state()
    client = TestClient(web.app)

    client.post("/api/push/claude", json=VALID_PAYLOAD)
    # 手动把推送时间改为 31 分钟前，模拟 TTL 过期
    web._pushed_at["claude"] = datetime.now().astimezone() - timedelta(minutes=31)

    resp = client.get("/api/usage")

    assert resp.status_code == 200
    providers = [u for u in resp.json() if u["provider"] == "Claude 订阅"]
    assert providers == []
```

- [ ] **Step 2: 运行测试，确认通过**

Run:

```bash
python -m pytest tests/test_web_push_claude.py -v
```

Expected: 3 passed（`test_push_claude_returns_ok`、`test_usage_returns_two_claude_limits_after_push`、`test_expired_push_is_not_returned`）。端点与 schema 已在前序任务实现，此处应直接通过。

- [ ] **Step 3: 运行全量测试，确认未破坏既有功能**

Run:

```bash
python -m pytest -q
```

Expected: 全部测试通过（含既有 4 个测试文件 + 新增 1 个）。

- [ ] **Step 4: Commit**

```bash
git add tests/test_web_push_claude.py
git commit -m "test: cover Claude push endpoint, usage retrieval, and TTL expiry"
```

---

### Task 6: 端到端 curl 冒烟验证

**Files:**
- 无（手动验证）

- [ ] **Step 1: 启动服务并 curl 验证**

启动服务（后台或新终端）:

```bash
python -m ai_plan_insight
```

执行 curl:

```bash
curl -X POST http://localhost:8000/api/push/claude \
  -H "Content-Type: application/json" \
  -d '{
    "seven_day": {"utilization": 45.2, "resets_at": "2026-07-08T12:00:00Z"},
    "five_hour": {"utilization": 12.8, "resets_at": "2026-07-01T15:00:00Z"}
  }'
```

Expected: 返回 `{"status":"ok"}`。

- [ ] **Step 2: 浏览器打开 `http://localhost:8000`，确认「Claude 订阅」卡片显示两行（7 天 / 5 小时）百分比与重置时间，且排在 Antigravity 之后、Cursor 之前。**

Expected: 卡片渲染正确，无报错。
