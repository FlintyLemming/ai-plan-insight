# 推送卡片显示时长（管理页可配置）

日期：2026-07-28

## 问题

push 模式的订阅实例（Claude、Cursor、Grok、MiMo 等，由本地 Agent 推送数据）在 `V2RuntimeManager.get_usage()` 里有一个硬编码的过期阈值：

```python
PUSH_TTL_SECONDS = 30 * 60  # provider_instances.py
```

最后一次推送距今超过 30 分钟的实例会被过滤掉，不再出现在「订阅用量总览」面板上。阈值写死在代码里，改它要改源码重启，管理员无法根据实际推送频率自行调整。

## 目标

- 把这个阈值挪到管理页，单位为**小时**，可在页面上直接修改。
- 与其余配置一样存数据库、走 `config_version` 热生效，保存后无需重启。
- 进导入导出备份。

## 非目标

- 不扩展到 fetch 模式实例。fetch 实例由面板每 30 秒主动拉取，"没有新数据"在 fetch 上意味着拉取出错，应当以错误卡片的形式暴露给管理员看，而不是静默隐藏。
- 不进 `config.json`。该文件已冻结为迁移源，不新增字段。
- 不做按实例粒度的阈值。一个全局值已足够。

## 关键决策

1. **只作用于 push 模式**，与现有行为一致。fetch 卡片始终展示（缓存或错误）。
2. **单位是小时，允许小数**。最小 `0.5`（30 分钟），即旧硬编码值；默认 `1.0` 小时。允许 `0.5` / `1.5` 这类小数比限制成整数更直观，也保住了旧默认值作为下限。
3. **存 `app_setting` 键值表**，和 `push_auth_secret` / `enforce_push_auth` 一样，不另开新表。一个标量值不值得一张表。
4. **走 `config_version` 热生效**。保存时 `_bump_version()`，`ConfigService` 下次 `get()` 发现版本变了就 `reload()`，运行时立刻拿到新值。`ConfigService._signature()` 只看 providers 和错误，所以改 TTL 不会误触发订阅实例的 reload——但 `reload()` 本身会把新 `V2Config`（含新 TTL）交给管理器，`get_usage()` 下一次查询就用新值。这个副作用是想要的，不需要改 `_signature()`。
5. **TTL 从 `V2Config` 读取**，不再作为模块级常量。`get_usage()` 用 `self._config.push_card_ttl_hours`。

## 架构

### 配置怎么送到运行中的服务

```
管理页保存 → set_push_card_ttl() → _bump_version()
           → ConfigService 下次 get() 发现 config_version 变了
           → V2RuntimeManager.reload(new_config, ...)
           → get_usage() 用 self._config.push_card_ttl_hours 算 cutoff
```

`V2RuntimeManager.reload()` 已经会把新 `V2Config` 整体替换进来，TTL 字段随之上车，不需要新的传递路径。

## 数据模型

`config_store.py` 新增（与 push auth 一节并列）：

```python
PUSH_CARD_TTL_KEY = "push_card_ttl_hours"
PUSH_CARD_TTL_MIN = 0.5
PUSH_CARD_TTL_DEFAULT = 1.0

def get_push_card_ttl(conn) -> float:
    """读 app_setting 里的 push_card_ttl_hours，缺失返回默认 1.0。"""

def set_push_card_ttl(conn, hours: float) -> None:
    """校验 >= 0.5、有限实数；_set_setting + _bump_version + commit。
    供管理页单独调用。与 set_push_auth 同构。"""

def validate_push_card_ttl(hours) -> float:
    """校验并返回归一后的 float。供 import_config 复用，不写库。"""
```

校验规则（`validate_push_card_ttl`）：
- 必须是 `int` / `float`，`bool` 按非法处理（避免 `true` 当成 1）
- 有限（非 NaN / inf）
- `>= 0.5`，否则 `ValueError`

读取时键不存在或解析失败一律回落到默认 `1.0`，永不抛错——读路径在 `load_config` 里，不能因为一个坏设置拖垮整个配置加载。

