# 模型价格手工覆盖（数据库 + 管理页）

日期：2026-07-28

## 问题

「总模型用量」的 USD 成本按每个原始 model id 的单价计算，价格来自 LiteLLM → OpenRouter → models.dev 三个上游源的回退链（`pricing.py`）。三个源都查不到的模型只统计 Token、不计成本，页面上表现为 `cost_usd = null` / `cost_partial = true`。

供应商自有前缀的模型名不会出现在任何上游源里。实测：

| model id | 上游查得到？ |
|---|---|
| `grok-4.5` | ✅ LiteLLM `xai/grok-4.5` |
| `cursor-grok-4.5-high` | ❌ |
| `cursor-grok-4.5-high-fast` | ❌ |

这类模型只会越来越多（各家的 `-fast` 加速通道、Cursor 的 `cursor-*` 系列），需要一个不改代码、不重启就能补价的入口。

## 目标

- 在管理页里给上游查不到价的模型手工填单价，保存后立即生效。
- 只暴露真正需要处理的模型：面板按实际用量列出「上游取不到价格」的 model id，不用手抄 id。
- 手填价格和其他配置一样存数据库，进导入导出备份。

## 非目标

- 不做通配/前缀覆盖（`cursor-*`）。Cursor 的 fast 通道和非 fast 不同价，一条规则盖一片正是要避免的。
- 不覆盖上游已有价格的模型。想改上游价不在本次范围内。
- 不进 `config.json`。该文件已冻结为 v0.12 的迁移源，不再新增字段。

## 关键决策

1. **精确匹配**。覆盖只对大小写归一后完全相同的 model id 生效，不走上游那套候选链（去 `:后端` 后缀、去 `org/` 前缀、去日期尾缀、版本分隔符互换）。面板本来就按实际用量列表，新出现的变体会自己冒出来成为新的一行，不会静默套用到别的模型上。
2. **覆盖优先于全部上游源**。查价顺序变成：手填 → LiteLLM → OpenRouter → models.dev。
3. **单价单位是 USD / 百万 Token**，和各家定价页、models.dev 一致，可以直接照抄；转成 per-token 只在一个地方发生。
4. **已填过价的条目继续留在同一张表里**。表 = 上游无价的全部模型，已填的显示填过的数字，没填的是空行；改价和清空都在原地做。否则填完即消失，之后没有入口修改或删除。
5. **热生效，不重启**。和管理页其余配置的行为一致。

## 架构

### 价格怎么送到运行中的服务

`model_prices` 与 `model_aliases` 是同一类东西——都是「总模型用量」视图的展示配置，都存数据库，都在管理页维护。因此把它挂进 `V2Config`，由 `config_store.load_config()` 一起读出来：

```
管理页保存 → replace_prices() → _bump_version()
           → ConfigService 下次 get() 发现 config_version 变了 → 重新 load_config()
           → /api/usage/timeseries 拿到新的 cfg.model_prices
```

`ConfigService` 现成的版本轮询直接白送了热生效，不需要新的轮询或推送机制。`ConfigService._signature()` 只看 providers 和错误，所以改价不会误触发订阅实例的 reload。

被否决的两个方案：让 `PricingService` 自己连数据库（会让目前只认一个缓存文件路径的独立模块反向依赖 `config_store`，测试也得开始造 DB）；管理页保存时直接 push 到 `PricingService` 单例（多 worker 下失效，也和项目已有的版本轮询模式不一致）。

### 分层

`pricing.py` 不 import `config_store`，只多两个纯函数当边界：

```python
def overrides_from_config(mapping: dict[str, dict]) -> dict[str, ModelPrice]:
    """{id: {input, output, cache_read, cache_write}}（USD/百万 Token）→ per-token。"""

def with_overrides(base, overrides: dict[str, ModelPrice]):
    """精确匹配 overrides，未命中则回退到 base（= PricingService.price_for）。"""
```

`PricingService` 本身完全不动：它仍然只负责三个上游源和它们的磁盘缓存。

## 数据模型

`config_store.py` 新增表，由 `init_config_schema()` 幂等建表（和其余配置表一样，老库下次启动自动获得）：

