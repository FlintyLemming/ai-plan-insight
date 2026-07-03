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
    """One (date, model) token row submitted by an agent client.

    The five token categories are mutually exclusive and additive, matching
    tokscale's breakdown: total = input + output + cache_read + cache_write
    + reasoning. The cache/reasoning fields default to 0 so older agents
    that only send input/output still validate.
    """
    date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    model_id: str
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    cache_read_tokens: int = Field(default=0, ge=0)
    cache_write_tokens: int = Field(default=0, ge=0)
    reasoning_tokens: int = Field(default=0, ge=0)


class UsageReportRequest(BaseModel):
    """POST /api/usage/report body.

    `source_id` is validated for presence (non-empty) in the route handler so
    a missing/empty id returns 400, distinct from pydantic's 422 for
    malformed JSON / field errors.

    `reported_at` is the agent's run date (UTC+8). Once the payload lands,
    all of this source's days before it are frozen against later writes; a
    payload without it (older agents) still respects existing frozen days
    but does not advance the freeze watermark.
    """
    source_id: str | None = None
    source_label: str | None = None
    reported_at: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    points: list[UsagePoint] = Field(default_factory=list)


class UsageDayModel(BaseModel):
    label: str
    raw_ids: list[str]
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    reasoning_tokens: int = 0
    total: int


class UsageDay(BaseModel):
    date: str
    models: list[UsageDayModel]


class UsageModelSummary(BaseModel):
    label: str
    color: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    reasoning_tokens: int = 0
    grand_total: int
    share_pct: float


class UsageTimeseriesResponse(BaseModel):
    range_days: int
    generated_at: str
    days: list[UsageDay]
    models: list[UsageModelSummary]
    all_models: list[UsageModelSummary] = []
