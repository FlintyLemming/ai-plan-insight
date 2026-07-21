# 删除订阅余额 v1、合并配置文件、热重载 设计

- 日期：2026-07-21
- 状态：设计已确认，待实施计划
- 主仓库：`ai-plan-insight`
- 关联：`2026-07-14-provider-instance-v2-design.md`（v2 引入设计）

## 1. 背景

`2026-07-14-provider-instance-v2-design.md` 采用新旧并行方案：旧 `config.json`
（按服务类别 key 的凭据表）驱动旧「订阅余额」tab，新 `config.v2.json`（按实例 ID
key 的实例注册表）驱动「订阅余额（新）」tab。两套系统共用一个 Web 进程、同一 SQLite
文件，但配置、后台任务、内存状态和数据库表各自独立。

迁移已稳定。本次目标：删除所有订阅余额 v1 逻辑，合并两套配置文件为单一文件，并让该
配置文件支持热重载。同时修复一个已知缺陷——配置文件中写入了运行代码尚不认识的 `type`
时，整页无法显示任何数据。

模型用量系统（`usage_point`、`source`、`/api/usage/report`、`/api/usage/timeseries`、
`/api/sources/*`）不在订阅余额范畴内，本次只顺带统一其推送认证配置来源，逻辑不变。

## 2. 已确认的关键决策

| 决策 | 选择 |
|---|---|
| 合并后的单一配置文件 | `config.json`（删除 `config.v2.json`、删除 `--v2-config`） |
| 热重载触发方式 | 每次请求 stat 文件 mtime，变化时重载 |
| 未知 `type` 处理 | 跳过该实例，仅通过 `/api/status` 暴露告警，页面照常渲染其余实例 |
| 非 Web CLI 模式 | 删除（项目变为仅 Web） |

## 3. 目标

1. 订阅余额只保留一套逻辑：v2 实例注册表 + 统一 `/api/push/v2/{instance_id}` +
   `provider_v2_snapshot` / `provider_v2_item`。
2. 单一 `config.json`，既含订阅余额实例，也含模型用量相关的 `model_aliases` 和推送
  认证。
3. 修改 `config.json` 后无需重启，下一次请求即生效。
4. 单个实例配置错误（含未知 `type`）只跳过该实例，其余实例正常显示。
5. 模型用量统计、推送认证、stale source 告警等功能不受影响。

## 4. 非目标

- 不重命名 `instance_config` 模块或 `V2Config` / `V2InstanceConfig` 类名（`v2` 仅作历史
  名，可留待后续 cosmetic rename）。
- 不重命名 `GET /api/usage/v2`、`GET /api/status/v2`、`POST /api/push/v2/{instance_id}`
  路径（已部署的 push agent 依赖该路径）。
- 不迁移或清理旧数据库表 `provider_snapshot` / `provider_item` / `push_card_snapshot`
  中的历史数据；只是不再读写。
- 不自动转换用户现有 `config.v2.json`；提供文档与示例让用户手动合并为 `config.json`。
- 不引入实例级推送 token；继续共用一个全局 `push_auth_secret`。

