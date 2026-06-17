import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

INDEX_HTML = (Path(__file__).parent / "index.html").read_text(encoding="utf-8")

from .config import ProviderConfig
from .config_loader import load_config, DEFAULT_CONFIG_PATH
from .providers.kimi import KimiProvider
from .providers.bigmodel import BigModelProvider
from .providers.bigmodel_international import BigModelInternationalProvider
from .providers.aiping import AipingProvider
from .providers.huawei_cloud import HuaweiCloudBssProvider
from .providers.codex import CodexProvider, CodexSecurityProvider
from .providers.volcengine_ark import VolcEngineArkProvider
from .providers.antigravity import AntigravityProvider
from .api_schemas import UsageResponse, LimitResponse, UsageDetailResponse, TokenUsageResponse, ModelStatResponse, AntigravityPushRequest, CursorPushRequest
from .pocketbase_store import background_store_glm

logger = logging.getLogger(__name__)

REFRESH_INTERVAL = 30

_cached_results: list[UsageResponse] = []
_pushed_results: dict[str, UsageResponse] = {}
_last_updated: str | None = None
_consecutive_failures: dict[str, int] = {}  # provider name -> consecutive fail count
_prev_results: dict[str, UsageResponse] = {}  # last successful result per provider
_config_path: str | None = None  # set by main() when --config is provided


def _build_provider(name: str, config: ProviderConfig):
    match name:
        case "kimi":
            return KimiProvider(config)
        case "bigmodel":
            return BigModelProvider(config)
        case "bigmodel_international":
            return BigModelInternationalProvider(config)
        case "aiping":
            return AipingProvider(config)
        case "huawei_cloud":
            return HuaweiCloudBssProvider(config)
        case "codex":
            return CodexProvider(config)
        case "codex_security":
            return CodexSecurityProvider(config)
        case "antigravity":
            return AntigravityProvider(config)
        case "volcengine_ark":
            return VolcEngineArkProvider(config)
        case _:
            raise ValueError(f"Unknown provider: {name}")


async def _fetch_one(name: str, config: ProviderConfig) -> UsageResponse:
    provider = _build_provider(name, config)
    provider.authenticate()
    raw = await provider.fetch_usage()
    parsed = provider.parse_usage(raw)

    # Fetch token usage if supported
    if hasattr(provider, "fetch_token_usage"):
        parsed.token_usage = await provider.fetch_token_usage()

    limits = []
    for lim in parsed.limits:
        details = [
            UsageDetailResponse(model_code=d.model_code, usage=d.usage)
            for d in lim.usage_details
        ]
        limits.append(
            LimitResponse(
                duration=lim.duration,
                time_unit=lim.time_unit,
                limit=lim.limit,
                used=lim.used,
                remaining=lim.remaining,
                reset_time=lim.reset_time.isoformat() if lim.reset_time else None,
                usage_details=details,
                limit_type=lim.limit_type,
            )
        )

    token_usage = [
        TokenUsageResponse(period=t.period, total_tokens=t.total_tokens, total_calls=t.total_calls)
        for t in parsed.token_usage
    ]

    model_stats = [
        ModelStatResponse(model=m.model, total_tokens=m.total_tokens, requests=m.requests)
        for m in parsed.model_stats
    ]

    return UsageResponse(
        provider=parsed.provider,
        user_id=parsed.user_id,
        membership_level=parsed.membership_level,
        limits=limits,
        balances=parsed.balances,
        token_usage=token_usage,
        model_stats=model_stats,
    )


async def _fetch_all_usage() -> list[UsageResponse]:
    global _consecutive_failures, _prev_results
    config = load_config(_config_path)
    names = list(config.providers.keys())
    tasks = [_fetch_one(name, config.providers[name]) for name in names]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    valid: list[UsageResponse] = []
    for name, r in zip(names, results):
        if isinstance(r, UsageResponse):
            _consecutive_failures[name] = 0
            _prev_results[name] = r
            valid.append(r)
        else:
            _consecutive_failures[name] = _consecutive_failures.get(name, 0) + 1
            if _consecutive_failures[name] < 3 and name in _prev_results:
                valid.append(_prev_results[name])
    return valid


