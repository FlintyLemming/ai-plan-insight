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


from pydantic import Field


class UsagePoint(BaseModel):
    """One (date, model) token row submitted by an agent client."""
    date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    model_id: str
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)


class UsageReportRequest(BaseModel):
    """POST /api/usage/report body.

    `source_id` is validated for presence (non-empty) in the route handler so
    a missing/empty id returns 400, distinct from pydantic's 422 for
    malformed JSON / field errors.
    """
    source_id: str | None = None
    source_label: str | None = None
    points: list[UsagePoint] = Field(default_factory=list)


class UsageDayModel(BaseModel):
    label: str
    raw_ids: list[str]
    input_tokens: int
    output_tokens: int
    total: int


class UsageDay(BaseModel):
    date: str
    models: list[UsageDayModel]


class UsageModelSummary(BaseModel):
    label: str
    color: str
    grand_total: int
    share_pct: float


class UsageTimeseriesResponse(BaseModel):
    range_days: int
    generated_at: str
    days: list[UsageDay]
    models: list[UsageModelSummary]
