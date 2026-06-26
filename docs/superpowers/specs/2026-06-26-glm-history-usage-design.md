# GLM 历史用量图表设计

日期：2026-06-26

## 背景

AI Plan Insight 当前通过 Web 面板展示各 Provider 的当前用量。GLM Coding Plan 和 GLM Coding Plan 国际版已经能通过 `model-usage` 接口取得今日、近 7 天、近 30 天的 token 和调用次数数据。此前项目还包含一套将 GLM 数据写入 PocketBase 的后台逻辑，但本次需求是删除 PocketBase 相关逻辑，直接在面板中提供一个简易历史数据显示功能。

## 目标

- 删除 PocketBase 的所有运行时逻辑和配置模型。
- 先在 GLM / GLM 国际版卡片上支持历史视图。
- 保持现有 GLM 卡片默认样式不变。
- 在 GLM 卡片右上角增加一个小切换按钮。
- 点击按钮后，在同一个卡片内切换为近 30 天模型调用图表。
- 历史视图需要展示每个模型分别用了多少 token，以及近 30 天调用次数信息。

## 非目标

- 不新增数据库或持久化历史数据。
- 不引入 Chart.js、ECharts 等前端依赖。
- 不改动其他 Provider 的卡片布局。
- 不新增复杂筛选、时间范围选择或导出功能。

## 已验证接口

GLM 国际版接口已使用配置中的 `bigmodel_international.api_key` 验证：

- `GET https://api.z.ai/api/monitor/usage/quota/limit` 返回 HTTP 200，并包含 `level` 和 `limits`。
- `GET https://api.z.ai/api/monitor/usage/model-usage` 在 30 天范围内返回 HTTP 200。
- 30 天响应为 daily 粒度，包含：
  - `x_time`: 日期数组。
  - `tokensUsage`: 每日总 token 数组。
  - `modelCallCount`: 每日总调用次数数组。
  - `totalUsage`: 近 30 天总调用次数、总 token、模型汇总。
  - `modelDataList`: 分模型每日 token 数据。

## 后端设计

### PocketBase 移除

- 删除 Web lifespan 中启动 `background_store_glm()` 的逻辑。
- 删除 `web.py` 对 `pocketbase_store` 的 import。
- 删除 `config.py` 中的 `PocketBaseConfig` 和 `Config.pocketbase` 字段。
- 保留 Push API 逻辑；它与 PocketBase 无关。
- `pocketbase_store.py` 文件可删除，避免后续误用。

### 数据模型

在标准模型中新增历史用量结构：

- `HistoryModelUsage`
  - `model_name: str`
  - `total_tokens: int`
  - `total_calls: int | None`
  - `tokens_usage: list[int]`
- `HistoryUsagePeriod`
  - `period: str`，固定为 `30d`。
  - `granularity: str`，接口返回 daily 时为 `daily`。
  - `x_time: list[str]`
  - `tokens_usage: list[int]`
  - `model_call_count: list[int]`
  - `total_tokens: int`
  - `total_calls: int`
  - `models: list[HistoryModelUsage]`

`UsageInfo` 和 API 响应 `UsageResponse` 增加 `history_usage: HistoryUsagePeriod | None`。

### Provider 行为

`BigModelProvider` 增加 `fetch_history_usage(days=30)`：

1. 使用北京时间计算近 30 天窗口：从今天 00:00 往前 29 天，到当前时间。
2. 调用现有 `MODEL_USAGE_URL`。
3. 验证 `code == 200` 后解析 daily 数据。
4. 从 `totalUsage` 读取近 30 天总 token 和总调用次数。
5. 从 `modelDataList` 读取每个模型的每日 token 数组。
6. 如果接口提供分模型调用次数，解析到 `total_calls`；如果没有，则 `total_calls` 为 `None`，前端显示 token 汇总，并在图表汇总区显示总调用次数。

`BigModelInternationalProvider` 继承 `BigModelProvider`，只覆盖 URL，因此可复用同一个历史解析逻辑。

### Web 聚合

`_fetch_one()` 在 provider 支持 `fetch_history_usage` 时调用它，并把结果写入 `UsageResponse.history_usage`。若历史接口失败，不影响当前用量显示；只记录 warning，返回没有历史数据的当前用量卡片。

## 前端设计

### 默认视图

- 现有 GLM 卡片默认内容和样式保持不变。
- 对没有 `history_usage` 的卡片不显示切换按钮。
- 非 GLM Provider 不显示切换按钮。

### 切换按钮

- 在 `.card-header` 右侧增加一个轻量按钮。
- 按钮文案：默认视图显示 `历史`，历史视图显示 `用量`。
- 切换状态保存在前端内存中，例如 `cardViewModes[provider] = 'usage' | 'history'`。
- 自动刷新后尽量保留用户当前切换状态。

### 历史视图内容

历史视图替换卡片主体内容，展示：

1. 近 30 天汇总：`总 token · 总调用次数`。
2. SVG 折线图：
   - 横轴使用 30 天日期。
   - 纵轴根据所有模型 token 最大值自动缩放。
   - 每个模型一条线，使用固定颜色轮换。
   - 不引入第三方图表库。
3. 模型列表：
   - 每行显示模型名、近 30 天 token 总量。
   - 如果分模型调用次数可得，则显示调用次数；否则不伪造分模型调用次数。
4. 如果历史数据为空，显示简短提示：`暂无近 30 天历史数据`。

## 错误处理

- 配额接口失败：沿用当前连续失败缓存逻辑。
- 历史接口失败：当前用量仍正常展示；历史按钮不显示或历史视图显示无数据。
- 历史数据数组长度不一致：以 `x_time` 长度为准截断或补零，避免图表渲染异常。
- token 数值为 0：图表显示空态或贴近底部的零线，不报错。

## 测试与验证

- 使用用户提供的 GLM 国际版 key 验证 `model-usage` 30 天接口返回 daily 数据。
- 增加或更新模型解析单元测试，覆盖：
  - `history_usage` 总量解析。
  - `modelDataList` 分模型 token 解析。
  - 缺少分模型调用次数时 `total_calls` 为 `None`。
- 本地启动 Web，验证：
  - GLM 卡片默认视图不变。
  - GLM 卡片右上角有切换按钮。
  - 点击后显示近 30 天折线图和模型 token 汇总。
  - 再次点击回到当前用量。
  - 非 GLM 卡片不受影响。

## 实施顺序

1. 移除 PocketBase runtime 入口和配置字段。
2. 新增历史用量模型和 API schema。
3. 在 GLM Provider 中实现近 30 天历史数据获取与解析。
4. 在 Web 聚合层把历史数据附加到 `UsageResponse`。
5. 在前端增加 GLM 卡片切换按钮、SVG 折线图和模型汇总列表。
6. 补充测试并用真实 GLM 国际版 key 做一次端到端验证。
