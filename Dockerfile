FROM python:3.12-slim

ENV TZ=Asia/Shanghai

WORKDIR /app

COPY pyproject.toml ./
COPY ai_plan_insight/ ./ai_plan_insight/
RUN pip install --no-cache-dir .

CMD ["python", "-m", "ai_plan_insight"]
