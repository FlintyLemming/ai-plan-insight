# 火山方舟 Coding Plan 用量 provider 设计

日期：2026-06-17

## 背景与目标

1. **禁用「自购 Codex 中转站」显示**：不再取数、不再展示，但 `CodexProvider` 代码全部保留，将来可在配置中恢复。
2. **新增「火山方舟 Coding Plan」provider**：在「白嫖 Codex Security 中转」之后插入一张用量卡片，展示 5 小时 / 周 / 月三个窗口的用量百分比与重置时间。

## 调研结论（均经实测验证）

使用用户提供的 AK/SK（`access_key_id` + `access_key_secret`），通过 HMAC-SHA256 签名实测火山方舟管控面 API：

- 鉴权方案：**管控面 API 必须用火山引擎 AK/SK 做 HMAC-SHA256 签名**，不支持 Ark API Key（Bearer）。用户最初给的 `c368ca6c-...` 是 Ark API Key（仅数据面 `/api/v3/*` 可用），不能调管控面接口；后补的 AK/SK 验证通过。
- 正确接口：**`GetCodingPlanUsage`**（不是 `GetAFPUsage`，也不是 `GetCFPUsage`）。`AFP` 对应 Agent Plan，Coding Plan 是独立的 `GetCodingPlanUsage`。
- Host：`ark.cn-beijing.volcengineapi.com`（管控面）。注意 `GetAFPUsage` 文档示例写的是 `ark.cn-beijing.volces.com`，但该 host 对管控面签名请求返回 401；正确 host 是 `ark.cn-beijing.volcengineapi.com`。
- 真实返回（HTTP 200，已取到真实用量）：

  ```json
  {
    "Status": "Running",
    "UpdateTimestamp": 1781674850,
    "QuotaUsage": [
      { "Level": "session",  "Percent": 4.103,  "ResetTimestamp": 1781690434 },
      { "Level": "weekly",   "Percent": 0.547,  "ResetTimestamp": 1782057600 },
      { "Level": "monthly",  "Percent": 0.2735, "ResetTimestamp": 1784303999 }
    ]
  }
  ```

  - `session` = 5 小时窗口，`weekly` = 周窗口，`monthly` = 月窗口。
  - `Percent` 为已用百分比，直接对应前端 `PERCENT` 类型进度条。
  - `ResetTimestamp` / `UpdateTimestamp` 为 **epoch 秒**（非毫秒，区别于 `GetAFPUsage`）。
- 模型明细不可得：`GetUsageDetails`（挂在 Agent Plan API 分组下）对该 Coding Plan 账号始终返回空 `Details`（近 7 天/31 天、Day/Hour 粒度、各种 PlanType 参数均试过）。因此**不做按模型明细**，只做 3 条百分比进度条。
- 签名实现：采用官方 `volcenginesdkcore.SignerV4` 同款算法，纯 httpx 自实现，**不引入 volcengine SDK**（避免拖入 cryptography 等 6 个传递依赖，与项目「slim Docker 镜像」目标一致）。签名算法已用官方 SDK 比对验证一致。

## 任务一：禁用「自购 Codex 中转站」显示

**做法**：从 `config.json` 删除 `"codex"` 配置项。

**原理**：`web.py::_fetch_all_usage` 遍历 `config.providers.keys()`，配置里没有 `codex` 就不会构建 `CodexProvider`、不会取数、不会展示。无需改动任何 Python 代码。`CodexProvider` 类与所有相关代码全部保留，将来恢复只需在 config 里加回 `codex` 即可。

**改动范围**：仅 `config.json`。

## 任务二：新增火山方舟 Coding Plan provider

### 架构

新增一个 `VolcEngineArkProvider`，文件 `providers/volcengine_ark.py`，遵循现有 `BaseProvider` 模式（`authenticate` / `fetch_usage` / `parse_usage`）。纯 httpx 发请求，自实现 HMAC-SHA256 签名，零新依赖。

签名逻辑独立放在 `providers/volcengine_signing.py`（从官方 `SignerV4` 提炼的 ~40 行工具函数），与 provider 业务逻辑解耦，方便单元测试。输入 `(host, method, path, query, body, ak, sk, region, service)`，输出完整的请求 headers。

### 签名要点（已验证）

- Host：`ark.cn-beijing.volcengineapi.com`
- Query 参数必须 URL-encode（`quote(..., safe="-_.~")`）并按字典序排序后拼 canonical string，否则 `SignatureDoesNotMatch`。
- Body 用 SHA256 hash 放入 `X-Content-Sha256` header。
- 派生 signing key 链：`sk → (short_date) → region → service → "request"`，每步 HMAC-SHA256。
- `Authorization: HMAC-SHA256 Credential={ak}/{scope}, SignedHeaders=content-type;host;x-content-sha256;x-date, Signature={sig}`，其中 `scope = {short_date}/{region}/{service}/request`，region=`cn-beijing`，service=`ark`。

### Provider 配置与调用

