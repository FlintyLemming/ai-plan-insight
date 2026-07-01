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


class HistoryModelUsageResponse(BaseModel):
    model_name: str
    total_tokens: int
    total_calls: int | None = None
    tokens_usage: list[int]


class HistoryUsagePeriodResponse(BaseModel):
    period: str
    granularity: str
    x_time: list[str]
    tokens_usage: list[int]
    model_call_count: list[int]
    total_tokens: int
    total_calls: int
    models: list[HistoryModelUsageResponse]


class ModelStatResponse(BaseModel):
    model: str
    total_tokens: int
    requests: int


class UsageResponse(BaseModel):
    provider: str
    user_id: str | None = None
    membership_level: str | None = None
    limits: list[LimitResponse] = []
    balances: dict[str, str] = {}
    token_usage: list[TokenUsageResponse] = []
    history_usage: HistoryUsagePeriodResponse | None = None
    model_stats: list[ModelStatResponse] = []
    error: str | None = None



class AntigravityPushRequest(BaseModel):
    gemini_3_1_pro_percentage: float
    gemini_3_1_pro_reset_time: str
    gemini_3_flash_percentage: float
    gemini_3_flash_reset_time: str
    claude_series_percentage: float
    claude_series_reset_time: str


class CursorPushRequest(BaseModel):
    membership: str
    billing_start: str
    billing_end: str
    autoPercentUsed: float | None = None
    apiPercentUsed: float | None = None


class MimoPushRequest(BaseModel):
    provider: str = "小米 MiMo Token Plan"
    user_id: str | None = None
    membership_level: str | None = None
    limits: list[LimitResponse] = []
    balances: dict[str, str] = {}


class ClaudeWindowPush(BaseModel):
    utilization: float
    resets_at: str


class ClaudePushRequest(BaseModel):
    seven_day: ClaudeWindowPush
    five_hour: ClaudeWindowPush
