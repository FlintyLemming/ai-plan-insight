# Grok 订阅用量推送 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 支持外部 agent 通过 `POST /api/push/grok` 推送 Grok 订阅周配额用量，在 web 面板上以一张 limit 卡片展示（百分比 + 重置时间，可选 plan 档位）。

**Architecture:** 完全复用现有 push 模式：外部 agent 推送 → Pydantic schema 校验 → 转成 `UsageResponse` 存入内存字典 `_pushed_results["grok"]` → 受 30 分钟 TTL 约束 → 由 `GET /api/usage` 合并返回并由现有前端渲染。走统一 `_handle_push_auth(request, "grok")`。不新增数据库表、不新增前端渲染分支、不服务端直连 Grok API。

**Tech Stack:** Python 3.12+、FastAPI、Pydantic v2、pytest（`fastapi.testclient.TestClient`）。

## Global Constraints

- Provider 显示名硬编码为 **`Grok 订阅`**；push key / `source_id` 为 **`grok`**。
- 仅支持单条周配额 limit：`duration=7`、`time_unit="天"`、`limit="100"`、`used`/`remaining` 为百分比整数字符串、`limit_type` 默认空串。
- `weekly` 必填；缺 `weekly` 或子字段 → Pydantic 422。不接受「仅 plan、无 weekly」。
- `plan` 可选；strip 后为空串则视为未提供（`membership_level=None`）。
- 不强制 `utilization` 上界（允许略超 100 如实反映）。
- 使用既有 `PUSH_TTL_SECONDS = 30 * 60`；不新增 TTL 常量。
- 卡片排序走 config `order`（`_build_display_order_map`），**不**再引入硬编码 sort dict。
- `_PUSH_ONLY_PROVIDERS` 与 `__main__.py` 的 `push_only` 必须同步加入 `"grok"`。
- 前端 `index.html` **无需修改**。
- 客户端 agent 不在本计划范围。

---

## File Structure

| File | Responsibility |
|------|----------------|
| `ai_plan_insight/api_schemas.py` | 新增 `GrokWindowPush`、`GrokPushRequest`（与 Claude 平行，不复用 `ClaudeWindowPush` 类型名）。 |
| `ai_plan_insight/web.py` | import `GrokPushRequest`；`POST /api/push/grok`；`_PUSH_ONLY_PROVIDERS` / `_PROVIDER_DISPLAY_NAMES` 注册。 |
| `ai_plan_insight/__main__.py` | CLI 的 `push_only` 集合加入 `"grok"`，避免 config 仅带 `order` 时 fetch 崩溃。 |
| `config.json.example` | 增加 `"grok": { "order": 11 }`（在 Claude `order: 12` 前，两张订阅卡相邻）。 |
| `tests/test_web_push_grok.py` | 对齐 `tests/test_web_push_claude.py`：合法推送、usage 映射、plan 处理、422、TTL、soft auth。 |
| `README.md` | 支持列表 + Push API 文档补上 Grok（与其他 push 源文档 parity）。 |
| `ai_plan_insight/index.html` | **不修改**。 |

---

### Task 1: 新增 Pydantic schema

**Files:**
- Modify: `ai_plan_insight/api_schemas.py`（在 `ClaudePushRequest` 之后、`from pydantic import Field` 之前追加）

**Interfaces:**
- Consumes: `pydantic.BaseModel`（文件顶部已 import）。
- Produces:
  - `class GrokWindowPush(BaseModel): utilization: float; resets_at: str`
  - `class GrokPushRequest(BaseModel): weekly: GrokWindowPush; plan: str | None = None`

- [ ] **Step 1: 在 `api_schemas.py` 中追加两个模型**

在 `ClaudePushRequest` 类定义之后、`from pydantic import Field` 之前插入：

```python
class GrokWindowPush(BaseModel):
    utilization: float
    resets_at: str


class GrokPushRequest(BaseModel):
    weekly: GrokWindowPush
    plan: str | None = None
```

插入后该区域应为：

```python
class ClaudePushRequest(BaseModel):
    seven_day: ClaudeWindowPush
    five_hour: ClaudeWindowPush


class GrokWindowPush(BaseModel):
    utilization: float
    resets_at: str


class GrokPushRequest(BaseModel):
    weekly: GrokWindowPush
    plan: str | None = None


from pydantic import Field
```

