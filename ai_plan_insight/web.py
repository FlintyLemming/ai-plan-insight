import asyncio
import logging
import secrets
import sqlite3
from contextlib import asynccontextmanager, closing
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse

INDEX_HTML = (Path(__file__).parent / "index.html").read_text(encoding="utf-8")

from .config import ProviderConfig
from .config_loader import load_config, DEFAULT_CONFIG_PATH
from . import usage_store
from .api_schemas import (
    UsageReportRequest,
    UsageTimeseriesResponse,
)
from .providers.kimi import KimiProvider
from .providers.bigmodel import BigModelProvider
from .providers.bigmodel_international import BigModelInternationalProvider
from .providers.aiping import AipingProvider
from .providers.huawei_cloud import HuaweiCloudBssProvider
from .providers.zenmux import ZenMuxProvider
from .providers.codex import CodexProvider, CodexSecurityProvider
from .providers.volcengine_ark import VolcEngineArkProvider
from .providers.antigravity import AntigravityProvider
from .api_schemas import (
    UsageResponse,
    LimitResponse,
    UsageDetailResponse,
    TokenUsageResponse,
    HistoryModelUsageResponse,
    HistoryUsagePeriodResponse,
    ModelStatResponse,
    AntigravityPushRequest,
    CursorPushRequest,
    MimoPushRequest,
    ClaudePushRequest,
)

logger = logging.getLogger(__name__)

REFRESH_INTERVAL = 30
PUSH_TTL_SECONDS = 30 * 60  # 推送数据若 30 分钟内未继续推送则视为过期，整块不显示

# Fixed palette for the usage chart, assigned in order of model rank (grand
# total desc). Models beyond the palette limit are rolled into "其他" (Other).
USAGE_PALETTE = ["#38bdf8", "#f59e0b", "#34d399", "#f472b6", "#a78bfa", "#fbbf24", "#22d3ee", "#fb7185"]
USAGE_OTHER_COLOR = "#64748b"
USAGE_OTHER_LABEL = "其他"
USAGE_PALETTE_LIMIT = 8

_cached_results: list[UsageResponse] = []
_pushed_results: dict[str, UsageResponse] = {}
_pushed_at: dict[str, datetime] = {}  # provider key -> 最近一次推送时间，用于 TTL 过滤
_last_updated: str | None = None
_consecutive_failures: dict[str, int] = {}  # provider name -> consecutive fail count
_prev_results: dict[str, UsageResponse] = {}  # last successful result per provider
_config_path: str | None = None  # set by main() when --config is provided
_usage_db_path: Path | None = None  # set by main() (--usage-db) or resolved at startup


def resolve_usage_db_path() -> Path:
    """Resolve the usage DB path per spec §4 precedence:

    1. explicit `--usage-db` flag (already set on `_usage_db_path`), else
    2. same directory as `--config`, else
    3. repo-root `/data/usage.db`.
    """
    global _usage_db_path
    if _usage_db_path is not None:
        return _usage_db_path
    if _config_path:
        _usage_db_path = Path(_config_path).resolve().parent / "usage.db"
    else:
        _usage_db_path = Path(__file__).resolve().parent.parent / "data" / "usage.db"
    _usage_db_path.parent.mkdir(parents=True, exist_ok=True)
    return _usage_db_path


def _usage_conn() -> sqlite3.Connection:
    if _usage_db_path is None:
        raise RuntimeError("usage DB path not initialized")
    return sqlite3.connect(_usage_db_path)


def _persist_usage_snapshot(usage: UsageResponse, source_kind: str) -> None:
    """Best-effort snapshot persistence; never raises."""
    try:
        with closing(_usage_conn()) as conn:
            usage_store.record_snapshot(conn, usage, source_kind=source_kind)  # type: ignore[arg-type]
            conn.commit()
    except Exception as e:
        logger.warning("failed to persist usage snapshot: %s", e)


