FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN curl -L --connect-timeout 30 --retry 5 --retry-delay 5 \
    "https://github.com/aliyun/aliyun-cli/releases/download/v3.3.4/aliyun-cli-linux-3.3.4-amd64.tgz" \
    -o /tmp/aliyun-cli.tgz && \
    tar zxvf /tmp/aliyun-cli.tgz -C /tmp && \
    mv /tmp/aliyun /usr/local/bin/ && \
    rm -f /tmp/aliyun-cli.tgz && \
    chmod +x /usr/local/bin/aliyun && \
    aliyun version

COPY pyproject.toml ./
RUN pip install --no-cache-dir .

COPY ai_plan_insight/ ./ai_plan_insight/

CMD ["python", "-m", "ai_plan_insight"]