- 配置字段：`access_key_id` + `access_key_secret`（复用现有 `ProviderConfig` 已有的这两个字段，与 `huawei_cloud` 一致）。
- Provider name：`"火山方舟 Coding Plan"`
- API：`POST https://ark.cn-beijing.volcengineapi.com/?Action=GetCodingPlanUsage&Version=2024-01-01`，body `{}`。
- `authenticate()`：从 config 读 AK/SK，无需设置 headers（签名在请求时按 body 动态生成）。

### 解析逻辑

`GetCodingPlanUsage` 返回 `Result.QuotaUsage[]`，每项含 `Level` / `Percent` / `ResetTimestamp`。转成 3 个 `LimitDetail`：

| Level | time_unit | duration | limit | used | remaining | reset_time |
|---|---|---|---|---|---|---|
| `session` | `"5 小时"` | 1 | `"100"` | `f"{Percent:.2f}"` | `f"{100-Percent:.2f}"` | epoch秒→datetime(UTC) |
| `weekly` | `"周"` | 1 | `"100"` | `f"{Percent:.2f}"` | `f"{100-Percent:.2f}"` | 同上 |
| `monthly` | `"月"` | 1 | `"100"` | `f"{Percent:.2f}"` | `f"{100-Percent:.2f}"` | 同上 |

- `limit_type="PERCENT"`。
- `ResetTimestamp` 是 **epoch 秒**，解析用 `datetime.fromtimestamp(ts, tz=timezone.utc)`（不是 `/1000`）。
- `Status` 放到 `membership_level`（如 `"Running"`）。
- `UpdateTimestamp` 暂不展示（YAGNI）。

Level 到展示标签的映射：`session`→`5 小时`、`weekly`→`周`、`monthly`→`月`。遇到未知 Level 跳过。

### 接入点（3 处，与现有 provider 对称）

1. `web.py::_build_provider` 增加 `case "volcengine_ark": return VolcEngineArkProvider(config)` 分支。
2. `__main__.py::_run_cli` 的 match 增加 `case "volcengine_ark"` 分支。
3. `web.py::_provider_sort_key` 的 order 字典增加 `"火山方舟 Coding Plan": 12`（插在「白嫖 Codex Security 中转」=11 之后、「GLM Coding Plan」=20 之前）。

`config.json` 与 `config.json.example` 增加：

```json
"volcengine_ark": {
  "access_key_id": "AKLT...",
  "access_key_secret": "TXp..."
}
```

`README.md` 的 Provider 表格增加一行 `volcengine_ark | 火山方舟 Coding Plan | access_key_id + access_key_secret`。

### 展示效果

复用现有「rate-limit card」渲染（与 Antigravity/Cursor 相同的 PERCENT 进度条 + 重置时间）。3 条用量条：

```
5 小时        4.10%  [█░░░░░░░░░]  今天 18:00 重置
周            0.55%  [░░░░░░░░░░]  06-19 00:00 重置
月            0.27%  [░░░░░░░░░░]  06-30 23:59 重置
```

### 空数据与异常处理

- 若某 `Level` 缺失或 `Percent` 为 0，仍正常渲染 0% 进度条，不报错。
- `ResetTimestamp` 为 0 或缺失 → `reset_time=None`，不显示重置时间。
- 接口报错走现有「连续失败 3 次才从页面消失」机制（`_fetch_all_usage` 已实现）。

### 不做的事（YAGNI）

- 不做 `GetUsageDetails` 模型明细（Coding Plan 账号拉不到）。
- 不做 `GetAFPUsage`（Agent Plan 接口，与 Coding Plan 无关）。
- 不引入 volcengine SDK。
- 不改前端（复用现有 PERCENT 进度条卡片，无需新增渲染分支）。
- 不展示 `UpdateTimestamp`。

## 改动文件清单

| 文件 | 改动 |
|---|---|
| `config.json` | 删除 `codex` 项；新增 `volcengine_ark` 项 |
| `config.json.example` | 新增 `volcengine_ark` 项 |
| `README.md` | Provider 表格新增 `volcengine_ark` 行 |
| `ai_plan_insight/providers/volcengine_signing.py` | 新增：HMAC-SHA256 签名工具函数 |
| `ai_plan_insight/providers/volcengine_ark.py` | 新增：`VolcEngineArkProvider` |
| `ai_plan_insight/web.py` | `_build_provider` + `_provider_sort_key` 增加分支 |
| `ai_plan_insight/__main__.py` | match 增加 `volcengine_ark` 分支 |

## 测试

- `volcengine_signing.py`：单元测试覆盖签名生成，断言生成的 `Authorization` header 结构正确（Credential/SignedHeaders/Signature 三段齐全），且对固定输入产出确定性输出。
- `volcengine_ark.py`：单元测试用实测返回的 JSON 样本（见上文「真实返回」）断言解析出 3 个 `LimitDetail`，百分比为 4.10/0.55/0.27，重置时间正确，`Status` 进 `membership_level`。
- 端到端：启动 web，确认「自购 Codex 中转站」消失、「火山方舟 Coding Plan」卡片出现在「白嫖 Codex Security 中转」之后，3 条进度条显示真实百分比与重置时间。
