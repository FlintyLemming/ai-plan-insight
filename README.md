# AI Plan Insight

聚合查看多个 AI 编码订阅的用量和余额信息，支持 CLI 和 Web 两种使用方式。

## 支持的 Provider

| Provider | 说明 | 配置字段 |
|---|---|---|
| Kimi | Kimi API 用量查询 | `api_key` |
| BigModel | 智谱 GLM Coding Plan | `api_key` |
| AIPing | AIPing 余额查询 | `api_key` |
| Alibaba Cloud | 阿里云 AI 代金券 | `access_key_id` + `access_key_secret` |

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
  -p 8765:8765 \
  -v ~/.ai_plan_insight.json:/root/.ai_plan_insight.json:ro \
  git.mitsea.com/flintylemming/ai-plan-insight:latest \
  python -m ai_plan_insight --web --host 0.0.0.0 --port 8765
```

容器启动后访问 `http://localhost:8765` 查看 Web 界面。

### Docker Compose

创建 `docker-compose.yaml`：

```yaml
services:
  ai-plan-insight:
    image: git.mitsea.com/flintylemming/ai-plan-insight:latest
    container_name: ai-plan-insight
    restart: unless-stopped
    ports:
      - "8765:8765"
    volumes:
      - ./config.json:/root/.ai_plan_insight.json:ro
    command: python -m ai_plan_insight --web --host 0.0.0.0 --port 8765
    logging:
      driver: json-file
      options:
        max-size: "10m"
        max-file: "3"
```

在同目录下放置 `config.json`，然后：

```bash
docker compose up -d
```

### 配置文件

在宿主机创建 `~/.ai_plan_insight.json`，按需填写要使用的 Provider，参考 [config.json.example](config.json.example)：

```json
{
  "providers": {
    "kimi": {
      "api_key": "YOUR_KIMI_API_KEY"
    },
    "bigmodel": {
      "api_key": "YOUR_BIGMODEL_API_KEY"
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
- `POST /api/push/codex` — 接收 Codex 的用量推送
- `POST /api/push/antigravity` — 接收 Antigravity 的用量推送

后台数据每 30 秒自动刷新。客户端推送的数据将立刻被记录，并会在前端被合并展示。

### 用量推送 (Push API)

对于无法直接配置 API 密钥拉取的服务 (如 Codex 和 Antigravity)，你可以通过 API 形式，将用量数据主动 `POST` 给面板。

#### 1. 推送 Codex 用量

**参数说明：**
分别传入 5 小时 和 一周 的使用百分比 (`_percentage`，不带百分号的数字) 及所对应的 Unix 重置时间戳 (`_reset_time`)。

**Curl 示例：**
```bash
curl -X POST http://localhost:8765/api/push/codex \
  -H "Content-Type: application/json" \
  -d '{
    "five_hours_percentage": 22.5,
    "five_hours_reset_time": 1766000000,
    "one_week_percentage": 10.0,
    "one_week_reset_time": 1766000000
  }'
```

#### 2. 推送 Antigravity 用量

**参数说明：**
分别传入 `gemini_3_1_pro`、`gemini_3_flash` 以及 `claude_series` 三款模型证书的 5 小时用量百分比 (`_percentage`) 和重置时间 (`_reset_time`，格式为标准的 ISO8601 字符串)。

**Curl 示例：**
```bash
curl -X POST http://localhost:8765/api/push/antigravity \
  -H "Content-Type: application/json" \
  -d '{
    "gemini_3_1_pro_percentage": 5.0,
    "gemini_3_1_pro_reset_time": "2024-04-15T00:00:00Z",
    "gemini_3_flash_percentage": 0.5,
    "gemini_3_flash_reset_time": "2024-04-15T00:00:00Z",
    "claude_series_percentage": 90.5,
    "claude_series_reset_time": "2024-04-15T00:00:00Z"
  }'
```