def _verify_push_auth(request: Request, source_id: str) -> tuple[bool, str | None]:
    """Check the Bearer token against the configured global secret.

    Returns (is_valid, reason). `reason` is one of "missing", "malformed",
    or "invalid" when `is_valid` is False, and None when it is True.
    """
    config = load_config(_config_path)
    secret = config.push_auth_secret

    auth_header = request.headers.get("Authorization")
    if not auth_header:
        return False, "missing"

    parts = auth_header.strip().split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return False, "malformed"

    token = parts[1].strip()
    if not secret:
        return False, "invalid"

    if secrets.compare_digest(token, secret):
        return True, None
    return False, "invalid"


def _record_push_auth(conn: sqlite3.Connection, source_id: str, is_valid: bool) -> None:
    """Persist the auth outcome for a source; caller must commit."""
    now = datetime.now().astimezone().isoformat()
    usage_store.update_source_auth(conn, source_id, is_valid, now)


def _ensure_source_row(conn: sqlite3.Connection, source_id: str) -> None:
    """Create an empty source row if it does not already exist."""
    conn.execute(
        "INSERT INTO source (source_id) VALUES (?) "
        "ON CONFLICT(source_id) DO NOTHING",
        (source_id,),
    )


def _handle_push_auth(request: Request, source_id: str) -> None:
    """Verify auth and persist the outcome; raise 401 in hard mode on failure.

    In soft mode, auth failure is logged but the request continues.
    """
    config = load_config(_config_path)
    is_valid, reason = _verify_push_auth(request, source_id)
    if not is_valid:
        logger.warning("push auth failed for %s: %s", source_id, reason)
    if config.enforce_push_auth and not is_valid:
        raise HTTPException(status_code=401, detail="Unauthorized")
    # Persist the outcome even when the row may not exist yet (some push-only
    # sources may not have reported through /api/usage/report). Insert a stub
    # row so the status is visible in /api/admin/sources.
    with closing(_usage_conn()) as conn:
        _ensure_source_row(conn, source_id)
        _record_push_auth(conn, source_id, is_valid)
        conn.commit()
# Providers that have no fetch implementation and are fed exclusively via the
# /api/push/* endpoints. They are allowed as keys in config.json (to carry an
# `order` value) but must be skipped by the fetch loop.
_PUSH_ONLY_PROVIDERS = {"cursor", "claude", "mimo_token_plan"}

# provider config key -> display name. Built from the same source of truth as
# `_build_provider` so the two never drift apart. Push-only entries use the
# display name the push endpoint stamps on the UsageResponse (the mimo default
# mirrors MimoPushRequest.provider).
_PROVIDER_DISPLAY_NAMES = {
    "kimi": "Kimi Coding Plan",
    "bigmodel": "GLM Coding Plan",
    "bigmodel_international": "白嫖 GLM Coding Plan 国际版",
    "aiping": "AIPing",
    "huawei_cloud": "华为云余额",
    "zenmux": "ZenMux",
    "codex": "自购 Codex 中转站",
    "codex_security": "白嫖 Codex Security 中转",
    "antigravity": "Antigravity",
    "volcengine_ark": "火山方舟 Coding Plan",
    "cursor": "Cursor",
    "claude": "Claude 订阅",
    "mimo_token_plan": "小米 MiMo Token Plan",
}


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
        case "zenmux":
            return ZenMuxProvider(config)
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

    # Fetch history usage if supported. History failures must not break current usage cards.
    if hasattr(provider, "fetch_history_usage"):
        try:
            parsed.history_usage = await provider.fetch_history_usage()
        except Exception as e:
            logger.warning("Failed to fetch history usage for %s: %s", name, e)

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

    history_usage = None
    if parsed.history_usage:
        history_usage = HistoryUsagePeriodResponse(
            period=parsed.history_usage.period,
            granularity=parsed.history_usage.granularity,
            x_time=parsed.history_usage.x_time,
            tokens_usage=parsed.history_usage.tokens_usage,
            model_call_count=parsed.history_usage.model_call_count,
            total_tokens=parsed.history_usage.total_tokens,
            total_calls=parsed.history_usage.total_calls,
            models=[
                HistoryModelUsageResponse(
                    model_name=m.model_name,
                    total_tokens=m.total_tokens,
                    total_calls=m.total_calls,
                    tokens_usage=m.tokens_usage,
                )
                for m in parsed.history_usage.models
            ],
        )

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
        history_usage=history_usage,
        model_stats=model_stats,
    )


