# Provider 多实例 v2 设计

- 日期：2026-07-14
- 状态：设计已确认，待实施计划
- 主仓库：`ai-plan-insight`
- 相关客户端：各 Push agent；已知需要补充认证能力的是 `claude-sub-agent`

## 1. 背景

当前 `providers` 配置的 key 同时承担服务类别和唯一实例身份。例如
`claude` 既表示 Claude 服务类型，也表示唯一一张 Claude 卡片。相同耦合还存在于：

- fetch Provider 的构造与调度；
- `/api/push/claude` 等固定类别路由；
- `_cached_results`、`_pushed_results`、失败计数和 TTL；
- `push_card_snapshot.push_key`；
- 卡片排序和前端用 `provider` 展示名维护的状态。

因此两个设备即使分别监控两个 Claude 订阅，后一次推送也会覆盖前一次。其他 Push
服务以及 fetch Provider 同样无法配置多个账号。

本次采用新旧并行方案。现有订阅余额系统保持不变，另建 Provider 多实例 v2，并在页面
增加“订阅余额（新）”tab。用户可以逐个迁移服务，迁移期间两套系统同时工作。

## 2. 目标

1. 所有 Provider，无论 fetch 还是 push，都支持同一服务类型的多个独立实例。
2. 每个实例显示为一张独立卡片，例如“Claude 订阅 · 个人号”和
   “Claude 订阅 · 工作号”。
3. 实例必须在 v2 服务端配置中预先声明，未知实例不得自动注册。
4. 实例 ID、服务类型和展示标签分离：
   - `instance_id` 是稳定机器身份；
   - `type` 决定服务实现和 payload schema；
   - `label` 是用户可修改的实例展示标签。
5. 一个实例只能使用一种采集模式：`fetch` 或 `push`。
6. v2 内所有 Push 实例继续共用一个全局 token，不引入实例级 token。
7. 旧系统、旧配置、旧 API、旧数据库表和旧 UI 行为保持可用。
8. 模型 Token 用量统计完全不受影响。

## 3. 非目标

- 不迁移旧 Provider 卡片快照或卡片历史到 v2。
- 不修改 `usage_point`、`source`、`/api/usage/report`、模型用量图表或模型用量表格。
- 不删除旧“订阅余额”tab 或旧 Push API。
- 不自动转换旧配置文件；用户将按 v2 规范另行生成配置。
- 不允许同一实例同时 fetch 和 push，因此不设计双数据源合并或覆盖优先级。
- 不在本次提供 v2 快照清理、配置热重载或 v2 CLI 文本输出。
- 不支持 Push 实例首次上报时自动注册。

## 4. 总体架构

### 4.1 旧系统保持原样

```text
旧 config.json
  ├─ 旧 fetch 调度
  ├─ 旧 /api/push/{provider-category}
  ├─ 旧内存缓存、失败计数和 TTL
  ├─ provider_snapshot / provider_item
  └─ push_card_snapshot
          ↓
      GET /api/usage
          ↓
      订阅余额
```

旧类别端点包括当前已有的：

- `POST /api/push/claude`
- `POST /api/push/grok`
- `POST /api/push/cursor`
- `POST /api/push/mimo`
- `POST /api/push/antigravity`

这些端点、响应和持久化语义不做破坏性修改。

### 4.2 v2 独立运行

```text
config.v2.json
  ├─ v2 fetch 调度：按 instance_id 并发
  ├─ POST /api/push/v2/{instance_id}
  ├─ v2 内存缓存、失败计数和 TTL
  └─ provider_v2_snapshot / provider_v2_item
          ↓
      GET /api/usage/v2
          ↓
      订阅余额（新）
```

两套系统共用一个 Web 进程和同一个 SQLite 文件，但使用不同配置对象、后台任务、
内存状态和数据库表。v2 不读取或写入旧 Provider 表。

## 5. v2 配置模型

### 5.1 示例

