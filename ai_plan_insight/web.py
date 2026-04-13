import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

INDEX_HTML = (Path(__file__).parent / "index.html").read_text(encoding="utf-8")

from .config import ProviderConfig
from .config_loader import load_config
from .providers.kimi import KimiProvider
from .providers.bigmodel import BigModelProvider
from .providers.aiping import AipingProvider
from .providers.alibaba_cloud import AlibabaCloudProvider
from .api_schemas import UsageResponse, LimitResponse, UsageDetailResponse, TokenUsageResponse

logger = logging.getLogger(__name__)

REFRESH_INTERVAL = 30

_cached_results: list[UsageResponse] = []
_last_updated: str | None = None


def _build_provider(name: str, config: ProviderConfig):
    match name:
        case "kimi":
            return KimiProvider(config)
        case "bigmodel":
            return BigModelProvider(config)
        case "aiping":
            return AipingProvider(config)
        case "alibaba_cloud":
            return AlibabaCloudProvider(config)
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

    return UsageResponse(
        provider=parsed.provider,
        user_id=parsed.user_id,
        membership_level=parsed.membership_level,
        limits=limits,
        balances=parsed.balances,
        token_usage=token_usage,
    )


async def _fetch_all_usage() -> list[UsageResponse]:
    config = load_config()
    tasks = []
    for name, pcfg in config.providers.items():
        tasks.append(_fetch_one(name, pcfg))
    results = await asyncio.gather(*tasks, return_exceptions=True)
    return [r for r in results if isinstance(r, UsageResponse)]


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
    task = asyncio.create_task(_background_refresh())
    yield
    task.cancel()


app = FastAPI(title="AI Plan Insight", lifespan=lifespan)


@app.get("/api/usage", response_model=list[UsageResponse])
async def get_usage():
    return _cached_results


@app.get("/api/status")
async def get_status():
    return {"last_updated": _last_updated}


@app.get("/", response_class=HTMLResponse)
async def index():
    return HTMLResponse(
        content=INDEX_HTML,
        status_code=200,
    )