- [ ] **Step 2: 验证 schema 可导入且校验正确**

Run（在仓库根目录，使用项目 venv / `uv run` 均可）:

```bash
uv run python -c "
from ai_plan_insight.api_schemas import GrokPushRequest, GrokWindowPush
from pydantic import ValidationError

req = GrokPushRequest(
    weekly=GrokWindowPush(utilization=45.2, resets_at='2026-07-10T04:01:09Z'),
    plan='SuperGrok',
)
assert req.weekly.utilization == 45.2
assert req.weekly.resets_at == '2026-07-10T04:01:09Z'
assert req.plan == 'SuperGrok'

req2 = GrokPushRequest(weekly=GrokWindowPush(utilization=12.0, resets_at='2026-07-10T04:01:09Z'))
assert req2.plan is None

try:
    GrokPushRequest.model_validate({'plan': 'SuperGrok'})
    raise SystemExit('expected ValidationError for missing weekly')
except ValidationError:
    pass

print('schema OK')
"
```

Expected: 输出 `schema OK`，无异常。

- [ ] **Step 3: Commit**

```bash
git add ai_plan_insight/api_schemas.py
git commit -m "feat: add GrokPushRequest pydantic schema"
```

---

### Task 2: 编写失败的集成测试

**Files:**
- Create: `tests/test_web_push_grok.py`

**Interfaces:**
- Consumes（期望 Task 3 实现后可用）:
  - `POST /api/push/grok` body: `{"weekly": {"utilization": float, "resets_at": str}, "plan"?: str | null}`
  - 返回 `{"status": "ok"}`
  - `GET /api/usage` 中出现 `provider == "Grok 订阅"`
  - soft auth：`_handle_push_auth(request, "grok")` → `source` 表 `source_id=="grok"`
- Produces: 7 个测试用例，全部对当前代码应失败（缺路由 / 缺映射）。

- [ ] **Step 1: 创建 `tests/test_web_push_grok.py`**

写入完整文件内容：

