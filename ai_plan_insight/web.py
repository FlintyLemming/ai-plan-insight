import logging
import secrets
import sqlite3
from contextlib import asynccontextmanager, closing
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse

INDEX_HTML = (Path(__file__).parent / "index.html").read_text(encoding="utf-8")

from . import pricing, usage_store
from .api_schemas import UsageReportRequest, UsageTimeseriesResponse
from .config_service import ConfigService
from .instance_config import DEFAULT_CONFIG_PATH

logger = logging.getLogger(__name__)

# Fixed palette for the usage chart, assigned in order of model rank (grand
# total desc). Models beyond the palette limit are rolled into "其他" (Other).
USAGE_PALETTE = ["#38bdf8", "#f59e0b", "#34d399", "#f472b6", "#a78bfa", "#fbbf24", "#22d3ee", "#fb7185"]
USAGE_OTHER_COLOR = "#64748b"
USAGE_OTHER_LABEL = "其他"
USAGE_PALETTE_LIMIT = 8

_config_path: str | None = None  # set by main() when --config is provided
_usage_db_path: Path | None = None  # set by main() (--usage-db) or resolved at startup

# v2 state
_config_service: ConfigService | None = None
_v2_manager = None  # type: V2RuntimeManager | None

# USD pricing (LiteLLM → OpenRouter → models.dev fallback chain); None until
# lifespan initializes it, and tests may monkeypatch it with a stub.
_pricing_service: pricing.PricingService | None = None


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


def _verify_push_auth(request: Request, source_id: str, cfg) -> tuple[bool, str | None]:
    """Check the Bearer token against the configured global secret.

    Returns (is_valid, reason). `reason` is one of "missing", "malformed",
    or "invalid" when `is_valid` is False, and None when it is True.
    """
    secret = cfg.push_auth_secret

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


def _init_v2_manager() -> None:
    """Create the ConfigService and the v2 runtime manager from it."""
    global _v2_manager, _config_service
    from .provider_instances import V2RuntimeManager

    config_path = Path(_config_path) if _config_path else DEFAULT_CONFIG_PATH
    _config_service = ConfigService(config_path)
    result = _config_service.get()

    if result.config_error is not None:
        logger.error("config unusable: %s", result.config_error)
        _v2_manager = V2RuntimeManager.disabled(
            result.config_error, resolve_usage_db_path()
        )
        _config_service.set_manager(_v2_manager)
        return

    _v2_manager = V2RuntimeManager(
        result.config, resolve_usage_db_path(),
        instance_errors=result.instance_errors,
    )
    _config_service.set_manager(_v2_manager)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _pricing_service
    try:
        usage_store.init_db(resolve_usage_db_path())
    except Exception as e:
        logger.error("usage DB init failed: %s", e)
    _init_v2_manager()
    if _v2_manager and _v2_manager.enabled:
        _v2_manager.start()
    try:
        _pricing_service = pricing.get_pricing_service(resolve_usage_db_path())
        _pricing_service.start_background()  # startup prefetch + hourly refresh
    except Exception as e:
        logger.error("pricing service init failed: %s", e)
        _pricing_service = None
    yield
    if _v2_manager:
        _v2_manager.stop()
    if _pricing_service is not None:
        _pricing_service.stop_background()


app = FastAPI(title="AI Plan Insight", lifespan=lifespan)


@app.get("/", response_class=HTMLResponse)
async def index():
    return HTMLResponse(
        content=INDEX_HTML,
        status_code=200,
    )


@app.post("/api/usage/report")
async def report_usage(req: UsageReportRequest, request: Request):
    if not req.source_id:
        raise HTTPException(status_code=400, detail="source_id is required")

    cfg = _config_service.get().config if _config_service is not None else None
    if cfg is None:
        raise HTTPException(status_code=503, detail="config not available")
    is_valid, reason = _verify_push_auth(request, req.source_id, cfg)
    if not is_valid:
        logger.warning("push auth failed for %s: %s", req.source_id, reason)
    if cfg.enforce_push_auth and not is_valid:
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


@app.get("/api/sources/stale")
async def get_stale_sources():
    """List reporting devices silent for over 24h (agent liveness alert)."""
    try:
        with closing(_usage_conn()) as conn:
            return usage_store.get_stale_sources(conn)
    except Exception as e:
        # The alert is best-effort; never let it break the usage page.
        logger.error("stale sources query failed: %s", e)
        return []


