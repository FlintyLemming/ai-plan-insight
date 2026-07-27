# 配置迁入数据库与 Web 管理页设计

- 日期：2026-07-27
- 状态：设计已确认，待实施计划
- 主仓库：`ai-plan-insight`
- 影响面：配置层、Web 层、前端、部署方式、README

## 1. 背景

当前唯一配置入口是 `config.json`，由 `instance_config.load_v2_config()` 解析，
`config_service.ConfigService` 按文件 mtime 轮询热重载。它有两个实际问题：

1. **编辑不方便**。改一条订阅实例要 SSH 到服务器、找到挂载目录、手写 JSON。
2. **类型知识只存在于代码里**。有哪些 `type`、每个 `type` 支持什么 `mode`、
   要填哪些凭据字段，这些信息散落在四处：
   - `instance_config._SUPPORTED_TYPES`（type → 允许的 mode）
   - `instance_config._ALLOWED_CREDENTIAL_FIELDS`（type → 可填字段）
   - `instance_config._REQUIRED_CREDENTIAL_FIELDS`（(type, mode) → 必填字段）
   - `provider_registry._TYPE_DISPLAY_NAMES`（type → 中文名）

   使用者无从得知，只能翻 README 或读源码；新增一个 provider 类型要同时改四个
   地方，漏改哪个都不会立刻报错。

本次把配置整体迁入 SQLite，并提供带密码保护的 Web 管理页。`config.json` 退休，
仅在首次启动时作为一次性迁移源。

## 2. 目标

1. 全部四块配置（`providers` 实例、`model_aliases`、`push_auth_secret`、
   `enforce_push_auth`）迁入数据库，通过 Web 管理页维护，不再需要 SSH。
2. 类型元数据集中为唯一真相，同时驱动：写入校验、管理页表单、`/api/admin/provider-types`。
   新增一个 provider 类型的改动点从四处收敛到一处。
3. 管理页新增实例时能看到全部可选类型、每个类型的用途说明、每个字段的中文标签与
   获取指引，并可在保存前测试连接。
4. 管理面（管理页与写接口）受密码保护；只读看板与推送接口的现有行为不变。
5. 启动时若发现 `config.json` 且数据库配置为空，自动迁移、备份原文件、提示一次。
6. 配置可整体导出为 JSON、可整体导入，作为备份与批量编辑的逃生出口。

## 3. 非目标

- 不改动 `usage_point` / `source` / `provider_v2_snapshot` / `provider_v2_item`
  的既有结构与语义（只在删除实例时清理对应快照行）。
- 不改动 `/api/usage/report`、`/api/push/v2/{instance_id}` 的协议与鉴权方式
  （推送仍用 `push_auth_secret` Bearer token）。
- 不给只读看板加登录。
- 不做多用户、角色、审计日志。
- 不做实例 ID 改名（见 §7.4）。
- 不改动看板的图表、卡片渲染与既有交互。

## 4. 总体结构

```
                    provider_types.py   ← 类型元数据唯一真相
                       ↑        ↑
        instance_config.py    admin_api.py ──→ admin.html（表单）
        （校验逻辑不变，       （/api/admin/*）
          三张 dict 改为派生）      ↑
                                admin_auth.py（密码 + session）
                    ┌──────────────┘
            config_store.py  ← SQLite 读写 + config_version
                    ↑
            config_service.py（轮询 config_version 取代轮询 mtime）
                    ↓
            V2RuntimeManager.reload()   ← 完全不变
```

新增模块：

| 文件 | 职责 |
|---|---|
| `provider_types.py` | 类型元数据表；派生出校验用的三张映射与显示名 |
| `config_store.py` | 配置表的读写、`config_version` 递增、导入导出 |
| `config_migration.py` | 启动时的 `config.json` → DB 一次性迁移 |
| `admin_auth.py` | 密码 hash/校验、session、限速、`require_admin` 依赖 |
| `admin_api.py` | `/api/admin/*` 路由（`APIRouter`，由 `web.py` 挂载） |
| `admin.html` | 独立管理页 |

