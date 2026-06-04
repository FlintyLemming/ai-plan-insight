FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml ./
COPY config.json ./
RUN pip install --no-cache-dir .

COPY ai_plan_insight/ ./ai_plan_insight/

CMD ["python", "-m", "ai_plan_insight"]