async def _background_refresh():
    global _cached_results, _last_updated
    while True:
        try:
            results = await _fetch_all_usage()
            _cached_results = results
            _last_updated = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
            logger.info("Background refresh done, %d providers fetched", len(results))
        except Exception as e:
            logger.error("Background refresh failed: %s", e)
        await asyncio.sleep(REFRESH_INTERVAL)


@asynccontextmanager
async def lifespan(app: FastAPI):
    task_refresh = asyncio.create_task(_background_refresh())
    task_pb = asyncio.create_task(background_store_glm())
    yield
    task_refresh.cancel()
    task_pb.cancel()


app = FastAPI(title="AI Plan Insight", lifespan=lifespan)


def _provider_sort_key(resp: UsageResponse) -> int:
    order = {
        "自购 Codex 中转站": 10,
        "白嫖 Codex Security 中转": 11,
        "火山方舟 Coding Plan": 12,
        "GLM Coding Plan": 20,
        "白嫖 GLM Coding Plan 国际版": 21,
        "Antigravity": 22,
        "Cursor": 25,
        "Kimi Coding Plan": 30,
        "华为云余额": 35,
        "AIPing": 50,
    }
    return order.get(resp.provider, 100)


@app.get("/api/usage", response_model=list[UsageResponse])
async def get_usage():
    combined = _cached_results + list(_pushed_results.values())
    combined.sort(key=_provider_sort_key)
    return combined


@app.get("/api/status")
async def get_status():
    return {"last_updated": _last_updated}


@app.get("/", response_class=HTMLResponse)
async def index():
    return HTMLResponse(
        content=INDEX_HTML,
        status_code=200,
    )


@app.post("/api/push/antigravity")
async def push_antigravity(req: AntigravityPushRequest):
    global _last_updated, _pushed_results
    _pushed_results["antigravity"] = UsageResponse(
        provider="Antigravity",
        membership_level="Gemini Ultra",
        limits=[
            LimitResponse(
                duration=5,
                time_unit="小时 (Gemini 3.1 Pro)",
                limit="100",
                used=str(int(req.gemini_3_1_pro_percentage)),
                remaining=str(int(100 - req.gemini_3_1_pro_percentage)),
                reset_time=req.gemini_3_1_pro_reset_time,
            ),
            LimitResponse(
                duration=5,
                time_unit="小时 (Gemini 3 Flash)",
                limit="100",
                used=str(int(req.gemini_3_flash_percentage)),
                remaining=str(int(100 - req.gemini_3_flash_percentage)),
                reset_time=req.gemini_3_flash_reset_time,
            ),
            LimitResponse(
                duration=5,
                time_unit="小时 (Claude 系列)",
                limit="100",
                used=str(int(req.claude_series_percentage)),
                remaining=str(int(100 - req.claude_series_percentage)),
                reset_time=req.claude_series_reset_time,
            ),
        ]
    )
    _last_updated = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    return {"status": "ok"}


@app.post("/api/push/cursor")
async def push_cursor(req: CursorPushRequest):
    global _last_updated, _pushed_results
    from datetime import datetime as dt

    end_dt = dt.fromisoformat(req.billing_end.replace("Z", "+00:00"))
    end_display = end_dt.strftime("%Y-%m-%d")

    def pct_to_limit(label: str, pct: float) -> LimitResponse:
        return LimitResponse(
            duration=1,
            time_unit=label,
            limit="100",
            used=f"{pct:.2f}",
            remaining=f"{100 - pct:.2f}",
            reset_time=req.billing_end,
            limit_type="PERCENT",
        )

    _pushed_results["cursor"] = UsageResponse(
        provider="Cursor",
        membership_level=req.membership,
        limits=[
            pct_to_limit("Auto + Composer 用量", req.autoPercentUsed or 0),
            pct_to_limit("API 用量", req.apiPercentUsed or 0),
        ],
        balances={"到期时间": end_display},
    )
    _last_updated = dt.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    return {"status": "ok"}
