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
from .api_schemas import UsageResponse, LimitResponse, UsageDetailResponse

logger = logging.getLogger(__name__)


def _build_provider(name: str, config: ProviderConfig):
    match name:
        case "kimi":
            return KimiProvider(config)
        case "bigmodel":
            return BigModelProvider(config)
        case "aiping":
            return AipingProvider(config)
        case _:
            raise ValueError(f"Unknown provider: {name}")


async def _fetch_one(name: str, config: ProviderConfig) -> UsageResponse:
    provider = _build_provider(name, config)
    provider.authenticate()
    raw = await provider.fetch_usage()
    parsed = provider.parse_usage(raw)

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

    return UsageResponse(
        provider=parsed.provider,
        user_id=parsed.user_id,
        membership_level=parsed.membership_level,
        limits=limits,
        balances=parsed.balances,
    )


async def fetch_all_usage() -> list[UsageResponse]:
    config = load_config()
    tasks = []
    for name, pcfg in config.providers.items():
        tasks.append(_fetch_one(name, pcfg))
    results = await asyncio.gather(*tasks, return_exceptions=True)
    return [r for r in results if isinstance(r, UsageResponse)]


_last_updated: str | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _last_updated
    _last_updated = None
    yield


app = FastAPI(title="AI Plan Insight", lifespan=lifespan)


@app.get("/api/usage", response_model=list[UsageResponse])
async def get_usage():
    global _last_updated
    results = await fetch_all_usage()
    _last_updated = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    return results


@app.get("/api/status")
async def get_status():
    return {"last_updated": _last_updated}


@app.get("/", response_class=HTMLResponse)
async def index():
    return HTMLResponse(
        content=INDEX_HTML,
        status_code=200,
    )