async def _fetch_all_usage() -> list[UsageResponse]:
    global _consecutive_failures, _prev_results
    config = load_config(_config_path)
    names = [n for n in config.providers.keys() if n not in _PUSH_ONLY_PROVIDERS]
    tasks = [_fetch_one(name, config.providers[name]) for name in names]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    valid: list[UsageResponse] = []
    for name, r in zip(names, results):
        if isinstance(r, UsageResponse):
            _consecutive_failures[name] = 0
            _prev_results[name] = r
            _persist_usage_snapshot(r, "fetch")
            valid.append(r)
        else:
            _consecutive_failures[name] = _consecutive_failures.get(name, 0) + 1
            display_name = _PROVIDER_DISPLAY_NAMES.get(name, name)
            if _consecutive_failures[name] < 3 and name in _prev_results:
                valid.append(_prev_results[name])
            else:
                valid.append(UsageResponse(provider=display_name, error=str(r)))
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
    try:
        usage_store.init_db(resolve_usage_db_path())
    except Exception as e:
        logger.error("usage DB init failed: %s", e)
    task_refresh = asyncio.create_task(_background_refresh())
    yield
    task_refresh.cancel()


app = FastAPI(title="AI Plan Insight", lifespan=lifespan)


def _build_display_order_map() -> dict[str, int]:
    """Map provider display name -> config `order` value, from the live config.

    Used to sort cards in `/api/usage`. Display names that are not present in
    config (e.g. a push for a provider with no config entry, or a mimo push
    that overrides `provider`) fall back to 999 so they sort last.
    """
    config = load_config(_config_path)
    order_map: dict[str, int] = {}
    for key, prov_cfg in config.providers.items():
        display_name = _PROVIDER_DISPLAY_NAMES.get(key, key)
        order_map[display_name] = prov_cfg.order
    return order_map


@app.get("/api/usage", response_model=list[UsageResponse])
async def get_usage():
    now = datetime.now().astimezone()
    cutoff = now - timedelta(seconds=PUSH_TTL_SECONDS)
    valid_pushed = [
        r for key, r in _pushed_results.items()
        if _pushed_at.get(key) and _pushed_at[key] >= cutoff
    ]
    combined = _cached_results + valid_pushed
    order_map = _build_display_order_map()
    # Sort by (order, display name): order governs position, the display name
    # is a stable tiebreaker so multiple providers sharing the default 999
    # (or the same explicit order) keep a deterministic order between refreshes.
    combined.sort(key=lambda r: (order_map.get(r.provider, 999), r.provider))
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
async def push_antigravity(req: AntigravityPushRequest, request: Request):
    _handle_push_auth(request, "antigravity")
    global _last_updated, _pushed_results, _pushed_at
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
    _persist_usage_snapshot(_pushed_results["antigravity"], "push")
    _last_updated = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    _pushed_at["antigravity"] = datetime.now().astimezone()
    return {"status": "ok"}


@app.post("/api/push/cursor")
async def push_cursor(req: CursorPushRequest, request: Request):
    _handle_push_auth(request, "cursor")
    global _last_updated, _pushed_results, _pushed_at
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
    _persist_usage_snapshot(_pushed_results["cursor"], "push")
    _last_updated = dt.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    _pushed_at["cursor"] = dt.now().astimezone()
    return {"status": "ok"}


@app.post("/api/push/mimo")
async def push_mimo(req: MimoPushRequest, request: Request):
    _handle_push_auth(request, "mimo_token_plan")
    global _last_updated, _pushed_results, _pushed_at
    _pushed_results["mimo_token_plan"] = UsageResponse(
        provider=req.provider,
        user_id=req.user_id,
        membership_level=req.membership_level,
        limits=req.limits,
        balances=req.balances,
    )
    _persist_usage_snapshot(_pushed_results["mimo_token_plan"], "push")
    _last_updated = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    _pushed_at["mimo_token_plan"] = datetime.now().astimezone()
    return {"status": "ok"}