```python
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

import ai_plan_insight.web as web
from ai_plan_insight import usage_store
from ai_plan_insight.config import Config


VALID_PAYLOAD = {
    "weekly": {"utilization": 45.2, "resets_at": "2026-07-10T04:01:09Z"},
    "plan": "SuperGrok",
}


def _reset_push_state(tmp_path, monkeypatch):
    """Clear in-memory push state and point the web layer at a fresh temp DB."""
    web._pushed_results.clear()
    web._pushed_at.clear()
    db = tmp_path / "usage.db"
    monkeypatch.setattr(web, "_usage_db_path", db)
    usage_store.init_db(db)
    monkeypatch.setattr(
        web,
        "load_config",
        lambda _=None: Config(
            providers={}, push_auth_secret="abc", enforce_push_auth=False
        ),
    )


def test_push_grok_returns_ok(tmp_path, monkeypatch):
    _reset_push_state(tmp_path, monkeypatch)
    client = TestClient(web.app)
    resp = client.post("/api/push/grok", json=VALID_PAYLOAD)
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_usage_returns_grok_limit_after_push(tmp_path, monkeypatch):
    _reset_push_state(tmp_path, monkeypatch)
    client = TestClient(web.app)

    client.post("/api/push/grok", json=VALID_PAYLOAD)
    resp = client.get("/api/usage")

    assert resp.status_code == 200
    providers = [u for u in resp.json() if u["provider"] == "Grok 订阅"]
    assert len(providers) == 1
    card = providers[0]
    assert card["membership_level"] == "SuperGrok"
    limits = card["limits"]
    assert len(limits) == 1
    limit = limits[0]
    assert limit["duration"] == 7
    assert limit["time_unit"] == "天"
    assert limit["limit"] == "100"
    assert limit["used"] == "45"  # int(45.2)
    assert limit["remaining"] == "54"  # int(100 - 45.2)
    assert limit["reset_time"] == "2026-07-10T04:01:09Z"
    assert limit["limit_type"] == ""


def test_push_grok_without_plan_has_null_membership(tmp_path, monkeypatch):
    _reset_push_state(tmp_path, monkeypatch)
    client = TestClient(web.app)

    payload = {
        "weekly": {"utilization": 12.0, "resets_at": "2026-07-10T04:01:09Z"},
    }
    client.post("/api/push/grok", json=payload)
    resp = client.get("/api/usage")
    providers = [u for u in resp.json() if u["provider"] == "Grok 订阅"]
    assert len(providers) == 1
    assert providers[0]["membership_level"] is None


def test_push_grok_blank_plan_treated_as_missing(tmp_path, monkeypatch):
    _reset_push_state(tmp_path, monkeypatch)
    client = TestClient(web.app)

    payload = {
        "weekly": {"utilization": 12.0, "resets_at": "2026-07-10T04:01:09Z"},
        "plan": "  ",
    }
    client.post("/api/push/grok", json=payload)
    resp = client.get("/api/usage")
    providers = [u for u in resp.json() if u["provider"] == "Grok 订阅"]
    assert len(providers) == 1
    assert providers[0]["membership_level"] is None


def test_push_grok_missing_weekly_returns_422(tmp_path, monkeypatch):
    _reset_push_state(tmp_path, monkeypatch)
    client = TestClient(web.app)

    resp = client.post("/api/push/grok", json={"plan": "SuperGrok"})
    assert resp.status_code == 422


def test_expired_grok_push_is_not_returned(tmp_path, monkeypatch):
    _reset_push_state(tmp_path, monkeypatch)
    client = TestClient(web.app)

    client.post("/api/push/grok", json=VALID_PAYLOAD)
    web._pushed_at["grok"] = datetime.now().astimezone() - timedelta(minutes=31)

    resp = client.get("/api/usage")

    assert resp.status_code == 200
    providers = [u for u in resp.json() if u["provider"] == "Grok 订阅"]
    assert providers == []


def test_push_grok_without_token_records_auth_invalid(tmp_path, monkeypatch):
    _reset_push_state(tmp_path, monkeypatch)
    client = TestClient(web.app)
    resp = client.post("/api/push/grok", json=VALID_PAYLOAD)
    assert resp.status_code == 200
    conn = sqlite3.connect(tmp_path / "usage.db")
    rows = usage_store.get_source_auth_status(conn)
    grok = [r for r in rows if r["source_id"] == "grok"]
    assert grok and grok[0]["auth_valid"] is False
```

- [ ] **Step 2: 运行测试，确认失败（端点尚未实现）**

Run:

```bash
uv run pytest tests/test_web_push_grok.py -v
```

Expected: FAIL — 多数用例因 `404`（路由不存在）或 assert 失败；**不得**全部 PASS。

- [ ] **Step 3: Commit（红灯测试入库）**

```bash
git add tests/test_web_push_grok.py
git commit -m "test: add failing tests for Grok push endpoint"
```

---

### Task 3: 实现 push 端点与 push-only 注册

**Files:**
- Modify: `ai_plan_insight/web.py`（import、`_PUSH_ONLY_PROVIDERS`、`_PROVIDER_DISPLAY_NAMES`、新增 `push_grok`）
- Modify: `ai_plan_insight/__main__.py`（`push_only` 集合）
- Test: `tests/test_web_push_grok.py`

**Interfaces:**
- Consumes: `GrokPushRequest`（Task 1）；`_handle_push_auth(request, source_id)`；`UsageResponse` / `LimitResponse`；`PUSH_TTL_SECONDS` 过滤逻辑（已有）。
- Produces:
  - `POST /api/push/grok` → `{"status": "ok"}`
  - `_pushed_results["grok"]` 为 `UsageResponse(provider="Grok 订阅", membership_level=plan|None, limits=[一条 7 天 limit])`
  - `_PUSH_ONLY_PROVIDERS` 含 `"grok"`；`_PROVIDER_DISPLAY_NAMES["grok"] == "Grok 订阅"`
  - CLI `push_only` 含 `"grok"`

- [ ] **Step 1: 将 `GrokPushRequest` 加入 `web.py` import**