```sql
CREATE TABLE IF NOT EXISTS model_price (
    model_id             TEXT PRIMARY KEY,   -- 归一化为小写的原始 model id
    input_per_mtok       REAL NOT NULL,
    output_per_mtok      REAL NOT NULL,
    cache_read_per_mtok  REAL NOT NULL DEFAULT 0,
    cache_write_per_mtok REAL NOT NULL DEFAULT 0,
    updated_at           TEXT NOT NULL
)
```

新增函数，逐一对照现有的别名实现：

| 函数 | 对照 | 说明 |
|---|---|---|
| `validate_prices(mapping)` | `validate_aliases` | 纯校验，抛 `ValueError` |
| `get_prices(conn)` | `get_aliases` | `{model_id: {input, output, cache_read, cache_write}}`，按 model_id 排序 |
| `_write_prices(conn, mapping)` | `_write_aliases` | 在调用方事务内整表替换 |
| `replace_prices(conn, mapping)` | `replace_aliases` | 校验 → 写 → `_bump_version` → commit |

校验规则：

- 顶层必须是对象；model_id 必须是非空字符串（存储前 `.strip().lower()`）
- 每个值必须是对象，键只允许 `input` / `output` / `cache_read` / `cache_write`，出现别的键报错（挡住 `cache-read` 这类拼写错误）
- 四个值都必须是有限非负实数；`bool` 按非法处理（避免 `true` 被当成 1）
- 缺省的键按 0 处理；但 `input` 与 `output` 不能同时为 0——那样的条目没有意义，报错。**清空一行 = 从 mapping 里删掉这一项**，由管理页负责，不是往下传一个全 0 条目

`V2Config` 新增字段：

```python
class ModelPriceConfig(BaseModel):
    model_config = {"extra": "forbid"}
    input: float = 0.0
    output: float = 0.0
    cache_read: float = 0.0
    cache_write: float = 0.0

class V2Config(BaseModel):
    ...
    model_prices: dict[str, ModelPriceConfig] = Field(default_factory=dict)
```

若 pydantic 对 `model_` 前缀字段告警，按 `AliasesRequest` 已有的做法加 `"protected_namespaces": ()`。

`load_v2_config()`（config.json 旧格式加载器）**不动**：`model_prices` 不是 config.json 的字段，旧文件里出现它仍应按「未知顶层字段」拒绝。`is_config_empty()` 也不动——它决定要不要执行 config.json 迁移，而价格从来不来自 config.json；只有价格、没有实例的库对迁移判断而言仍是空库。

### 导入导出

`export_config()` 增加 `"model_prices": get_prices(conn)`；`parse_import_payload()` 增加校验并把返回值从 4 元组扩成 5 元组；`import_config()` 调 `_write_prices()`。

导入是整体替换，所以导入一份不含 `model_prices` 的旧备份会清空价格表——与 `model_aliases` 现有行为一致。

## 用量查询

`usage_store.py` 新增：

```python
def list_raw_model_usage(conn, days: int) -> list[tuple[str, int]]:
    """最近 days 天内有 Token 的原始 model_id 及其总 Token，按总量降序。"""
```

```sql
SELECT model_id,
       SUM(input_tokens + output_tokens + cache_read_tokens
           + cache_write_tokens + reasoning_tokens) AS total
FROM usage_point
WHERE date >= ?
GROUP BY model_id
HAVING total > 0
ORDER BY total DESC
```

注意这里要的是**原始 id**，不经别名归并——计价本来就按原始 id 走。`date` 是 `YYYY-MM-DD` 文本，字典序比较即可。

窗口取 90 天，和图表的最大范围一致。

## 接口

两个受保护接口，放在 `admin_api.py` 的模型别名一节之后。

### `GET /api/admin/model-prices`

```json
{
  "window_days": 90,
  "pricing_ready": true,
  "models": [
    {
      "model_id": "cursor-grok-4.5-high-fast",
      "total_tokens": 1234567,
      "price": { "input": 4, "output": 18, "cache_read": 1, "cache_write": 0 }
    },
    { "model_id": "cursor-grok-4.5-high", "total_tokens": 456789, "price": null }
  ]
}
```

列表 = 「窗口内有用量、且上游查不到价的 id」∪「已有覆盖记录的 id」。并上第二个集合是为了保证每一条覆盖永远可见、可改、可删——否则上游哪天补上了这个模型，对应行会从表里消失，而覆盖仍在静默生效。`price` 为 `null` 表示还没填。