```json
{
  "providers": {
    "claude-personal": {
      "type": "claude",
      "mode": "push",
      "label": "个人号",
      "order": 12
    },
    "claude-work": {
      "type": "claude",
      "mode": "push",
      "label": "工作号",
      "order": 13
    },
    "bigmodel-personal": {
      "type": "bigmodel",
      "mode": "fetch",
      "label": "个人账号",
      "api_key": "...",
      "order": 20
    },
    "bigmodel-work": {
      "type": "bigmodel",
      "mode": "fetch",
      "label": "工作账号",
      "api_key": "...",
      "order": 21
    }
  },
  "push_auth_secret": "...",
  "enforce_push_auth": true
}
```

顶层 `providers` 是实例注册表：

```text
providers[instance_id] = ProviderInstanceConfig
```

### 5.2 实例字段

| 字段 | 必填 | 语义 |
|---|---|---|
| 配置 key | 是 | `instance_id`，稳定且不可随意修改 |
| `type` | 是 | 服务实现类型，例如 `claude`、`grok`、`bigmodel` |
| `mode` | 是 | 只能为 `fetch` 或 `push` |
| `label` | 是 | 非空实例标签，例如“工作号” |
| `order` | 否 | 卡片顺序，默认 `999` |
| 凭据字段 | 按类型 | 复用现有 `ProviderConfig` 的 `api_key`、`base_url`、`cookie` 等 |

卡片标题由服务类型的标准显示名和实例标签组合：

```text
<type display name> · <label>
```

例如 `type=claude, label=工作号` 显示为“Claude 订阅 · 工作号”。`label` 可以修改；
`instance_id` 是缓存、持久化、路由和前端状态的唯一身份。

### 5.3 路径与加载

- 保留 `--config` 作为旧配置路径。
- 新增 `--v2-config` 作为 v2 配置路径，仅供 Web 模式使用。
- 未指定 `--v2-config` 时，从已解析的旧配置同目录读取 `config.v2.json`。
- 未显式指定任何旧配置路径时，v2 默认路径为项目默认配置同目录下的
  `config.v2.json`。
- v2 配置不存在时，v2 子系统为 disabled；旧系统照常启动。
- v2 配置无效时，只禁用 v2 并保留错误信息；旧系统照常启动。
- 修改 v2 配置后需要重启进程，本次不实现热重载。

### 5.4 启动校验

加载器必须校验：

- `instance_id` 匹配 `[A-Za-z0-9._-]+`，可安全作为 URL path segment；
- `type` 已注册；
- `mode` 是 `fetch` 或 `push`；
- `type + mode` 是受支持组合；
- `label.strip()` 非空；
- 凭据字段符合该类型和模式的允许字段及必填约束；
- 未知配置字段报错，避免凭据字段拼错后静默失效。

不同实例可以拥有相同 `type` 或相同 `label`，只要求 `instance_id` 唯一。排序按
`(order, 完整标题, instance_id)`，确保刷新间顺序稳定。

## 6. 服务类型注册表

v2 增加集中式类型注册表，不再从实例 ID 推断实现。注册项至少包含：

- 标准服务显示名；
- 支持的 mode；
- fetch Provider factory；
- Push request Pydantic model；
- Push payload 到标准卡片响应的 converter；
- 允许和必填的配置字段。

初始 fetch 类型覆盖现有 `_build_provider` 支持的全部实现：

```text
kimi
bigmodel
bigmodel_international
aiping
huawei_cloud
zenmux
codex
codex_security
antigravity
volcengine_ark
```

初始 Push 类型覆盖现有全部 Push 端点：

```text
claude
grok
cursor
mimo_token_plan
antigravity
```

`antigravity` 同时支持 fetch 和 push，但每个具体实例仍只能选择一种 mode。

fetch factory 复用现有 Provider 类。v2 将实例凭据转换为现有 `ProviderConfig` 后创建
独立 Provider 对象；现有 Provider 类不感知 `instance_id`。同一类型的多个实例会创建
多个对象并使用各自凭据并发抓取。

Push converter 复用现有请求 schema 和转换语义。v2 的 MiMo 卡片标题以配置和类型
注册表为准，不允许 request body 中的 `provider` 覆盖实例标题。