## 5. 合并后的配置格式

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
    "bigmodel-personal": {
      "type": "bigmodel",
      "mode": "fetch",
      "label": "个人账号",
      "api_key": "YOUR_BIGMODEL_API_KEY",
      "order": 20
    }
  },
  "model_aliases": {
    "GLM 5.2": ["glm-5.2", "glm5.2"],
    "GPT 5.2": ["gpt-5.2"]
  },
  "push_auth_secret": "ai-plan-insight-push-token-2026",
  "enforce_push_auth": false
}
```

### 5.2 顶层字段

| 字段 | 来源 | 语义 |
|---|---|---|
| `providers` | 旧 `config.v2.json` | 实例注册表，key 为 `instance_id` |
| `model_aliases` | 旧 `config.json` | 原始模型 ID → 展示名归并，供模型用量图表 |
| `push_auth_secret` | 两份旧配置各一份，合并为 **唯一一个** | 同时用于 `/api/push/v2/{instance_id}` 和 `/api/usage/report` |
| `enforce_push_auth` | 同上，合并为唯一一个 | 同时作用于上述两类推送 |

> ⚠️ 行为变化：旧的两份配置各自有一个 `push_auth_secret` / `enforce_push_auth`，合并后
> 只剩一个，两类推送共用。迁移时用户需选定一个 token 并同步给所有 push agent 和
> ai-usage-agent。

### 5.3 路径与加载

- 保留 `--config`，默认 `config.json`（项目根或 `--config` 指定）。
- 删除 `--v2-config`。订阅余额与模型用量共用同一文件。
- `config.json` 不存在时：订阅余额 manager disabled，模型用量图表的 alias 退化为空
  映射（原样展示模型 ID），Web 仍可启动。
- `config.json` 顶层结构非法（JSON 解析失败或顶层 schema 校验失败）时：manager
  disabled，`/api/status` 暴露 `config_error`，Web 仍可启动。

## 6. 配置模型变更

### 6.1 `V2Config` 扩展

在 `instance_config.V2Config` 增加 `model_aliases` 字段与 `alias_lookup` 属性，迁移自
旧 `Config`：

```python
class V2Config(BaseModel):
    model_config = {"extra": "forbid"}
    providers: dict[str, V2InstanceConfig]
    model_aliases: dict[str, list[str]] = Field(default_factory=dict)
    push_auth_secret: str = ""
    enforce_push_auth: bool = False

    @property
    def alias_lookup(self) -> dict[str, str]:
        lookup: dict[str, str] = {}
        for label, raw_ids in self.model_aliases.items():
            for raw_id in raw_ids:
                lookup[raw_id] = label
        return lookup
```

### 6.2 删除旧模型

删除 `config.py`（`Config` / `ProviderConfig` 中的 `Config` 类）和 `config_loader.py`
（`load_config` / `DEFAULT_CONFIG_PATH` 的 v1 用途）。`ProviderConfig` 作为各 Provider 构造
函数的「凭据值对象」保留——它已与 v1 配置解耦，仅由
`provider_registry._to_provider_config` 从 `V2InstanceConfig` 构造。`ProviderConfig` 可
移入 `instance_config.py` 或留在原文件并删除 `Config`；实施时选择改动最小的位置，预计
留在 `config.py` 仅保留 `ProviderConfig`。

`DEFAULT_CONFIG_PATH`（`config_loader.py`）迁至 `instance_config.py`，指向项目根
`config.json`。

### 6.3 加载与校验：`load_v2_config` → 容错加载

当前 `load_v2_config` 遇到第一个非法实例即整体抛错。改为收集每个实例的校验错误，跳过
非法实例，返回有效配置 + 每实例错误映射：

```python
@dataclass
class LoadResult:
    config: V2Config            # 只含通过校验的实例
    instance_errors: dict[str, str]  # instance_id -> 错误信息（被跳过的实例）
    config_error: str | None    # 顶层结构错误（JSON / 顶层 schema），整体不可用时非 None
```

校验分层：

1. **顶层结构错误**（整体不可用，`config_error` 非 None，`config` 为空配置）：
   - 文件不存在；
   - JSON 解析失败；
   - `V2Config.model_validate` 顶层失败（如 `providers` 不是对象、出现未知顶层字段）。
2. **单实例错误**（跳过该实例，记入 `instance_errors`）：对每个 `instance_id` 单独执行
   `V2InstanceConfig.model_validate` + `_validate_instance`。任何异常只跳过该实例，不影响
   其他实例。包括：
   - 未知 `type`（本次重点修复的场景）；
   - 非法 `mode`、空 `label`、不支持的字段、缺失必填凭据；
   - 实例 ID 不匹配 `[A-Za-z0-9._-]+`。

`_validate_instance` 内对未知 `type` 的判定改为「跳过」而非「抛错使整配置失败」——由
`load_v2_config` 的 per-instance try/except 捕获，错误文案记入 `instance_errors`。

## 7. 热重载

### 7.1 机制

新增一个配置服务对象（模块级单例，`web.py` 或新建 `config_service.py`）：

```python
class ConfigService:
    def __init__(self, path: Path): ...
    def get(self) -> LoadResult:
        # stat mtime；与缓存 mtime 相同则直接返回缓存
        # 不同则重新加载（容错），更新缓存，触发订阅 manager 重载