在 `ai_plan_insight/web.py` 的第二段 `from .api_schemas import (...)` 中，在 `ClaudePushRequest,` 之后新增 `GrokPushRequest,`。修改后该块应为：

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
    GrokPushRequest,
)
```

（`GrokWindowPush` 仅作嵌套，无需在此导入。）

- [ ] **Step 2: 注册 push-only 与显示名**

将 `_PUSH_ONLY_PROVIDERS` 改为：

```python
_PUSH_ONLY_PROVIDERS = {"cursor", "claude", "mimo_token_plan", "grok"}
```

在 `_PROVIDER_DISPLAY_NAMES` 中，于 `"claude": "Claude 订阅",` 附近增加：

```python
    "claude": "Claude 订阅",
    "grok": "Grok 订阅",
    "mimo_token_plan": "小米 MiMo Token Plan",
```

（键顺序不强制；确保存在 `"grok": "Grok 订阅"` 即可。若原文件中 `mimo_token_plan` 已在 `claude` 后，直接在 `claude` 行后插入 `"grok"` 行。）

- [ ] **Step 3: 在 `push_claude` 之后追加 `push_grok` 端点**

紧接在 `push_claude` 函数（`return {"status": "ok"}` 与下一路由 `@app.post("/api/usage/report")` 之间）插入：

```python
@app.post("/api/push/grok")
async def push_grok(req: GrokPushRequest, request: Request):
    _handle_push_auth(request, "grok")
    global _last_updated, _pushed_results, _pushed_at
    plan = (req.plan or "").strip() or None
    _pushed_results["grok"] = UsageResponse(
        provider="Grok 订阅",
        membership_level=plan,
        limits=[
            LimitResponse(
                duration=7,
                time_unit="天",
                limit="100",
                used=str(int(req.weekly.utilization)),
                remaining=str(int(100 - req.weekly.utilization)),
                reset_time=req.weekly.resets_at,
            ),
        ],
    )
    _last_updated = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    _pushed_at["grok"] = datetime.now().astimezone()
    return {"status": "ok"}
```

要点：
- `source_id` 必须是 `"grok"`（auth 状态表与 TTL key 一致）。
- `used` / `remaining` 用 `int(...)` 截断浮点（`45.2` → `"45"` / `"54"`），与 Claude/Antigravity 一致。
- 不设 `limit_type`（默认 `""`）。
- `plan` strip 后空串 → `None`。

- [ ] **Step 4: 同步 `__main__.py` 的 `push_only`**

将 `ai_plan_insight/__main__.py` 中：

```python
    push_only = {"cursor", "claude", "mimo_token_plan"}
```

改为：

```python
    push_only = {"cursor", "claude", "mimo_token_plan", "grok"}
```

- [ ] **Step 5: 运行集成测试，确认全部通过**

Run:

```bash
uv run pytest tests/test_web_push_grok.py -v
```

Expected: 7 passed。

- [ ] **Step 6: 快速回归既有 Claude push / push-auth 测试**

Run:

```bash
uv run pytest tests/test_web_push_claude.py tests/test_push_auth.py -q --tb=short
```

Expected: 全部 PASS（本任务未改 Claude/auth 行为）。

- [ ] **Step 7: Commit**

```bash
git add ai_plan_insight/web.py ai_plan_insight/__main__.py
git commit -m "feat: add POST /api/push/grok endpoint and push-only registration"
```

---

### Task 4: 配置示例与 README

**Files:**
- Modify: `config.json.example`
- Modify: `README.md`

**Interfaces:**
- Consumes: 无代码接口；config key `"grok"` 可选，仅影响卡片 `order`（缺省 999）。
- Produces: 示例配置与文档中的 Grok push 契约与 curl。

- [ ] **Step 1: 在 `config.json.example` 增加 grok 条目**

在 `"claude": { "order": 12 }` **之前**插入（使 order 11 的 Grok 与 order 12 的 Claude 相邻）：

```json
    "grok": {
      "order": 11
    },
    "claude": {
      "order": 12
    }
```

完整 `providers` 尾部在改动后应包含（与现有其它条目并存）：

```json
    "cursor": {
      "order": 25
    },
    "grok": {
      "order": 11
    },
    "claude": {
      "order": 12
    }