## 7. v2 API 契约

### 7.1 查询接口

```text
GET /api/usage/v2
GET /api/status/v2
```

`GET /api/usage/v2` 返回 v2 卡片数组。正常启用时只返回当前 v2 配置中存在的实例。
配置不存在时返回空数组；配置无效时返回 `503`。旧 `/api/usage` 不变。

`GET /api/status/v2` 始终返回 `200`：

```json
{
  "enabled": true,
  "last_updated": "2026-07-14T12:30:00+08:00",
  "config_error": null
}
```

- 配置不存在：`enabled=false`、`config_error=null`；
- 配置无效：`enabled=false`、`config_error` 为可展示错误；
- `last_updated` 在任一 v2 fetch 周期完成或 v2 Push 成功时更新。

### 7.2 Push 接口

```text
POST /api/push/v2/{instance_id}
Authorization: Bearer <v2 push_auth_secret>
Content-Type: application/json
```

请求 body 继续使用对应类型的现有 payload，不重复携带 `instance_id` 或 `type`。例如：

```http
POST /api/push/v2/claude-work
```

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

处理顺序和错误码：

1. v2 配置不可用：`503`；
2. `instance_id` 未注册：`404`；
3. 实例 mode 不是 `push`：`422`；
4. Bearer token 缺失或错误且启用强制认证：`401`；
5. body 不符合该 `type` 的 schema：`422`；
6. 成功：更新内存、尽力持久化并返回
   `{"status":"ok","instance_id":"claude-work"}`。

v2 所有 Push 实例共用 v2 配置顶层的 `push_auth_secret`。这意味着持有该 token 的客户端
可以向任意已注册 Push 实例发请求；这是明确接受的安全边界。使用
`secrets.compare_digest` 比较 token。

`enforce_push_auth=false` 时，认证失败只记录日志并继续处理，与旧系统软迁移模式一致。
v2 不把认证结果写入旧 `source` 表，也不进入 `/api/admin/sources`，避免 Provider 卡片
重构触碰模型用量的来源状态。v2 认证状态管理不在本次范围。

## 8. 标准响应与实例身份

旧 `UsageResponse` 不增加字段，保证旧 `/api/usage` 响应结构不变。v2 定义独立响应模型，
复用现有卡片字段并增加：

```json
{
  "instance_id": "claude-work",
  "type": "claude",
  "instance_label": "工作号",
  "provider": "Claude 订阅 · 工作号",
  "user_id": null,
  "membership_level": null,
  "limits": [],
  "balances": {},
  "token_usage": [],
  "history_usage": null,
  "model_stats": [],
  "error": null
}
```

- `instance_id` 是所有状态和交互的稳定 key；
- `type` 用于类型能力判断，例如 GLM 历史视图；
- `provider` 仅作为最终卡片标题；
- 具体 Provider 返回的服务名不得覆盖 v2 注册表生成的标题。

## 9. v2 运行时状态和调度

v2 使用独立运行时管理器，封装：

- 已加载配置和配置错误；
- fetch 结果；
- Push 结果及上报时间；
- 每实例连续失败次数；
- 每实例上一次成功结果；
- v2 最近更新时间；
- 后台 refresh task 生命周期。

这些状态均以 `instance_id` 为 key，不复用旧 `_cached_results`、`_pushed_results`、
`_pushed_at`、`_consecutive_failures` 或 `_prev_results`。

### 9.1 Fetch

- 后台任务只调度 `mode=fetch` 的实例；
- 同一周期内所有 fetch 实例并发执行；
- 成功后更新该实例结果、清零失败计数、保存最新快照；
- 前两次连续失败继续返回该实例上一次成功结果；
- 第三次及以后返回该实例的错误卡片；
- 一个实例失败不影响其他 v2 实例或旧系统；
- 只有 Push 实例时不必启动空转的 fetch 循环。

### 9.2 Push