`provider_instances.py`（`V2RuntimeManager`）、`provider_registry.py` 的运行时
部分、`api_schemas.py`、`usage_store.py`、`pricing.py`、`index.html` 的看板逻辑
均不需要改动。

## 5. 类型元数据（`provider_types.py`）

### 5.1 数据结构

```python
@dataclass(frozen=True)
class CredentialField:
    name: str                     # api_key / base_url / ...
    label: str                    # 中文标签，如 "Management API Key"
    required_in: frozenset[str]   # 在哪些 mode 下必填，如 {"fetch"}
    secret: bool = False          # 是否掩码显示
    help: str = ""                # 一句话获取指引
    placeholder: str = ""

@dataclass(frozen=True)
class ProviderType:
    name: str
    display_name: str
    modes: frozenset[str]         # {"fetch"} / {"push"} / {"fetch", "push"}
    summary: str = ""             # 一句话用途说明
    doc_url: str = ""             # 可选，凭据获取页或 agent 项目地址
    fields: tuple[CredentialField, ...] = ()

PROVIDER_TYPES: dict[str, ProviderType] = {...}   # 15 个既有类型
```

文案原则：`label` / `help` 照搬 README 现有描述，拿不准获取步骤的类型只写最朴素
的说明（如"该服务的 API Key"），不编造控制台路径。`doc_url` 对 push 类型填对应
agent 项目地址（README 已有），fetch 类型没有可靠链接时留空。

### 5.2 派生

`instance_config.py` 的三张模块级 dict 改为从 `PROVIDER_TYPES` 计算得出，
`_validate_instance()` 与 `_KNOWN_FIELDS` 的逻辑保持原样，对外行为不变。
`provider_registry._TYPE_DISPLAY_NAMES` 同样改为查 `PROVIDER_TYPES`。

### 5.3 自洽性测试

`tests/test_provider_types.py` 断言元数据与实现不脱节：

- `PROVIDER_TYPES` 中每个支持 `fetch` 的类型都在 `_FETCH_FACTORIES` 里有工厂
- 每个支持 `push` 的类型都在 `_PUSH_REQUEST_MODELS` 和 `_PUSH_CONVERTERS` 里有条目
- 反向：工厂/converter 里的每个 key 都在 `PROVIDER_TYPES` 里，且 mode 对得上
- 每个字段的 `required_in` ⊆ 该类型的 `modes`
- 所有字段名 ∈ `V2InstanceConfig` 的字段集合
- 所有类型的 `display_name`、每个字段的 `label` 非空

这条测试是"加类型漏改"的护栏：漏注册工厂或漏加元数据都会红。

## 6. 数据库 schema

全部放进现有 `usage.db`，随其他表一起在启动时 `CREATE TABLE IF NOT EXISTS`。

```sql
CREATE TABLE IF NOT EXISTS app_setting (
    key    TEXT PRIMARY KEY,
    value  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS provider_instance (
    instance_id       TEXT PRIMARY KEY,
    type              TEXT NOT NULL,
    mode              TEXT NOT NULL,
    label             TEXT NOT NULL,
    sort_order        INTEGER NOT NULL DEFAULT 999,
    enabled           INTEGER NOT NULL DEFAULT 1,
    credentials_json  TEXT NOT NULL DEFAULT '{}',
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS model_alias (
    raw_id  TEXT PRIMARY KEY,
    label   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_model_alias_label ON model_alias(label);

CREATE TABLE IF NOT EXISTS admin_session (
    token_hash  TEXT PRIMARY KEY,
    created_at  TEXT NOT NULL,
    expires_at  TEXT NOT NULL
);
```

说明：

- `sort_order` 而非 `order`（SQL 关键字），映射到 `V2InstanceConfig.order`。
- 凭据放 `credentials_json`，新增类型的新凭据字段不需要 `ALTER TABLE`。
- `model_alias` 用 `raw_id` 做主键，一个原始模型 ID 只能归到一个展示名。这比
  现在 `model_aliases` 的"后定义覆盖前定义"更严格；迁移时若源文件有重复
  `raw_id`，沿用现行为（后者覆盖）并记一条日志。
