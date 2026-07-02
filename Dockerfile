FROM python:3.12-slim

ENV TZ=Asia/Shanghai

WORKDIR /app

COPY pyproject.toml ./
RUN pip install --no-cache-dir .

COPY ai_plan_insight/ ./ai_plan_insight/

CMD ["python", "-m", "ai_plan_insight"]