@app.post("/api/sources/{source_id}/dismiss-stale")
async def dismiss_stale_source(source_id: str):
    """Silence the stale alert for one device until it reports again."""
    try:
        with closing(_usage_conn()) as conn:
            found = usage_store.dismiss_stale_source(
                conn, source_id, datetime.now().astimezone().isoformat()
            )
            conn.commit()
    except Exception as e:
        logger.error("dismiss stale source failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))
    if not found:
        raise HTTPException(status_code=404, detail="unknown source")
    return {"ok": True}


@app.get("/api/admin/sources")
async def get_admin_sources():
    """Return the authentication status of every source that has reported."""
    try:
        with closing(_usage_conn()) as conn:
            return usage_store.get_source_auth_status(conn)
    except Exception as e:
        logger.error("admin sources failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


def build_timeseries_response(rows: list, range_days: int, price_of=None) -> UsageTimeseriesResponse:
    """Turn alias-resolved store rows into the chart payload.

    `rows` are `usage_store.TimeseriesRow` (already aliased + cross-source
    summed). This layer assigns fixed colors by rank, rolls 9th+ models into
    "其他", and computes per-model grand totals + share %.

    `price_of`, when given, maps a raw model id to a `pricing.ModelPrice`;
    each raw id's OWN tokens are priced at its own rate and summed per
    canonical label (the alias display name itself has no price).
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

    # USD cost per canonical label, priced per raw model id then summed.
    label_cost: dict[str, float] = {}
    label_priced: dict[str, bool] = {}
    label_partial: dict[str, bool] = {}
    if price_of is not None:
        for r in rows:
            for raw_id, (ti, to, tcr, tcw, trs) in r.raw_breakdown.items():
                price = price_of(raw_id)
                if price is None:
                    if ti or to or tcr or tcw or trs:
                        label_partial[r.label] = True
                    continue
                label_cost[r.label] = label_cost.get(r.label, 0.0) + price.cost(
                    ti, to, tcr, tcw, trs
                )
                label_priced[r.label] = True

    def cost_fields(label: str) -> dict:
        return {
            "cost_usd": label_cost[label] if label_priced.get(label) else None,
            "cost_partial": bool(label_partial.get(label)),
        }

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
            "cost_usd": None, "cost_partial": False,
        })
        slot["grand_total"] += total
        slot["input_tokens"] += grand_in.get(label, 0)
        slot["output_tokens"] += grand_out.get(label, 0)
        slot["cache_read_tokens"] += grand_cr.get(label, 0)
        slot["cache_write_tokens"] += grand_cw.get(label, 0)
        slot["reasoning_tokens"] += grand_rs.get(label, 0)
        cf = cost_fields(label)
        if cf["cost_usd"] is not None:
            slot["cost_usd"] = (slot["cost_usd"] or 0.0) + cf["cost_usd"]
        slot["cost_partial"] = slot["cost_partial"] or cf["cost_partial"]
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
            "cost_usd": s["cost_usd"],
            "cost_partial": s["cost_partial"],
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
            **cost_fields(label),
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
    cfg = _config_service.get().config if _config_service is not None else None
    alias_lookup = cfg.alias_lookup if cfg is not None else {}
    try:
        with closing(_usage_conn()) as conn:
            rows = usage_store.query_timeseries(conn, days, alias_lookup)
    except Exception as e:
        logger.error("usage timeseries failed: %s", e)
        return UsageTimeseriesResponse(
            range_days=days,
            generated_at=datetime.now().astimezone().isoformat(),
            days=[],
            models=[],
            all_models=[],
        )
    price_of = None
    if _pricing_service is not None:
        await _pricing_service.ensure_ready()  # bounded wait; never raises
        price_of = _pricing_service.price_for
    return build_timeseries_response(rows, days, price_of)


# ── v2 endpoints ──────────────────────────────────────────────

@app.get("/api/usage/v2")
async def get_usage_v2():
    if _config_service is not None:
        _config_service.get()  # mtime poll; may trigger manager.reload
    if _v2_manager is None or not _v2_manager.enabled:
        return []
    return _v2_manager.get_usage_v2()


@app.get("/api/status/v2")
async def get_status_v2():
    if _v2_manager is None:
        return {
            "enabled": False,
            "last_updated": None,
            "config_error": None,
            "instance_errors": {},
        }
    return _v2_manager.get_status()


@app.post("/api/push/v2/{instance_id}")
async def push_v2(instance_id: str, request: Request):
    if _config_service is None or _v2_manager is None or not _v2_manager.enabled:
        raise HTTPException(status_code=503, detail="v2 not available")

    cfg = _config_service.get().config  # hot reload poll
    if instance_id not in cfg.providers:
        raise HTTPException(status_code=404, detail=f"instance {instance_id!r} not registered")

    inst = cfg.providers[instance_id]
    if inst.mode != "push":
        raise HTTPException(
            status_code=422,
            detail=f"instance {instance_id!r} is a fetch instance",
        )

    # Auth check
    is_valid, reason = _verify_push_auth_v2(request, cfg)
    if not is_valid:
        logger.warning("v2 push auth failed for %s: %s", instance_id, reason)
    if cfg.enforce_push_auth and not is_valid:
        raise HTTPException(status_code=401, detail="Unauthorized")

    # Persist the auth outcome so push sources show up in /api/admin/sources
    # (mirrors the old v1 _handle_push_auth behavior).
    with closing(_usage_conn()) as conn:
        _ensure_source_row(conn, instance_id)
        _record_push_auth(conn, instance_id, is_valid)
        conn.commit()

    # Get the request model for this type and parse the body
    from .provider_registry import get_push_request_model
    req_model = get_push_request_model(inst.type)
    if req_model is None:
        raise HTTPException(status_code=422, detail=f"no push schema for type {inst.type!r}")

    try:
        body = await request.json()
        payload = req_model.model_validate(body)
    except Exception as e:
        raise HTTPException(status_code=422, detail=str(e))

    try:
        result = await _v2_manager.handle_push(instance_id, payload)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    return result


def _verify_push_auth_v2(request: Request, v2_config) -> tuple[bool, str | None]:
    """Check Bearer token against v2 config's push_auth_secret."""
    secret = v2_config.push_auth_secret
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