- `admin_session` 存 token 的 SHA-256，重启不掉线，可实现"退出所有设备"。

`app_setting` 的 key：

| key | 含义 |
|---|---|
| `push_auth_secret` | 推送 token |
| `enforce_push_auth` | `"1"` / `"0"` |
| `admin_password_hash` | 见 §8.1 |
| `config_version` | 单调递增整数，配置写事务内 +1 |
| `config_migrated_at` | 迁移完成时间（ISO） |
| `migration_notice` | `pending` / `dismissed` |
| `migration_summary_json` | 迁移摘要，供提示横幅展示 |

## 7. 配置读写（`config_store.py`）

### 7.1 读

```python
def load_config(conn) -> LoadResult
```

组装 `V2Config`，供 `ConfigService` 使用。语义与 `load_v2_config()` 对齐：

- 只装载 `enabled = 1` 的实例。禁用实例对运行时完全不存在——不拉取、不上卡片、
  push 到它返回 404。
- 每行仍跑一遍 `_validate_instance()`。写入时已强校验过，但代码升级可能让旧行
  变非法（某 `type` 被移除、必填字段变更）；这类行进 `instance_errors`，其余
  实例照常加载，管理页在该行高亮错误。
- `config_error` 保留但只在数据库不可读时出现（`/api/status/v2` 的响应形状不变，
  看板前端不用改）。

### 7.2 写

每个写操作是一个事务，事务内 `config_version = config_version + 1`。
写入前用 `provider_types` + `_validate_instance()` 强校验，不合法直接 422，
数据库不会留下非法行。

```python
def list_instances(conn, include_disabled=True) -> list[InstanceRow]
def create_instance(conn, row) -> None
def update_instance(conn, instance_id, row) -> None
def delete_instance(conn, instance_id) -> None      # 同时清理 v2 快照行
def get_aliases(conn) / replace_aliases(conn, mapping)
def get_push_auth(conn) / set_push_auth(conn, secret, enforce)
def export_config(conn) -> dict / import_config(conn, payload)
def get_config_version(conn) -> int
```

### 7.3 热重载（`config_service.py`）

`ConfigService` 的 `_stat_mtime()` 换成 `_read_config_version()`（一次
`SELECT value FROM app_setting WHERE key='config_version'`，与 stat 一样便宜）。
版本未变返回缓存；变了重新组装、比签名、必要时 `manager.reload()`。
签名对比与 `set_manager` 的逻辑原样保留。

管理页写完立刻生效：下一次 `/api/usage/v2` 请求就会看到新版本号并 reload。
不需要额外的通知机制。

### 7.4 实例 ID 不可改

`instance_id` 是 push URL 的一部分，也是快照主键，改名等于换实例。管理页把该
字段在编辑态置灰并说明"改名请删除后重建"。

## 8. 鉴权（`admin_auth.py`）

### 8.1 密码存储

零新依赖，用标准库 `hashlib.scrypt`：

```
admin_password_hash = "scrypt$16384$8$1$<salt_b64>$<hash_b64>"
```

校验用 `secrets.compare_digest`。

### 8.2 初始化与重置

| 情况 | 行为 |
|---|---|
| DB 无密码，环境变量 `ADMIN_PASSWORD` 非空 | 启动时 hash 后写入，日志 info |
| DB 无密码，无环境变量 | 管理接口全部返回 503；`/admin` 页面显示"未设置管理密码，请设置 `ADMIN_PASSWORD` 环境变量后重启"。**不存在无密码可访问的窗口** |
| 环境变量 `ADMIN_PASSWORD_RESET` 非空 | 启动时无条件覆盖密码、清空全部 session，日志 warning 提示"重置完成，请移除该环境变量后重启" |
| DB 有密码，`ADMIN_PASSWORD` 也在 | 忽略环境变量（DB 为准），日志 debug |