- 每个 Push 实例保存自己的结果和 `recorded_at`；
- 沿用现有 30 分钟 TTL；
- 超时实例不出现在 `/api/usage/v2`，直到再次成功推送；
- 同类型实例互不覆盖。

### 9.3 排序

查询时直接使用当前 v2 配置中实例的 `order`，不再通过 `provider` 展示名反查配置。
fetch 和未过期 Push 结果合并后按 `(order, provider, instance_id)` 排序。

## 10. v2 数据库

v2 与旧系统使用同一个 SQLite 文件，新增独立表：

```sql
CREATE TABLE IF NOT EXISTS provider_v2_snapshot (
    instance_id   TEXT PRIMARY KEY,
    type          TEXT NOT NULL,
    mode          TEXT NOT NULL,
    label         TEXT NOT NULL,
    recorded_at   TEXT NOT NULL,
    raw_json      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS provider_v2_item (
    item_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    instance_id   TEXT NOT NULL,
    item_kind     TEXT NOT NULL,
    name          TEXT NOT NULL,
    value_text    TEXT,
    value_number  REAL,
    unit          TEXT,
    reset_time    TEXT,
    extra_json    TEXT,
    FOREIGN KEY (instance_id)
        REFERENCES provider_v2_snapshot(instance_id)
);

CREATE INDEX IF NOT EXISTS idx_provider_v2_item_instance
    ON provider_v2_item(instance_id);
```

v2 表保存每个实例的最新卡片状态，不承担时间序列历史：

1. UPSERT `provider_v2_snapshot`；
2. 删除该 `instance_id` 的旧 `provider_v2_item`；
3. 从新响应提取并插入当前 item；
4. 在同一事务中提交。

`raw_json` 保存完整 v2 响应。item 提取沿用旧 Provider item 的 limit、balance、
token usage、model stat 和 history usage 分类，但新旧 item 表不混用。

持久化失败沿用旧系统的 best-effort 策略：记录 warning，但已成功的 fetch 或 Push 结果
仍保留在内存并对 API 可见。

### 10.1 启动恢复

- 只读取 `provider_v2_snapshot`；
- 只恢复当前配置中仍存在的实例；
- 持久化的 `type`、`mode` 必须与当前配置匹配，否则忽略该快照；
- 标题和实例标签以当前配置为准，配置改名后无需等下一次采集；
- Push 快照恢复后仍执行 30 分钟 TTL；
- fetch 快照恢复为上一次成功结果，供启动后的失败回退使用；
- 损坏或无法验证的单行跳过，不阻塞其他实例或应用启动。

从 v2 配置删除实例时不删除其数据库行，但 API 不返回它。重新添加同 ID、同类型和同
模式后可以恢复；清理残留快照不在本次范围。

### 10.2 旧表和模型用量表

以下旧表原样保留，v2 不读不写：

```text
provider_snapshot
provider_item
push_card_snapshot
```

以下模型用量表和相关逻辑完全不改：

```text
usage_point
source
/api/usage/report
/api/usage/timeseries
/api/sources/*
```

因此本次上线不会清空、迁移、复制或重新聚合模型 Token 用量数据。

## 11. 前端

tab bar 增加一个按钮，保留现有布局：

```html
<button data-tab="balance">订阅余额</button>
<button data-tab="balance-v2">订阅余额（新）</button>
<button data-tab="usage">模型用量</button>
```

数据源映射：

| tab | 数据接口 | 状态接口 |
|---|---|---|
| `balance` | `/api/usage` | `/api/status` |
| `balance-v2` | `/api/usage/v2` | `/api/status/v2` |
| `usage` | 现有模型用量接口 | 不变 |

两个余额 tab 共用现有 `grid`、`renderCard` 和全部卡片 CSS，不复制 UI。切换余额 tab 时
请求对应接口并替换 grid 内容；60 秒轮询只刷新当前可见的余额数据源。

前端状态调整：