`pricing_ready` 来自 `PricingService.ready`；处理器先 `await ensure_ready()`。价格数据没就绪时「上游无价」对所有模型都成立，列表会是垃圾，此时返回 `pricing_ready: false` 且 `models` 为空数组，前端不渲染表格而是提示稍后刷新。价格服务初始化失败（`_pricing_service is None`）走同一条分支。

### `PUT /api/admin/model-prices`

```json
{ "model_prices": { "cursor-grok-4.5-high": { "input": 2, "output": 6, "cache_read": 0.5 } } }
```

整表替换（与 `PUT /api/admin/aliases` 一致）。校验失败返回 **422**，`detail` 是具体原因——沿用别名接口的状态码。

请求模型需要 `model_config = {"protected_namespaces": ()}`。

## 管理页

`admin.html` 在「模型别名」面板之后新增「模型价格」面板：

- 说明文字：成本按每个原始模型 ID 的单价计算，价格自动从 LiteLLM / OpenRouter / models.dev 获取；这里只列出这三个源都查不到价的模型，填了才会计入成本。单位 **USD / 百万 Token**，照抄各家定价页即可。
- 表头：模型 ID ｜ 近 90 天 Token ｜ 输入 ｜ 输出 ｜ 缓存读取 ｜ 缓存写入 ｜ 操作
- 四个价格是 `type=number min=0 step=any` 输入框；「清空」按钮把一行四个框清空
- 「保存价格」提交整张表：四个框全空的行不进 payload（= 删除该覆盖）；只填了一部分的行，空框按 0 提交。若一行只填了缓存价、输入和输出都是 0，服务端按非法条目返回 422，面板原样展示错误信息
- 空态：「近 90 天内没有缺少价格的模型」
- `pricing_ready=false`：不渲染表格，显示「价格数据尚未就绪，请稍后刷新」

`web.py` 的 `usage_timeseries` 处理器里，`cfg` 已经在现有代码中取到，只需：

```python
overrides = pricing.overrides_from_config(cfg.model_prices) if cfg else {}
price_of = pricing.with_overrides(_pricing_service.price_for, overrides)
```

每请求重建一个小 dict，开销可忽略。

## 附带修复：`xai` 前缀

`KNOWN_ORGS` 里有 `x-ai` 但没有 `xai`。LiteLLM 把 xAI 的模型放在 `xai/` 下，OpenRouter 放在 `x-ai/` 下，于是 `grok-4.5` 整个跳过 LiteLLM、落到 OpenRouter，而 OpenRouter 的列表接口不带缓存价——结果 `grok-4.5` 的缓存读取一直按 $0 计（实际 $0.5/百万）。

`KNOWN_ORGS` 加一个 `"xai"` 即可。与本设计其余部分独立，可以单独取舍。

## 测试

| 层 | 覆盖点 |
|---|---|
| `test_config_store.py` | 建表；校验拒绝负数/非数字/未知键/全 0；`replace_prices` + `get_prices` 往返；写入后 `config_version` 递增；导出含 `model_prices`；导入往返；导入旧备份清空价格表 |
| `test_pricing.py` | `overrides_from_config` 的单位换算与小写归一；`with_overrides` 精确命中、大小写不敏感、未命中时回退；`xai/grok-4.5` 出现在候选里 |
| `test_admin_api.py` | 两个接口都要登录；GET 只列上游无价的 id，且已覆盖的 id 一定在列；`pricing_ready=false` 时的响应；PUT 保存成功；PUT 非法输入返回 422 |
| `test_index_usage_view.py` 或新的 web 层测试 | 端到端：有覆盖时 `/api/usage/timeseries` 的 `cost_usd` 按覆盖价算 |
| `test_admin_page.py` | 面板渲染、填写、保存（playwright，未在 pyproject 声明，需手工安装；非 UI 套件是提交门槛） |

按 TDD 推进：每一项先写失败的测试，看着它以正确的理由失败，再写实现。

## 文档

README 在「API」一节前新增「模型价格」小节，说明成本来源、覆盖入口、单位；「管理页」一节的面板清单加一行。