与 push auth 的模式一致：`set_push_card_ttl` 给管理页单独用（自己 commit）；`import_config` 在整体事务里直接调 `_set_setting(conn, PUSH_CARD_TTL_KEY, str(validate_push_card_ttl(...)))`，不自己 commit。

`instance_config.py` 的 `V2Config` 新增字段：

```python
class V2Config(BaseModel):
    ...
    push_card_ttl_hours: float = 1.0
```

`load_v2_config()`（config.json 旧格式加载器）**不动**：`push_card_ttl_hours` 不是 config.json 的字段，旧文件里出现它仍按「未知顶层字段」拒绝。`is_config_empty()` 也不动——它判断要不要执行 config.json 迁移，而 TTL 从来不来自 config.json。

### 导入导出

`export_config()` 增加 `"push_card_ttl_hours": get_push_card_ttl(conn)`；`parse_import_payload()` 用 `validate_push_card_ttl` 校验并把返回元组再扩一位；`import_config()` 在整体事务内 `_set_setting(conn, PUSH_CARD_TTL_KEY, str(value))`（不自己 commit）。

导入是整体替换，所以导入一份不含该字段的旧备份会把 TTL 重置为默认 `1.0`——与 push auth 现有行为一致（旧备份里没有 `push_auth_secret` 会被清空）。

## 运行时

`provider_instances.py`：

- 删除模块级常量 `PUSH_TTL_SECONDS`。
- `get_usage()` 改为：
  ```python
  cutoff = now - timedelta(hours=self._config.push_card_ttl_hours)
  ```
- `disabled()` classmethod 里 `mgr._config = V2Config(providers={})`，TTL 取默认值，不影响 disable 态（disable 态下 `get_usage` 返回空列表，TTL 无关）。

## 接口

两个受保护接口，放在 `admin_api.py` 的 push auth 一节之后。

### `GET /api/admin/display-settings`

```json
{ "push_card_ttl_hours": 1.0 }
```

### `PUT /api/admin/display-settings`

```json
{ "push_card_ttl_hours": 1.5 }
```

校验失败返回 **422**，`detail` 是具体原因（"推送卡片显示时长不能小于 0.5 小时" / "必须是数字"），沿用 push auth 接口的状态码风格。

请求模型：

```python
class DisplaySettingsRequest(BaseModel):
    push_card_ttl_hours: float
```

## 管理页

`admin.html` 在「推送鉴权」面板之后新增「显示设置」面板：

- 说明文字：push 模式的订阅卡片在最后一次推送后超过该时长会从面板隐藏；fetch 模式卡片不受影响（始终展示）。单位**小时**，允许小数，最小 `0.5`。
- 一个 `type=number min=0.5 step=any` 输入框，标签「推送卡片最长显示时长（小时）」
- 「保存」按钮提交 `PUT /api/admin/display-settings`
- 成功提示与错误展示沿用其余面板的样式

## 测试

| 层 | 覆盖点 |
|---|---|
| `test_config_store.py` | `validate_push_card_ttl` 拒绝 `< 0.5`、非数字、`bool`、NaN/inf；`set_push_card_ttl` + `get_push_card_ttl` 往返；`config_version` 递增；`load_config` 返回存储值，缺失时返回默认 `1.0`；导出含字段；导入往返；旧备份导入重置为默认 |
| `test_provider_instances.py` | `get_usage` 隐藏超过 TTL 的 push 卡片、保留未过期的；TTL 改小后过期卡片消失（经 `reload`） |
| `test_admin_api.py` | 两个接口都要登录；GET 返回当前值；PUT 保存成功后 GET 反映新值；PUT `< 0.5` 返回 422 |
| `test_admin_page.py` | 面板渲染、填写、保存（playwright，未在 pyproject 声明，需手工安装；非 UI 套件是提交门槛） |

按 TDD 推进：每一项先写失败的测试，看着它以正确的理由失败，再写实现。

## 文档

README 在「管理页」一节的面板清单加一行「显示设置」。