- tab 继续保存在 `ai-plan-insight:tab`；
- 对不存在或过时的 tab 值回退到 `balance`；
- v2 卡片历史切换、图表 DOM ID 和 tooltip 查找使用 `instance_id`；
- 旧卡片没有 `instance_id` 时继续以旧 `provider` 作为兼容 key；
- GLM 历史能力在 v2 中根据 `type` 和 `history_usage` 判断，不根据完整标题；
- v2 disabled 时显示“尚未配置订阅余额（新）”；
- v2 配置错误时显示配置错误，不伪装成 Provider 错误卡片；
- HTML/CSS 卡片结构、桌面网格和移动端布局保持现状。

## 12. 客户端迁移

v2 Push body schema 不变。客户端只需：

1. 将目标 URL 从旧类别端点改为具体实例端点，例如：

   ```text
   /api/push/claude
   -> /api/push/v2/claude-work
   ```

2. 在 v2 启用强制认证时发送：

   ```http
   Authorization: Bearer <v2 push_auth_secret>
   ```

两个 Claude 账号位于不同设备，每台设备继续只运行一个 `claude-sub-agent`。不需要在同一
Mac 上实现多 Keychain 凭据、多个 launchd label 或多个进程。每台设备只配置不同的
`--push-url` 即可区分实例。

当前 `claude-sub-agent` 已支持 `--push-url`，但尚未发送 Bearer token。为完整支持 v2
强制认证，需要增加：

- `--push-token`；
- `CLAUDE_USAGE_PUSH_TOKEN`；
- flag 高于环境变量；
- 安装时将有效值写入 launchd 参数；
- Pusher 在 token 非空时发送 Authorization header；
- 测试不得打印或泄漏 token。

其他 Push agent 若已支持自定义 URL 和 Bearer token，只需配置迁移；否则按同一契约
补齐。旧 agent 在迁移期间可继续调用旧端点。

`ai-usage-agent` 只负责 `/api/usage/report` 的模型 Token 用量，不属于 Provider 卡片
系统，本次无需修改。

## 13. 错误处理

| 场景 | 行为 |
|---|---|
| v2 配置不存在 | v2 disabled，新 tab 显示未配置，旧系统可用 |
| v2 配置语法或校验失败 | v2 disabled，status 暴露错误，旧系统可用 |
| 未知 Push instance ID | `404`，不创建实例或数据库行 |
| 向 fetch 实例 Push | `422` |
| Push schema 错误 | `422`，不更新旧快照 |
| 强制认证失败 | `401`，不更新内存或数据库 |
| fetch 单实例失败 | 仅增加该实例失败计数，按三次失败规则回退 |
| v2 持久化失败 | warning；内存结果仍可用 |
| v2 单行快照损坏 | 跳过该行，继续恢复其他实例 |
| v2 后台任务异常 | 记录错误并继续下一周期，不影响旧后台任务 |

## 14. 测试策略

### 14.1 配置

- 同一 `type` 的多个 fetch 实例可加载；
- 同一 `type` 的多个 Push 实例可加载；
- `instance_id` 非法、空 label、未知 type、未知 mode 均失败；
- 不支持的 `type + mode` 失败；
- 类型必填凭据和未知字段校验；
- v2 配置缺失与无效均不影响旧配置加载。

### 14.2 Fetch 运行时

- 同类型两个实例创建两个 Provider 对象并使用各自凭据；
- 结果按 instance ID 独立保存；
- 一个实例失败不覆盖另一个实例；
- 每实例三次失败回退规则；
- 实例排序使用 order 和稳定 tiebreaker；
- 只有 Push 实例时不启动空 fetch 循环。

### 14.3 Push API

每个现有 Push 类型至少覆盖一次成功转换，并额外覆盖：

- 同类型两个实例连续推送后同时存在；
- 未知实例 `404`；
- fetch 实例 `422`；
- 动态 schema 验证 `422`；
- 全局 token 的软模式与强制模式；
- 一个实例的新推送不改变另一个实例；
- MiMo body 不能覆盖配置生成的卡片标题；
- 旧 Push 端点的现有回归测试继续通过。

### 14.4 数据库