```

- 每次 `/api/usage/v2`、`/api/status/v2`、`/api/push/v2/*`、`/api/usage/report`、
  `/api/usage/timeseries` 处理时调用 `config_service.get()`（一次 stat），命中缓存时几乎
  零成本。
- mtime 变化时重新解析、容错校验，得到新 `LoadResult`。
- 若新 `LoadResult` 与旧的「有效实例集合」不同，或顶层 `config_error` 状态翻转，触发
  `V2RuntimeManager.reload(new_config)`。
- 单次请求内多次取配置共享同一 `LoadResult`（请求级缓存，避免同请求多次 stat）。

### 7.2 `V2RuntimeManager.reload`

新增方法，原子地替换运行时配置：

```python
def reload(self, new_config: V2Config) -> None:
    # 1. 停止旧 fetch task
    # 2. 旧配置中有、新配置中没有的实例：清除其 _fetch_results / _pushed_results /
    #    _pushed_at / _prev_results / _consecutive_failures
    # 3. self._config = new_config
    # 4. restore_snapshots()：只恢复新配置中仍存在的实例
    # 5. 若 has_fetch_instances：重启 fetch task
```

- push 实例的 `_pushed_results` 若实例仍在配置中则保留（避免热重载清掉刚推送的数据）；
  仅清除被删除实例的结果。
- `order` / `label` 变更通过下次 `get_usage_v2` 排序自然生效；标题变更在
  `get_usage_v2` 用当前 config 重新拼 `make_card_title`，无需额外处理。
- 顶层 `config_error` 时 manager 置 `_enabled=False`、`_config_error=...`、
  `_config=空配置`，fetch task 停止，`/api/usage/v2` 返回 `[]`，`/api/status/v2` 暴露错误。

### 7.3 启动

`lifespan` 启动时调用 `config_service.get()` 初始化 manager；之后不再在 lifespan 内主动
重载——由请求路径上的 mtime poll 驱动。

## 8. `/api/status/v2` 契约

删除旧 `GET /api/status`（v1 语义）。保留 `GET /api/status/v2` 路径不变，增加
`instance_errors` 字段：

```json
{
  "enabled": true,
  "last_updated": "2026-07-21T12:30:00+08:00",
  "config_error": null,
  "instance_errors": {
    "some-new-type": "instance 'some-new-type': unknown type 'foo'"
  }
}
```

- `enabled`：manager 是否启用（配置存在且顶层结构合法）。
- `last_updated`：v2 最近刷新 / push 时间。
- `config_error`：顶层结构错误（整体不可用）。
- `instance_errors`：被跳过实例的错误映射（含未知 type）。

前端读取 `instance_errors`，在余额 grid 上方以一行小字提示「N 个实例配置错误已忽略，
详见 /api/status/v2」；不在 grid 内为跳过的实例渲染错误卡片（符合「仅 status 告警」决策）。

`/api/usage/v2` 路径与响应结构不变（仍返回 `instance_id` / `type` / `instance_label` /
`type_display_name` 等字段）。

## 9. 订阅余额 v1 删除范围

### 9.1 后端 `web.py`

删除：

- v1 内存状态：`_cached_results`、`_pushed_results`、`_pushed_at`、
  `_consecutive_failures`、`_prev_results`、`_last_updated`。
- v1 常量与表：`_PUSH_ONLY_PROVIDERS`、`_PROVIDER_DISPLAY_NAMES`。
- v1 函数：`_build_provider`、`_fetch_one`、`_fetch_all_usage`、
  `_background_refresh`、`_build_display_order_map`、`_restore_push_card_snapshots`、
  `_persist_usage_snapshot`、`_persist_push_card_snapshot`。
- v1 端点：`GET /api/usage`、`GET /api/status`（旧语义）、`POST /api/push/antigravity`、
  `/api/push/cursor`、`/api/push/mimo`、`/api/push/claude`、`/api/push/grok`。
- 旧 `load_config` / `DEFAULT_CONFIG_PATH`（v1 用途）引用，改为 `config_service`。

保留并改造：

- `GET /api/usage/v2`、`GET /api/status/v2`、`POST /api/push/v2/{instance_id}`：保留路径，
  改为从 `config_service` 取配置；`_verify_push_auth_v2` 用 `config_service` 的
  `push_auth_secret`。
- `POST /api/usage/report`：`_verify_push_auth` / `config.enforce_push_auth` 改为读
  `config_service` 的统一 `push_auth_secret` / `enforce_push_auth`。source 表写入不变。
- `GET /api/usage/timeseries`：`config.alias_lookup` 改为读 `config_service` 的
  `V2Config.alias_lookup`。
- `lifespan`：只初始化模型用量 DB（`usage_store.init_db`）和 v2 manager；删除
  `_restore_push_card_snapshots` 调用与 v1 `_background_refresh` task。

### 9.2 `usage_store.py`

删除 v1 订阅余额相关：

- 表 `provider_snapshot`、`provider_item`、`push_card_snapshot` 的建表语句。
- 函数 `record_snapshot`、`upsert_push_card_snapshot`、`load_push_card_snapshots`。
- 仅供上述函数使用的解析辅助：`_parse_flexible_number`、`_parse_limit_used`（确认仅
  `record_snapshot` 使用后删除）。

保留：`usage_point`、`source` 表与 `upsert_points`、`query_timeseries`、
`update_source_auth`、`get_stale_sources`、`dismiss_stale_source`、`get_source_auth_status`、
`init_db` / `init_schema`（移除 v1 表后）。

旧 DB 文件中残留的 v1 表不动（不 DROP），仅不再写入。

### 9.3 `__main__.py`

- 删除 `_run_cli` 及非 `--web` 分支。
- 删除 `--v2-config` 参数。
- `main()` 直接以 `--web` 方式启动；若未带 `--web` 也默认进入 Web（或要求显式 `--web`，实施
  时按最小改动决定——倾向：未带 `--web` 时打印提示并退出，因为 CLI 已删）。
- `--config` / `--usage-db` / `--host` / `--port` 保留。

### 9.4 前端 `index.html`

- 删除 `initTabs` 中 v1/v2 分支与动态插入「订阅余额（新）」按钮的逻辑；`balanceSource`
  变量删除。
- tab bar 只保留静态两个按钮：`订阅余额`（`data-tab="balance"`）与`模型用量`（`data-tab="usage"`）。
  删除 `balance-v2` tab 与 `validTabs` 中的对应项。
- `balance` tab 始终请求 `/api/usage/v2` + `/api/status/v2`（路径不变）。`refresh()` 改为
  调用 v2 接口并按 `instance_errors` 渲染顶部提示。
- 60 秒轮询：`balance` tab 刷新 v2 数据。
- `renderCard` 不变；卡片仍以 `instance_id` 为稳定 key。

## 10. 模块边界

| 模块 | 职责 |
|---|---|
| `instance_config.py` | `V2Config`（含 `model_aliases`）、`V2InstanceConfig`、`ProviderConfig`（或留在 `config.py`）、`LoadResult`、容错 `load_v2_config`、路径解析、`DEFAULT_CONFIG_PATH` |
| `config_service.py`（新） | `ConfigService`：mtime poll、缓存、触发 manager reload |
| `provider_registry.py` | 不变（类型注册、factory、push converter） |
| `provider_instances.py` | `V2RuntimeManager` 增加 `reload`；`get_status` 增加 `instance_errors` |
| `provider_v2_store.py` | 不变 |
| `usage_store.py` | 移除 v1 订阅余额表与函数 |
| `web.py` | 删除 v1 端点/状态/后台任务；端点改用 `config_service` |
| `__main__.py` | 删除 CLI；删除 `--v2-config` |
| `index.html` | 单余额 tab，始终 v2 接口 |

## 11. 配置文件迁移（用户侧）

提供 `config.json.example`（合并版，取代旧 `config.json.example` 与
`config.v2.json.example`）与 README 段落，说明：

1. 将 `config.v2.json` 的 `providers`、`push_auth_secret`、`enforce_push_auth` 合入
   `config.json`。
2. 将 `config.json` 的 `model_aliases` 保留。
3. 统一 `push_auth_secret` 为单一值，同步给所有 push agent 与 ai-usage-agent。
4. 删除 `config.v2.json`，重启服务。
5. 以后修改 `config.json` 无需重启。

`data/config.json`（当前 `providers` 为空）与 `data/config.v2.json` 需手动合并为
`data/config.json`。

## 12. 错误处理

| 场景 | 行为 |
|---|---|
| `config.json` 不存在 | manager disabled，`/api/status/v2` 的 `config_error` 提示，余额 grid 显示「尚未配置」 |
| `config.json` JSON / 顶层 schema 非法 | manager disabled，`config_error` 暴露错误，余额 grid 显示配置错误卡片 |
| 单实例未知 `type`（本次重点） | 跳过该实例，记入 `instance_errors`，其余实例正常显示，grid 上方一行提示 |
| 单实例其他校验错误 | 同上：跳过 + `instance_errors` |
| 热重载后实例被删除 | 清除其内存结果，不再显示 |
| 热重载后实例仍存在 | 保留其 push 结果；`order`/`label` 下次排序/标题自然生效 |
| 顶层结构恢复合法 | manager 重新 enabled，恢复 fetch |
| push 认证失败（软模式） | 记日志，继续处理 |
| push 认证失败（强制模式） | 401 |
| fetch 单实例失败 | 沿用三次失败回退规则 |

## 13. 测试策略

- **配置容错**：未知 type、非法 mode、空 label、未知字段、缺失必填凭据分别只跳过对应
  实例；其余实例加载成功；`instance_errors` 内容正确。顶层 JSON 非法时 `config_error`
  非 None 且 `providers` 为空。
- **热重载**：
  - 修改 mtime 后下次 `get()` 返回新配置；
  - 未改 mtime 时返回缓存（不重载）；
  - 删除实例后其结果从 `/api/usage/v2` 消失；
  - 新增 fetch 实例后 fetch task 调度它；
  - 顶层结构从合法变非法再变回合法，manager enabled 状态正确翻转。
- **模型用量不受影响**：`/api/usage/timeseries` 仍使用 `model_aliases`；`/api/usage/report`
  认证使用统一 `push_auth_secret`；`usage_point` 行数与聚合不变。
- **v1 删除回归**：`GET /api/usage`（旧）、`/api/push/{category}` 返回 404；v1 相关测试
  删除或改写。
- **前端**：单余额 tab 始终请求 v2 接口；`instance_errors` 非空时顶部提示出现；其余实例
  正常渲染。
- 全量 `pytest` 通过。

## 14. 验收标准

1. `config.json` 为唯一配置文件；`config.v2.json` 与 `--v2-config` 不再存在。
2. 修改 `config.json` 后下一次请求即生效，无需重启。
3. `config.json` 含一个未知 `type` 的实例时，余额页面仍正常显示其余实例，未知实例不出现，
   `/api/status/v2` 的 `instance_errors` 含其错误。
4. 旧 `/api/usage`、`/api/push/{category}` 端点已删除；`/api/usage/v2`、`/api/status/v2`、
   `/api/push/v2/{instance_id}` 继续工作。
5. 非 Web CLI 模式已删除。
6. `usage_point`、`source`、模型用量图表与 stale source 告警行为不变。
7. `provider_snapshot` / `provider_item` / `push_card_snapshot` 不再被写入。
8. 全量后端、前端回归通过。
