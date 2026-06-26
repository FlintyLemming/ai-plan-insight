from pydantic import BaseModel
from datetime import datetime


class UsageDetail(BaseModel):
    """Detail of usage for a specific model/feature."""
    model_code: str
    usage: int


class LimitDetail(BaseModel):
    """Rate limit detail for a specific time window."""
    duration: int
    time_unit: str
    limit: str
    used: str
    remaining: str
    reset_time: datetime | None = None
    usage_details: list[UsageDetail] = []
    limit_type: str = ""  # e.g. "TIME_LIMIT", "TOKENS_LIMIT"


class TokenUsagePeriod(BaseModel):
    """Token usage for a specific time period."""
    period: str  # "today", "7d", "30d"
    total_tokens: int
    total_calls: int


class HistoryModelUsage(BaseModel):
    """Per-model token usage in a historical usage period."""
    model_name: str
    total_tokens: int
    total_calls: int | None = None
    tokens_usage: list[int]


class HistoryUsagePeriod(BaseModel):
    """Historical token and call usage for a fixed period."""
    period: str
    granularity: str
    x_time: list[str]
    tokens_usage: list[int]
    model_call_count: list[int]
    total_tokens: int
    total_calls: int
    models: list[HistoryModelUsage]


class ModelStat(BaseModel):
    """Per-model usage statistics."""
    model: str
    total_tokens: int
    requests: int


class UsageInfo(BaseModel):
    """Standardized usage information across all providers."""
    provider: str
    user_id: str | None = None
    membership_level: str | None = None
    limits: list[LimitDetail] = []
    raw_response: dict
    # Balance-style info (used by providers like AIPing that don't have rate limits)
    balances: dict[str, str] = {}  # e.g. {"total": "100.00", "gift": "10.50", "recharge": "89.50"}
    # Token usage by period (used by providers like BigModel)
    token_usage: list[TokenUsagePeriod] = []
    # Historical token usage for chart rendering (used by providers like BigModel)
    history_usage: HistoryUsagePeriod | None = None
    # Per-model stats (used by providers like Codex)
    model_stats: list[ModelStat] = []