- v2 表初始化不改变旧表和 `usage_point` 数据；
- 同实例快照 UPSERT 且 item 整体替换；
- 不同实例互不覆盖；
- fetch 与 Push 快照恢复；
- Push TTL 在恢复后仍生效；
- 配置已删除实例不返回；
- type/mode 不匹配快照不恢复；
- 当前配置 label 覆盖快照旧 label；
- 损坏快照不阻塞恢复。

### 14.5 前端

- 三个 tab 均存在，旧 tab 和模型用量 tab 行为不变；
- 新 tab 请求 v2 API；
- 两个余额 tab 复用同一 card renderer；
- v2 disabled、配置错误、空数据和正常数据状态；
- 同类型多实例显示两张卡片；
- v2 历史视图使用 instance ID，两个 GLM 实例互不切换；
- localStorage tab 值兼容和非法值回退；
- 桌面与移动端截图检查 tab 不换行遮挡、卡片无重叠。

### 14.6 Claude agent

- token flag/env/default 优先级；
- token 非空时发送 Bearer header；
- token 为空时不发送 header；
- install 持久化 push URL 和 token 参数；
- 日志与错误不包含 token；
- 现有收集、重试和 launchd 测试继续通过。

### 14.7 全量回归

- `ai-plan-insight` 全部测试通过；
- `claude-sub-agent` 的 `go test ./...` 和 `go vet ./...` 通过；
- 显式验证现有 `usage_point` 行数与聚合结果在 schema 初始化前后不变；
- Playwright 验证旧订阅余额、新订阅余额和模型用量三个 tab。

## 15. 部署与迁移顺序

1. 部署支持 v2 的 `ai-plan-insight`，暂不创建 `config.v2.json`。
2. 验证旧“订阅余额”和“模型用量”均与部署前一致。
3. 按新规范创建 `config.v2.json`，先声明一个测试实例并重启服务。
4. 将对应 agent 的 URL 改为 `/api/push/v2/{instance_id}`；需要时配置 v2 全局 token。
5. 验证“订阅余额（新）”出现该实例卡片，旧 tab 仍保持原卡片。
6. 逐个添加其余 fetch 和 Push 实例。
7. 迁移稳定后，可在未来单独设计旧系统下线；本次不删除任何旧配置、端点或表。

回滚只需移除或停用 `config.v2.json` 并重启。旧 tab、旧 API 和旧数据一直保留，模型用量
统计不需要回滚或恢复。

## 16. 实现边界建议

为避免继续扩大当前 `web.py`，v2 按职责拆分：

- `instance_config.py`：v2 schema、路径解析和加载；
- `provider_registry.py`：type/mode 注册、factory、Push schema 和 converter；
- `provider_instances.py`：v2 运行时状态、fetch 调度、Push dispatch 和排序；
- `provider_v2_store.py`：v2 表初始化、最新快照写入和恢复；
- `api_schemas.py`：v2 响应模型；
- `web.py`：生命周期接线和三个薄 v2 endpoint；
- `index.html`：新增 tab 和数据源切换，复用现有卡片 renderer。

这些模块只依赖现有 Provider 类和标准卡片模型，不修改各 Provider 的内部抓取实现。

## 17. 验收标准

1. 两个不同设备可向两个已配置的 Claude v2 实例推送，并同时显示两张独立卡片。
2. 任意支持的 fetch 类型可配置两个账号，并同时显示两个独立结果。
3. 所有现有 Push 类型均可通过统一的 `/api/push/v2/{instance_id}` 多实例路由工作。
4. 未注册实例被拒绝，实例之间缓存、TTL、失败和快照互不覆盖。
5. 旧“订阅余额”tab、旧 Push API 和旧 Provider 表继续工作。
6. “订阅余额（新）”复用现有卡片布局，只增加 tab 和数据源切换。
7. `usage_point`、`source` 和模型用量统计在升级前后保持不变。
8. v2 配置或运行错误不会使旧系统不可用。
9. 重启后可从 v2 表恢复仍有效的实例卡片。
10. 全量后端、客户端和前端回归验证通过。