```

- [ ] **Step 2: 更新 README 支持列表**

在 `README.md` 支持列表表格中，于 Claude 订阅行附近增加一行（Agent 列可暂写「需要本地 Agent」；agent 仓库链接本版可不填或写「另做」）：

```markdown
| Grok 订阅 | 🤖 需要本地 Agent | 通过 Agent 抓取周配额后推送到面板 | — |
```

- [ ] **Step 3: 更新 README API 列表与 Push 说明**

1. 在「Web 模式下提供以下接口」列表中，于 Claude 行后增加：

```markdown
- `POST /api/push/grok` — 接收 Grok 订阅的用量推送
```

2. 将「对于通过 Push API 推送数据的 Provider（Cursor、MiMo、Antigravity、Claude）」改为包含 Grok，例如：

```markdown
对于通过 Push API 推送数据的 Provider（Cursor、MiMo、Antigravity、Claude、Grok），数据保留 30 分钟。若 30 分钟内未收到新的推送，对应区块将从页面消失，直到再次推送。
```

3. 在「#### 推送 Claude 订阅用量」小节之后、「#### 推送模型 Token 用量」之前，插入以下内容（先写标题与说明，再写 bash 代码块）：

标题与说明：

```
#### 推送 Grok 订阅用量

传入 `weekly` 的用量百分比 (`utilization`) 和重置时间 (`resets_at`，ISO8601 字符串)。可选 `plan`（订阅档位展示名，如 `SuperGrok`）。
```

紧随其后的 bash 示例块（作为 README 中的 fenced code block）：

```bash
curl -X POST http://localhost:8000/api/push/grok \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <push_auth_secret>" \
  -d '{
    "weekly": {
      "utilization": 45.2,
      "resets_at": "2026-07-10T04:01:09Z"
    },
    "plan": "SuperGrok"
  }'
```

示例块之后再追加一句说明：

```
无 plan 时可省略该字段；仅空白的 `plan` 会被忽略。缺少 `weekly` 会返回 422。
```

4. 若 README 后文有 push 端点清单（含 Claude），同样补上 `- POST /api/push/grok`。

- [ ] **Step 4: 再跑一遍 Grok 测试（文档改动不应影响代码）**

Run:

```bash
uv run pytest tests/test_web_push_grok.py -q
```

Expected: 7 passed。

- [ ] **Step 5: Commit**

```bash
git add config.json.example README.md
git commit -m "docs: document Grok push API and config example"
```

---

## Self-Review

### 1. Spec coverage

| 设计要求 | 对应任务 |
|----------|----------|
| `GrokWindowPush` / `GrokPushRequest` | Task 1 |
| `POST /api/push/grok` + 映射到单条 7 天 limit | Task 3 |
| `plan` strip / 可选 membership | Task 3 + Task 2 测试 |
| 缺 `weekly` → 422 | Task 2 |
| `_PUSH_ONLY_PROVIDERS` / display names / `__main__.py` push_only | Task 3 |
| `config.json.example` order 11 | Task 4 |
| 30 分钟 TTL | Task 2（复用既有 `PUSH_TTL_SECONDS`） |
| soft auth `source_id=="grok"` | Task 2 + Task 3 `_handle_push_auth` |
| 前端不改 | File Structure 标明 |
| 非目标（Extra Usage、历史、直连 API 等） | 未列入任何任务 |

### 2. Placeholder scan

无 TBD / “add appropriate error handling” / “similar to Task N” 式占位；端点与测试代码完整给出。

### 3. Type consistency

- key / source_id：一律 `"grok"`
- 显示名：一律 `"Grok 订阅"`
- Schema 字段：`weekly.utilization`、`weekly.resets_at`、`plan`
- limit：`duration=7`、`time_unit="天"`、`limit="100"`、`used`/`remaining` 为 `str(int(...))`
- 响应：`{"status": "ok"}`

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-07-09-grok-subscription-push.md`.

**Two execution options:**

1. **Subagent-Driven (recommended)** — dispatch a fresh subagent per task, review between tasks, fast iteration  
   → uses `superpowers:subagent-driven-development`

2. **Inline Execution** — execute tasks in this session with `superpowers:executing-plans`, batch with checkpoints  

**Which approach?**