### 8.3 会话

- `POST /api/admin/login` body `{"password": "..."}` → 生成
  `secrets.token_urlsafe(32)`，SHA-256 后存 `admin_session`，
  Set-Cookie `apiadmin=<token>`；`HttpOnly`、`SameSite=Lax`、`Path=/`、
  请求为 https 时加 `Secure`。
- 有效期 7 天，每次成功鉴权若剩余不足 1 天则滑动续期。
- `POST /api/admin/logout` 删除当前 token 行；`POST /api/admin/logout-all` 清表。
- 过期行在每次登录时顺带清理。

### 8.4 限速

登录失败按客户端 IP 计数（进程内滑动窗口）：1 分钟内失败 5 次，锁定 60 秒，
期间一律返回 429。成功登录清零。

### 8.5 保护范围

- 需要登录：全部 `/api/admin/*`（`/session` 与 `/login` 除外），含现有的
  `GET /api/admin/sources`（它暴露推送源认证状态，本就该保护；前端未使用，
  仅 `tests/test_push_auth.py:92` 调用，该测试需相应更新）。
- `GET /admin` 返回的 HTML 本身不鉴权——它得先渲染出登录框。页面里没有任何配置
  数据，所有内容都来自受保护的接口；未登录时 JS 只显示登录表单。
- 不需要登录：`/`、`/admin`（空壳页面）、`/api/usage/v2`、`/api/status/v2`、
  `/api/usage/timeseries`、`/api/sources/stale`、
  `/api/sources/{id}/dismiss-stale`、`/api/notices`。
- 推送接口不变，仍用 `push_auth_secret`。

## 9. 迁移（`config_migration.py`）

### 9.1 时机与条件

在 `lifespan` 中，`usage_store.init_db()` 之后、`_init_v2_manager()` 之前执行。

源路径：`--config` 指定的路径，否则 `DEFAULT_CONFIG_PATH`。

"数据库配置为空"定义为：`provider_instance` 与 `model_alias` 均无行，
且 `push_auth_secret` 未设置。

| 条件 | 行为 |
|---|---|
| 文件不存在 | 什么都不做 |
| 文件存在 + DB 配置为空 + 文件可解析 | 执行迁移（§9.2） |
| 文件存在 + DB 配置非空 | **不迁移、不改名**。日志 warning；管理页顶部常驻提示"检测到 `config.json`，已忽略，配置以数据库为准"（只要文件还在就一直提示，不可 dismiss） |
| 文件存在 + DB 为空 + JSON 解析失败 | 不迁移、不改名。日志 error；管理页提示解析错误原文，让用户修好文件重启或直接在管理页手工录入 |

### 9.2 迁移流程

1. `load_v2_config(path)` 容错读取（复用现有函数，不改）。
2. 单事务写入：`provider_instance`（`enabled = 1`）、`model_alias`、
   `push_auth_secret`、`enforce_push_auth`，并递增 `config_version`。
3. 重命名原文件为 `config.json.bak`；若已存在则用
   `config.json.bak.<YYYYMMDD-HHMMSS>`。重命名失败（只读挂载等）不回滚已写入的
   配置，只记 error 并把这一情况写进摘要——数据库已是真相，下次启动会走"DB 配置
   非空"分支，不会重复导入。
4. 写 `config_migrated_at`、`migration_notice = pending`、`migration_summary_json`
   （实例数、别名数、被跳过的实例及原因、备份文件名、重命名是否成功）。

`load_v2_config()` 的 `instance_errors` 里的实例**不写入数据库**，但会列进摘要，
提示用户在管理页重新添加。

### 9.3 提示

- `GET /api/notices`（**公开**，只读）：返回 pending 的迁移提示，含摘要文本与
  备份文件名。不含任何凭据。
- 看板 `index.html` 顶部与管理页各显示一条横幅。
- `POST /api/notices/migration/dismiss`（**公开**）：置为 `dismissed`。之后
  任何启动都不再提示，符合"这次启动后提醒用户，后续不提醒"。
