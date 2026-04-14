from pydantic import BaseModel


class UsageDetailResponse(BaseModel):
    model_code: str
    usage: int


class LimitResponse(BaseModel):
    duration: int
    time_unit: str
    limit: str
    used: str
    remaining: str
    reset_time: str | None = None
    usage_details: list[UsageDetailResponse] = []
    limit_type: str = ""


class TokenUsageResponse(BaseModel):
    period: str
    total_tokens: int
    total_calls: int


class UsageResponse(BaseModel):
    provider: str
    user_id: str | None = None
    membership_level: str | None = None
    limits: list[LimitResponse] = []
    balances: dict[str, str] = {}
    token_usage: list[TokenUsageResponse] = []


class CodexPushRequest(BaseModel):
    five_hours_percentage: float
    five_hours_reset_time: int
    one_week_percentage: float
    one_week_reset_time: int


class AntigravityPushRequest(BaseModel):
    gemini_3_1_pro_percentage: float
    gemini_3_1_pro_reset_time: str
    gemini_3_flash_percentage: float
    gemini_3_flash_reset_time: str
    claude_series_percentage: float
    claude_series_reset_time: str
