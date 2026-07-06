# Push API 认证设计

## 背景

AI Plan Insight 的 Web 服务目前通过 `/api/push/*` 和 `/api/usage/report` 接收外部 Agent 推送的用量数据。这些端点当前没有认证，任何能访问服务的人都可以推送。

本设计引入一个可选的 Bearer Token 认证机制，分阶段启用：

1. 先实现认证，但不强制开启，只记录每个 source 的认证状态。
2. 等所有 Agent 客户端都更新为带 Token 调用后，再开启强制认证。

## 目标

- 所有推送端点支持 `Authorization: Bearer <token>` 认证。
- 认证状态持久化，服务重启不丢失。
- 通过一个配置开关在"软验证"和"硬验证"之间切换。
- 提供管理端点查看各 source 是否已正确认证。
- 给出统一的客户端改动说明和请求示例，方便更新多个 Agent。

## 方案概述

采用**全局固定 secret + 持久化认证状态 + 软/硬开关**方案：

- `config.json` 顶层新增 `push_auth_secret` 和 `enforce_push_auth`。
- 所有推送端点统一检查 `Authorization` 头。
- 认证状态记录在 SQLite `source` 表的 `auth_valid` 列。
- `enforce_push_auth` 为 `false` 时，认证失败仍处理请求，但标记状态；为 `true` 时直接返回 401。

## 配置

在 `config.json` 顶层新增两个字段：

```json
{
  "providers": { ... },
  "push_auth_secret": "ai-plan-insight-push-token-2026",
  "enforce_push_auth": false
}
```

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `push_auth_secret` | string | 否 | 全局 Bearer Token。不填则任何请求都无法通过认证（除非关闭强制验证）。 |
| `enforce_push_auth` | bool | 否 | 默认 `false`。`false` 为软验证，记录状态但不拦截；`true` 为硬验证，失败直接 401。 |

配置通过现有的 `load_config` 热加载，无需重启服务。

## 数据库变更

在 `usage_store.init_schema` 中给 `source` 表增加两列（已存在则跳过）：

```sql
ALTER TABLE source ADD COLUMN auth_valid INTEGER NOT NULL DEFAULT 0
ALTER TABLE source ADD COLUMN last_auth_at TEXT
```

新增辅助函数：

- `usage_store.update_source_auth(conn, source_id, is_valid, now)`
  - 写入 `auth_valid` 和 `last_auth_at`。
- `usage_store.get_source_auth_status(conn) -> list[dict]`
  - 返回所有 source 的认证状态，供管理端点使用。

## 认证检查函数

新增 `web._verify_push_auth(request, source_id)`：

1. 读取 `load_config(_config_path)`。
2. 若 `push_auth_secret` 为空或不存在，返回 `is_valid=False`。
3. 从 `Authorization` 头解析 `Bearer <token>`，忽略大小写和首尾空格。
4. 使用 `secrets.compare_digest` 进行常量时间比较。
5. 若通过，`is_valid=True`；否则 `is_valid=False`。

返回结构：

```python
(is_valid: bool, reason: str | None)
```

`reason` 用于日志和错误响应，例如 `"missing"`、`"malformed"`、`"invalid"`。

## 端点改造

### 受影响的端点

- `POST /api/push/antigravity` → source_id 固定为 `antigravity`
- `POST /api/push/cursor` → source_id 固定为 `cursor`
- `POST /api/push/mimo` → source_id 固定为 `mimo_token_plan`
- `POST /api/push/claude` → source_id 固定为 `claude`
- `POST /api/usage/report` → source_id 来自请求体 `source_id`

### 行为

#### 软验证模式 (`enforce_push_auth = false`)

- Token 正确：
  - 处理请求并返回 200。
  - 更新 `source.auth_valid = true`。
- Token 缺失或错误：
  - 处理请求并返回 200（保持兼容）。
  - 更新 `source.auth_valid = false`。
  - 打印 warning 日志：`push auth failed for <source_id>: <reason>`。

#### 硬验证模式 (`enforce_push_auth = true`)

- Token 正确：
  - 处理请求，返回 200。
  - 更新 `source.auth_valid = true`。
- Token 缺失或错误：
  - 直接返回 401，不处理请求，不写库。
  - 响应体：`{"detail": "Unauthorized"}` 或更具体的 `reason`。

### 新增管理端点

`GET /api/admin/sources`

返回所有 source 的认证状态：

```json
[
  {
    "source_id": "my-agent",
    "label": "My Agent",
    "last_seen": "2026-07-06T12:00:00+08:00",
    "auth_valid": true,
    "last_auth_at": "2026-07-06T12:00:00+08:00"
  },
  {
    "source_id": "cursor",
    "label": null,
    "last_seen": "2026-07-06T11:00:00+08:00",
    "auth_valid": false,
    "last_auth_at": "2026-07-06T11:00:00+08:00"
  }
]
```

