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


class UsageInfo(BaseModel):
    """Standardized usage information across all providers."""
    provider: str
    user_id: str | None = None
    membership_level: str | None = None
    limits: list[LimitDetail] = []
    raw_response: dict
    # Balance-style info (used by providers like AIPing that don't have rate limits)
    balances: dict[str, str] = {}  # e.g. {"total": "100.00", "gift": "10.50", "recharge": "89.50"}