@app.post("/api/push/claude")
async def push_claude(req: ClaudePushRequest, request: Request):
    _handle_push_auth(request, "claude")
    global _last_updated, _pushed_results, _pushed_at
    _pushed_results["claude"] = UsageResponse(
        provider="Claude 订阅",
        limits=[
            LimitResponse(
                duration=5,
                time_unit="小时",
                limit="100",
                used=str(int(req.five_hour.utilization)),
                remaining=str(int(100 - req.five_hour.utilization)),
                reset_time=req.five_hour.resets_at,
            ),
            LimitResponse(
                duration=7,
                time_unit="天",
                limit="100",
                used=str(int(req.seven_day.utilization)),
                remaining=str(int(100 - req.seven_day.utilization)),
                reset_time=req.seven_day.resets_at,
            ),
        ],
    )
    _persist_usage_snapshot(_pushed_results["claude"], "push")
    _last_updated = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    _pushed_at["claude"] = datetime.now().astimezone()
    return {"status": "ok"}


@app.post("/api/usage/report")
async def report_usage(req: UsageReportRequest, request: Request):
    if not req.source_id:
        raise HTTPException(status_code=400, detail="source_id is required")

    config = load_config(_config_path)
    is_valid, reason = _verify_push_auth(request, req.source_id)
    if not is_valid:
        logger.warning("push auth failed for %s: %s", req.source_id, reason)
    if config.enforce_push_auth and not is_valid:
        raise HTTPException(status_code=401, detail="Unauthorized")

    try:
        with closing(_usage_conn()) as conn:
            written, dropped = usage_store.upsert_points(
                conn,
                req.source_id,
                req.source_label,
                [p.model_dump() for p in req.points],
                reported_at=req.reported_at,
            )
            usage_store.update_source_auth(
                conn, req.source_id, is_valid, datetime.now().astimezone().isoformat()
            )
            conn.commit()
    except Exception as e:
        logger.error("usage report failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))
    if dropped:
        logger.info(
            "usage report from %s: dropped %d point(s) for frozen days", req.source_id, dropped
        )
    return {"ok": True, "upserted": written, "dropped": dropped}


@app.get("/api/admin/sources")
async def get_admin_sources():
    """Return the authentication status of every source that has reported."""
    try:
        with closing(_usage_conn()) as conn:
            return usage_store.get_source_auth_status(conn)
    except Exception as e:
        logger.error("admin sources failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


def build_timeseries_response(rows: list, range_days: int) -> UsageTimeseriesResponse:
    """Turn alias-resolved store rows into the chart payload.

    `rows` are `usage_store.TimeseriesRow` (already aliased + cross-source
    summed). This layer assigns fixed colors by rank, rolls 9th+ models into
    "其他", and computes per-model grand totals + share %.
    """
    grand: dict[str, int] = {}
    grand_in: dict[str, int] = {}
    grand_out: dict[str, int] = {}
    grand_cr: dict[str, int] = {}
    grand_cw: dict[str, int] = {}
    grand_rs: dict[str, int] = {}
    for r in rows:
        grand[r.label] = grand.get(r.label, 0) + r.total
        grand_in[r.label] = grand_in.get(r.label, 0) + r.input_tokens
        grand_out[r.label] = grand_out.get(r.label, 0) + r.output_tokens
        grand_cr[r.label] = grand_cr.get(r.label, 0) + r.cache_read_tokens
        grand_cw[r.label] = grand_cw.get(r.label, 0) + r.cache_write_tokens
        grand_rs[r.label] = grand_rs.get(r.label, 0) + r.reasoning_tokens

    ranked = sorted(grand.items(), key=lambda kv: kv[1], reverse=True)
    top_labels = [label for label, _ in ranked[:USAGE_PALETTE_LIMIT]]
    color_of = {label: USAGE_PALETTE[i] for i, label in enumerate(top_labels)}

    def resolve(label: str) -> tuple[str, str]:
        if label in color_of:
            return label, color_of[label]
        return USAGE_OTHER_LABEL, USAGE_OTHER_COLOR

    # days[] — group by date, collapse top/其他 within each day
    by_date: dict[str, list] = {}
    for r in rows:
        by_date.setdefault(r.date, []).append(r)

    days_out: list[dict] = []
    for d in sorted(by_date.keys()):
        agg: dict[str, dict] = {}
        for r in by_date[d]:
            resolved_label, _ = resolve(r.label)
            slot = agg.setdefault(resolved_label, {
                "input_tokens": 0, "output_tokens": 0, "total": 0, "raw_ids": set(),
                "cache_read_tokens": 0, "cache_write_tokens": 0, "reasoning_tokens": 0,
            })
            slot["input_tokens"] += r.input_tokens
            slot["output_tokens"] += r.output_tokens
            slot["cache_read_tokens"] += r.cache_read_tokens
            slot["cache_write_tokens"] += r.cache_write_tokens
            slot["reasoning_tokens"] += r.reasoning_tokens
            slot["total"] += r.total
            slot["raw_ids"].update(r.raw_ids)
        days_out.append({
            "date": d,
            "models": [
                {
                    "label": rl,
                    "raw_ids": sorted(s["raw_ids"]),
                    "input_tokens": s["input_tokens"],
                    "output_tokens": s["output_tokens"],
                    "cache_read_tokens": s["cache_read_tokens"],
                    "cache_write_tokens": s["cache_write_tokens"],
                    "reasoning_tokens": s["reasoning_tokens"],
                    "total": s["total"],
                }
                for rl, s in agg.items()
            ],
        })

    # models[] — grand totals + share %, merged for 其他
    grand_total_all = sum(grand.values()) or 1
    summary: dict[str, dict] = {}
    for label, total in ranked:
        resolved_label, color = resolve(label)
        slot = summary.setdefault(resolved_label, {
            "grand_total": 0, "color": color,
            "input_tokens": 0, "output_tokens": 0,
            "cache_read_tokens": 0, "cache_write_tokens": 0, "reasoning_tokens": 0,
        })
        slot["grand_total"] += total
        slot["input_tokens"] += grand_in.get(label, 0)
        slot["output_tokens"] += grand_out.get(label, 0)
        slot["cache_read_tokens"] += grand_cr.get(label, 0)
        slot["cache_write_tokens"] += grand_cw.get(label, 0)
        slot["reasoning_tokens"] += grand_rs.get(label, 0)
    models_out = [
        {
            "label": rl,
            "color": s["color"],
            "input_tokens": s["input_tokens"],
            "output_tokens": s["output_tokens"],
            "cache_read_tokens": s["cache_read_tokens"],
            "cache_write_tokens": s["cache_write_tokens"],
            "reasoning_tokens": s["reasoning_tokens"],
            "grand_total": s["grand_total"],
            "share_pct": round(s["grand_total"] * 100 / grand_total_all, 1),
        }
        for rl, s in summary.items()
    ]
    models_out.sort(key=lambda m: m["grand_total"], reverse=True)

    # all_models[] — every model un-merged (no top-8 + 其他 rollup),
    # so the per-model table can show the full list with pagination.
    all_models_out = [
        {
            "label": label,
            "color": color_of.get(label, USAGE_OTHER_COLOR),
            "input_tokens": grand_in.get(label, 0),
            "output_tokens": grand_out.get(label, 0),
            "cache_read_tokens": grand_cr.get(label, 0),
            "cache_write_tokens": grand_cw.get(label, 0),
            "reasoning_tokens": grand_rs.get(label, 0),
            "grand_total": total,
            "share_pct": round(total * 100 / grand_total_all, 1),
        }
        for label, total in ranked
    ]

    return UsageTimeseriesResponse(
        range_days=range_days,
        generated_at=datetime.now().astimezone().isoformat(),
        days=days_out,
        models=models_out,
        all_models=all_models_out,
    )


@app.get("/api/usage/timeseries", response_model=UsageTimeseriesResponse)
async def usage_timeseries(days: int = 90):
    days = days if days in (7, 30, 90) else 90
    config = load_config(_config_path)
    try:
        with closing(_usage_conn()) as conn:
            rows = usage_store.query_timeseries(conn, days, config.alias_lookup)
    except Exception as e:
        logger.error("usage timeseries failed: %s", e)
        return UsageTimeseriesResponse(
            range_days=days,
            generated_at=datetime.now().astimezone().isoformat(),
            days=[],
            models=[],
            all_models=[],
        )
    return build_timeseries_response(rows, days)