用于确认所有 Agent 已切换完成，之后再将 `enforce_push_auth` 改为 `true`。

## 错误处理

| 场景 | 软验证 | 硬验证 |
|---|---|---|
| 缺少 `Authorization` 头 | 200 + 标记 `auth_valid=false` | 401 |
| 头格式错误（非 Bearer） | 200 + 标记 `auth_valid=false` | 401 |
| Token 不匹配 | 200 + 标记 `auth_valid=false` | 401 |
| 请求体非法 | 422（pydantic） | 401 先拦截，不会到达 422 |

## 测试要点

- 软验证：错误 token 仍返回 200，但 `auth_valid=false`。
- 硬验证：错误 token 返回 401，不写库。
- 正确 token 在两种模式下都返回 200，且 `auth_valid=true`。
- `/api/admin/sources` 正确列出状态。
- 数据库列迁移对旧库兼容。

## 客户端改动指南

所有调用 Push API 的 Agent 都需要在 HTTP 请求中增加 `Authorization` 头：

```python
import requests

PUSH_SECRET = "ai-plan-insight-push-token-2026"  # 与 config.json 中的 push_auth_secret 一致

requests.post(
    "http://localhost:8000/api/usage/report",
    headers={
        "Authorization": f"Bearer {PUSH_SECRET}",
        "Content-Type": "application/json",
    },
    json={...},
)
```

对于 `/api/push/*` 端点同理：

```python
requests.post(
    "http://localhost:8000/api/push/cursor",
    headers={"Authorization": f"Bearer {PUSH_SECRET}"},
    json={...},
)
```

### 给 Agent 维护者的提示词

> 你正在维护一个向 AI Plan Insight 推送用量数据的 Agent。该服务现在支持可选的 Bearer Token 认证，未来会强制开启。
>
> 请修改 Agent 的 HTTP 客户端，在向以下端点发送 POST 请求时，添加请求头：
> - `Authorization: Bearer <PUSH_SECRET>`
>
> 受影响的端点包括：
> - `/api/push/antigravity`
> - `/api/push/cursor`
> - `/api/push/mimo`
> - `/api/push/claude`
> - `/api/usage/report`
>
> 其中 `<PUSH_SECRET>` 是用户 `config.json` 中的 `push_auth_secret` 字段，Agent 应从环境变量或配置文件中读取，不要硬编码。
> 当前阶段不强制认证，但缺失 Token 会被记录为未认证；未来服务端开启强制认证后，未带 Token 的请求将返回 401。

## 请求示例

### 带认证推送 Cursor 用量

```bash
curl -X POST http://localhost:8000/api/push/cursor \
  -H "Authorization: Bearer ai-plan-insight-push-token-2026" \
  -H "Content-Type: application/json" \
  -d '{
    "membership": "Pro",
    "autoPercentUsed": 45.5,
    "apiPercentUsed": 12.3,
    "billingEnd": "2026-07-01T00:00:00Z"
  }'
```

### 带认证推送模型 Token 用量

```bash
curl -X POST http://localhost:8000/api/usage/report \
  -H "Authorization: Bearer ai-plan-insight-push-token-2026" \
  -H "Content-Type: application/json" \
  -d '{
    "source_id": "my-agent",
    "source_label": "My Agent",
    "reported_at": "2026-07-03",
    "points": [
      {
        "date": "2026-07-03",
        "model_id": "claude-3-5-sonnet",
        "input_tokens": 15000,
        "output_tokens": 8000,
        "cache_read_tokens": 5000,
        "cache_write_tokens": 2000,
        "reasoning_tokens": 1000
      }
    ]
  }'
```

### 查询各 source 认证状态

```bash
curl http://localhost:8000/api/admin/sources
```

## 迁移计划

1. 服务端实现本设计中的认证逻辑和数据库变更。
2. 更新 `config.json.example`，添加 `push_auth_secret` 和 `enforce_push_auth` 示例。
3. 更新 `README.md`，在 Push API 文档中说明认证头和示例。
4. 给每个 Agent 仓库发起 PR/修改，添加 `Authorization` 头。
5. 观察 `/api/admin/sources`，确认所有 source 的 `auth_valid` 均为 `true`。
6. 将 `enforce_push_auth` 改为 `true`，完成强制认证切换。

## 决策记录

- 采用全局固定 secret，而非 per-source secret，因为当前阶段目标是快速迁移多个 Agent，不希望增加配置分发成本。
- 使用 `secrets.compare_digest` 防止时序攻击。
- 软验证模式下仍返回 200，避免在 Agent 更新期间中断现有数据推送。
- 认证状态持久化在 `source` 表，而不是内存，因为需要跨重启观察迁移进度。
