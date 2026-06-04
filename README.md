# AI Plan Insight

聚合查看多个 AI 编码订阅的用量和余额信息，支持 CLI 和 Web 两种使用方式。

## 支持的 Provider

| Provider | 说明 | 配置字段 |
|---|---|---|
| `codex` | 自购 Codex 中转站（Sub2API，quota 模式） | `api_key` + `base_url` |
| `codex_security` | 白嫖 Codex Security 中转（Sub2API，钱包模式） | `api_key` + `base_url` |
| BigModel | 智谱 GLM Coding Plan | `api_key` |
| Kimi | Kimi API 用量查询 | `api_key` |
| Huawei Cloud | 华为云账户余额 | `access_key_id` + `access_key_secret` |
| AIPing | AIPing 余额查询 | `api_key` |
| Alibaba Cloud | 阿里云 AI 代金券 | `access_key_id` + `access_key_secret` |
| Antigravity | Antigravity 用量（仅支持 Push） | — |

## 部署

### 拉取镜像

```bash
docker pull git.mitsea.com/flintylemming/ai-plan-insight:latest
```

### 运行容器

```bash
docker run -d \
  --name ai-plan-insight \
  --log-opt max-size=10m \
  --log-opt max-file=3 \
  -p 8000:8000 \
  -v ~/.ai_plan_insight.json:/root/.ai_plan_insight.json:ro \
  git.mitsea.com/flintylemming/ai-plan-insight:latest \
  python -m ai_plan_insight --web --host 0.0.0.0 --port 8000
```

容器启动后访问 `http://localhost:8000` 查看 Web 界面。

### Docker Compose

参考 [sample-compose.yaml](sample-compose.yaml)，复制为 `compose.yaml` 后按需修改：

```bash
cp sample-compose.yaml compose.yaml
docker compose up -d
```

### 配置文件

在宿主机创建 `~/.ai_plan_insight.json`，按需填写要使用的 Provider：

```json
{
  "providers": {
    "codex": {
      "api_key": "YOUR_CODEX_API_KEY",
      "base_url": "https://api2.ai.aiatechco.com"
    },
    "codex_security": {
      "api_key": "YOUR_CODEX_SECURITY_API_KEY",
      "base_url": "https://tinykittens.online"
    },
    "bigmodel": {
      "api_key": "YOUR_BIGMODEL_API_KEY"
    },
    "kimi": {
      "api_key": "YOUR_KIMI_API_KEY"
    },
    "huawei_cloud": {
      "user_name": "YOUR_HUAWEI_CLOUD_USER_NAME",
      "access_key_id": "YOUR_HUAWEI_CLOUD_ACCESS_KEY_ID",
      "access_key_secret": "YOUR_HUAWEI_CLOUD_SECRET_ACCESS_KEY"
    },
    "aiping": {
      "api_key": "YOUR_AIPING_API_KEY"
    },
    "alibaba_cloud": {
      "access_key_id": "YOUR_ACCESS_KEY_ID",
      "access_key_secret": "YOUR_ACCESS_KEY_SECRET"
    }
  }
}
```

不需要的 Provider 直接删除即可。

## CLI 使用

```bash
# 本地运行，需要先安装依赖
pip install .
ai-plan-insight

# 指定配置文件
ai-plan-insight --config /path/to/config.json
```

## API

Web 模式下提供以下接口：

- `GET /api/usage` — 返回所有 Provider 的用量数据
- `GET /api/status` — 返回最近一次刷新时间
- `POST /api/push/antigravity` — 接收 Antigravity 的用量推送

后台数据每 30 秒自动刷新。若某个 Provider 连续 3 次取数据失败，才会从页面消失。

### 用量推送 (Push API)

对于无法直接配置 API 密钥拉取的服务（如 Antigravity），可以通过 Push API 将用量数据主动推送给面板。

#### 推送 Antigravity 用量

分别传入 `gemini_3_1_pro`、`gemini_3_flash` 以及 `claude_series` 三款模型证书的 5 小时用量百分比 (`_percentage`) 和重置时间 (`_reset_time`，ISO8601 字符串)。

```bash
curl -X POST http://localhost:8000/api/push/antigravity \
  -H "Content-Type: application/json" \
  -d '{
    "gemini_3_1_pro_percentage": 5.0,
    "gemini_3_1_pro_reset_time": "2026-04-15T00:00:00Z",
    "gemini_3_flash_percentage": 0.5,
    "gemini_3_flash_reset_time": "2026-04-15T00:00:00Z",
    "claude_series_percentage": 90.5,
    "claude_series_reset_time": "2026-04-15T00:00:00Z"
  }'
```
