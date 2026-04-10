FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml ./
RUN pip install --no-cache-dir .

COPY ai_plan_insight/ ./ai_plan_insight/

ENV HOST=0.0.0.0
ENV PORT=8765

EXPOSE 8765

ENTRYPOINT ["python", "-m", "ai_plan_insight"]
CMD ["--web", "--host", "0.0.0.0", "--port", "8765"]