- dismiss 接口公开是刻意的：迁移提示不含敏感信息，而看板不需要登录，若要求登录
  才能关掉横幅会形成死结。

## 10. 管理 API（`admin_api.py`）

`APIRouter(prefix="/api/admin")`，除标注外均需 `require_admin`。

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/session` | **公开**。`{authenticated, password_configured}`，供管理页决定显示登录框还是引导文案 |
| POST | `/login` | **公开**。校验密码，下发 cookie |
| POST | `/logout` / `/logout-all` | 退出当前 / 全部会话 |
| POST | `/password` | 改密码，body `{old, new}`，新密码最短 8 位；成功后清空其他会话 |
| GET | `/provider-types` | 返回 `PROVIDER_TYPES` 全表（含字段元数据），管理页据此渲染表单 |
| GET | `/instances` | 全部实例（含禁用），secret 字段掩码为 `sk-••••ab12`（保留前 3 后 4，长度不足则全掩） |
| GET | `/instances/{id}/secrets` | 返回该实例凭据明文，供"显示"按钮 |
| POST | `/instances` | 新建。`instance_id` 需匹配 `[A-Za-z0-9._-]+` 且不重复 |
| PUT | `/instances/{id}` | 更新 type/mode/label/order/enabled/凭据。凭据字段传 `null` 表示保持原值，传空串表示清空 |
| DELETE | `/instances/{id}` | 删除实例并清理其 `provider_v2_snapshot` / `provider_v2_item` 行 |
| POST | `/instances/test` | 用请求体里的 type/mode/凭据试拉一次，不落库 |
| GET / PUT | `/aliases` | 模型别名全量读写 |
| GET / PUT | `/push-auth` | 读时 `push_auth_secret` 掩码 + `enforce_push_auth`；PUT 全量写 |
| GET | `/push-auth/secret` | `push_auth_secret` 明文，供"显示"按钮 |
| GET | `/export` | 导出全量配置 JSON（含明文凭据，不含管理密码） |
| POST | `/import` | 整体替换导入 |
| GET | `/sources` | 既有端点，改为需登录 |

### 10.1 测试连接

`POST /api/admin/instances/test` body `{type, mode, credentials}`：

- `mode = "fetch"`：复用 `build_fetch_provider()` 构造临时 provider，跑
  `authenticate()` → `fetch_usage()` → `parse_usage()`，总超时 15 秒。返回
  `{ok: true, summary: {membership_level, limits: [...摘要], balances}}` 或
  `{ok: false, error: "..."}`。不写数据库、不进运行时缓存。
- `mode = "push"`：不发请求，返回推送 URL（`/api/push/v2/{instance_id}`）、
  该类型的 payload 示例、以及是否已配置 `push_auth_secret`。

### 10.2 导入导出

导出格式与现 `config.json` 同构，额外带 `enabled`：

```json
{
  "providers": {"kimi-main": {"type": "kimi", "mode": "fetch", "label": "主账号",
                              "order": 30, "enabled": true, "api_key": "..."}},
  "model_aliases": {"GLM 5.2": ["glm-5.2"]},
  "push_auth_secret": "...",
  "enforce_push_auth": false
}
```

导入是**整体替换**：先全量校验（任一实例不合法则整体 422 并列出每条错误），
通过后在一个事务里清空三张配置表再写入。管理密码与 session 不受影响。
前端二次确认，文案明确"将覆盖现有全部配置"。

## 11. 管理页（`admin.html`）

单文件 vanilla JS，深色 slate 配色与 `index.html` 一致。`GET /admin` 返回该页面。
看板右上角加一个入口链接。

区块：

1. **登录**：未登录时只显示密码框；`password_configured` 为 false 时改为显示
   环境变量引导文案。
2. **订阅实例**：表格（标签 / 类型 / 模式 / 排序 / 状态 / 操作）。`order` 用数字
   输入框直接改（不做拖拽）。禁用行整行降透明度。校验失败的行标红并显示原因。
3. **实例编辑弹窗**：
   - 新建时先选类型 → 下拉显示 `display_name`，选中后展示 `summary` 与 `doc_url`
   - 模式：只列该类型支持的 mode；只有一种时锁定
   - 凭据：按 `fields` 动态渲染，必填打星号，字段下方显示 `help`。`secret` 字段
     在编辑既有实例时显示掩码值 + 眼睛图标（点击调 `/instances/{id}/secrets` 取
     明文）；新建时是普通空输入框，无需掩码
   - 底部「测试连接」按钮，结果就地展示
4. **模型别名**：`展示名 → 原始 ID 列表` 的可增删编辑器。
5. **推送认证**：secret（掩码 + 显示 + 生成随机值按钮）与 `enforce_push_auth` 开关。
6. **管理密码**：旧密码 + 新密码 + 确认。
7. **导入导出**：导出按钮触发下载；导入是一个粘贴 JSON 的文本域 + 二次确认。
8. **迁移横幅**：`migration_notice = pending` 时置顶显示摘要与关闭按钮；
   `config.json` 被忽略时置顶显示常驻警告。

## 12. 部署与文档

- 新增环境变量：`ADMIN_PASSWORD`、`ADMIN_PASSWORD_RESET`。
- `--config` 参数保留，语义变为"迁移源文件路径"，help 文本相应修改。
- README 改动：
  - 删除「配置（config.json）」小节的编辑说明，改为「管理页」小节
  - 「从旧版迁移」小节改写为 config.json → 数据库的自动迁移说明
  - 支持的服务一览表保留，注明"以管理页类型下拉为准"
  - `sample-compose.yaml`、Docker 运行示例加上 `ADMIN_PASSWORD`
  - `config.json.example` 保留，标注为"仅用于从旧版迁移，新部署不需要"

## 13. 测试

新增：

| 文件 | 覆盖 |
|---|---|
| `test_provider_types.py` | §5.3 的元数据自洽断言 |
| `test_config_store.py` | CRUD、`config_version` 递增、`enabled` 过滤、别名唯一约束、删除实例连带清快照、导入导出往返 |
| `test_config_migration.py` | 正常迁移、DB 非空不迁移、坏 JSON 不改名、bak 重名加时间戳、重命名失败不回滚、摘要内容、`instance_errors` 不入库 |
| `test_admin_auth.py` | 环境变量初始化与重置、错误密码、session 过期与滑动续期、限速 429、未设密码时 503、cookie 属性 |
| `test_admin_api.py` | 各端点鉴权、掩码不泄露明文、`null` 凭据保持原值、导入整体校验失败不落库、测试连接不落库 |
| `test_notices.py` | 迁移提示的 pending / dismissed 流转 |

需要改造的现有测试（它们通过 `_config_path` 指向临时 `config.json`）：
`test_config_service.py`、`test_v2_api.py`、`test_push_auth.py`、
`test_web_push_claude.py`、`test_web_push_grok.py`、`test_usage_api.py`、
`test_usage_chart.py`、`test_db_startup.py`、`test_main_args.py`、
`test_pocketbase_removal.py`。改造方式统一：提供一个测试辅助函数，直接往临时
`usage.db` 里写配置行，替代写临时 config.json。`test_instance_config.py` 保留，
因为 `load_v2_config()` 仍被迁移路径使用。

## 14. 实施顺序

1. `provider_types.py` + 派生改造 + `test_provider_types.py`（纯重构，行为不变）
2. DB schema + `config_store.py` + `test_config_store.py`
3. `config_service.py` 切到版本号驱动 + 现有测试改造
4. `config_migration.py` + 接入 `lifespan` + 测试
5. `admin_auth.py` + 测试
6. `admin_api.py` + 测试
7. `admin.html` + `/admin` 路由 + 看板入口与横幅
8. README / compose / `config.json.example` 文档更新

第 1、2 步落地后系统仍完全按旧方式工作；第 3 步是切换点。
