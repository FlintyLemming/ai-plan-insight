"""Per-model USD pricing with a per-model source fallback chain.

Sources tried in order, per raw model id (mirrors tokscale's design):

1. LiteLLM ``model_prices_and_context_window.json`` — flat JSON, USD per token,
   keys exist both bare ("gpt-4o") and provider-prefixed ("anthropic/claude-…").
2. OpenRouter ``/api/v1/models`` — ``pricing.prompt``/``pricing.completion`` are
   strings, USD per token. The list endpoint carries no cache pricing, so cache
   tokens contribute $0 when OpenRouter is the source of truth.
3. models.dev ``api.json`` — ``cost.*`` in USD per MILLION tokens (÷ 1e6),
   includes cache pricing. Blocks Python's default User-Agent, so every fetch
   sends a browser-like UA (harmless for the other two).

Reasoning tokens are billed at the output rate. Fetched datasets are cached to
``pricing-cache.json`` beside the usage DB (survives container rebuilds on the
``./data`` volume), refreshed at most once per 24h, and stale data is served
when a refresh fails.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import httpx

logger = logging.getLogger(__name__)

LITELLM_URL = (
    "https://raw.githubusercontent.com/BerriAI/litellm/main/"
    "model_prices_and_context_window.json"
)
OPENROUTER_URL = "https://openrouter.ai/api/v1/models"
MODELSDEV_URL = "https://models.dev/api.json"

REFRESH_TTL = 24 * 3600  # s; a cache younger than this is authoritative
REFRESH_CHECK_INTERVAL = 3600  # s; background loop wake-up
FETCH_TIMEOUT = 30.0
# models.dev returns 403 to Python's default UA; a browser UA is harmless
# for the other two sources.
FETCH_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
}

# Provider prefixes prepended to bare ids as low-confidence lookup candidates,
# so e.g. "kimi-k2.7-code" can hit OpenRouter's "moonshotai/kimi-k2.7-code".
KNOWN_ORGS = (
    "anthropic",
    "openai",
    "google",
    "z-ai",
    "x-ai",
    "deepseek",
    "moonshotai",
    "qwen",
    "bytedance",
    "mistralai",
    "meta-llama",
    "minimax",
)


@dataclass(frozen=True)
class ModelPrice:
    """USD per token (reasoning is billed at the output rate)."""

    input: float
    output: float
    cache_read: float = 0.0
    cache_write: float = 0.0

    def cost(self, in_t: int, out_t: int, cr_t: int, cw_t: int, rs_t: int) -> float:
        return (
            self.input * in_t
            + self.output * out_t
            + self.cache_read * cr_t
            + self.cache_write * cw_t
            + self.output * rs_t
        )


def _f(value: Any) -> float:
    """Coerce a price value (possibly a string, as OpenRouter returns) to float."""
    try:
        result = float(value if value is not None else 0)
    except (TypeError, ValueError):
        return 0.0
    return result if result >= 0 and result == result and result != float("inf") else 0.0


def _parse_litellm(raw: Any) -> dict[str, ModelPrice]:
    """Parse LiteLLM's flat model → cost map (USD per token)."""
    out: dict[str, ModelPrice] = {}
    if not isinstance(raw, dict):
        return out
    for key, entry in raw.items():
        # github_copilot/* entries are subscription-priced ($0.00) and would
        # underprice real usage; "_"-prefixed keys are file metadata.
        if not isinstance(entry, dict) or key.startswith(("github_copilot/", "_")):
            continue
        inp = _f(entry.get("input_cost_per_token"))
        outp = _f(entry.get("output_cost_per_token"))
        if not inp and not outp:
            continue  # unusable without both base rates (tiered *_above_*k ignored)
        out[key.lower()] = ModelPrice(
            inp,
            outp,
            _f(entry.get("cache_read_input_token_cost")),
            _f(entry.get("cache_creation_input_token_cost")),
        )
    return out


def _parse_openrouter(raw: Any) -> dict[str, ModelPrice]:
    """Parse OpenRouter's model list (string prices, USD per token, no cache)."""
    out: dict[str, ModelPrice] = {}
    data = raw.get("data") if isinstance(raw, dict) else None
    for model in data or []:
        if not isinstance(model, dict):
            continue
        model_id = model.get("id")
        pricing_data = model.get("pricing")
        if not model_id or not isinstance(pricing_data, dict):
            continue
        inp = _f(pricing_data.get("prompt"))
        outp = _f(pricing_data.get("completion"))
        if not inp and not outp:
            continue
        out[str(model_id).lower()] = ModelPrice(inp, outp)
    return out


def _parse_modelsdev(raw: Any) -> dict[str, ModelPrice]:
    """Parse models.dev's provider → models → cost map (USD per MILLION tokens)."""
    out: dict[str, ModelPrice] = {}
    if not isinstance(raw, dict):
        return out
    for provider, pdata in raw.items():
        models = pdata.get("models") if isinstance(pdata, dict) else None
        if not isinstance(models, dict):
            continue
        for model_id, mdata in models.items():
            cost = mdata.get("cost") if isinstance(mdata, dict) else None
            if not isinstance(cost, dict):
                continue
            inp = _f(cost.get("input")) / 1_000_000
            outp = _f(cost.get("output")) / 1_000_000
            if not inp and not outp:
                continue
            price = ModelPrice(
                inp,
                outp,
                _f(cost.get("cache_read")) / 1_000_000,
                _f(cost.get("cache_write")) / 1_000_000,
            )
            # Provider-qualified key first (exact org/name hits), then the bare
            # id; on bare-id collisions across providers the first provider wins.
            out.setdefault(f"{provider}/{model_id}".lower(), price)
            out.setdefault(str(model_id).lower(), price)
    return out


_DATE_SUFFIX = re.compile(r"-\d{4}-\d{2}-\d{2}$")
_DASH_BETWEEN_DIGITS = re.compile(r"(?<=\d)-(?=\d)")
_DOT_BETWEEN_DIGITS = re.compile(r"(?<=\d)\.(?=\d)")


def lookup_candidates(raw_id: str) -> list[str]:
    """Normalized lookup keys for a raw model id, most-confident first.

    Stages: exact → strip ":backend" suffix → strip "org/" prefix → strip a
    trailing "-YYYY-MM-DD" date stamp (then its org prefix too) →
    version-separator swap (digit-dash-digit ↔ digit-dot-digit). Finally,
    known-org-prefixed variants of every candidate so bare ids can match
    OpenRouter-style "org/model" keys. Deduplicated, order preserved.
    """
    base = raw_id.strip().lower()

    stages = [base]
    colon_stripped = base.split(":", 1)[0]
    if colon_stripped != base:
        stages.append(colon_stripped)
    # org-prefix strip of every form so far
    for s in list(stages):
        if "/" in s:
            stages.append(s.split("/", 1)[1])
    # date-suffix strip of every form so far (plus its org-stripped variant)
    for s in list(stages):
        nodate = _DATE_SUFFIX.sub("", s)
        if nodate != s:
            stages.append(nodate)
            if "/" in nodate:
                stages.append(nodate.split("/", 1)[1])

    out: list[str] = []
    seen: set[str] = set()

    def emit(candidate: str) -> None:
        if candidate and candidate not in seen:
            seen.add(candidate)
            out.append(candidate)

    # 1. exact + structural strips, most-confident first
    for s in stages:
        emit(s)
    # 2. version-separator swaps of every stage
    for s in stages:
        emit(_DASH_BETWEEN_DIGITS.sub(".", s))  # claude-opus-4-8 → claude-opus-4.8
        emit(_DOT_BETWEEN_DIGITS.sub("-", s))  # claude-opus-4.8 → claude-opus-4-8
    # 3. org-prefixed variants of the bare candidates so far (never re-prefix a
    # key that already carries an org) — lowest confidence, so they come last.
    for s in list(out):
        if "/" not in s:
            for org in KNOWN_ORGS:
                emit(f"{org}/{s}")

    return out


_SOURCE_NAMES = ("litellm", "openrouter", "modelsdev")
_URLS = (LITELLM_URL, OPENROUTER_URL, MODELSDEV_URL)
_PARSERS: tuple[Callable[[Any], dict[str, ModelPrice]], ...] = (
    _parse_litellm,
    _parse_openrouter,
    _parse_modelsdev,
)


async def _fetch_json(client: httpx.AsyncClient, url: str) -> Any:
    """GET `url` and parse JSON. Module-level so tests can monkeypatch it."""
    response = await client.get(url)
    response.raise_for_status()
    return response.json()


class PricingService:
    """In-memory pricing tables (one dict per source) + on-disk cache.

    Lookup is source-outer / candidate-inner: every normalized candidate is
    tried against LiteLLM first, then OpenRouter, then models.dev — the first
    source with any match wins, per the configured fallback order.
    """

    def __init__(self, cache_path: Path):
        self._cache_path = Path(cache_path)
        self._sources: list[dict[str, ModelPrice]] = [{}, {}, {}]
        self._fetched_at = 0.0
        self._lock = asyncio.Lock()
        self._task: asyncio.Task | None = None
        self._load_cache()

    @property
    def ready(self) -> bool:
        return any(self._sources)

    def price_for(self, raw_id: str) -> ModelPrice | None:
        for source in self._sources:  # chain: LiteLLM → OpenRouter → models.dev
            for candidate in lookup_candidates(raw_id):
                hit = source.get(candidate)
                if hit is not None:
                    return hit
        return None

    async def ensure_ready(self, timeout: float = 5.0) -> None:
        """Wait (bounded) until any price data exists; never raises.

        On timeout/failure the caller simply serves without costs — pricing is
        decorative and must never degrade the core token view.
        """
        if self.ready:
            return
        try:
            await asyncio.wait_for(self._refresh_locked(), timeout)
        except Exception as e:  # incl. asyncio.TimeoutError
            logger.warning("pricing not ready (%s); serving without costs", e)

    async def refresh(self) -> None:
        """TTL-guarded refresh; no-op while the cache is still fresh."""
        await self._refresh_locked()

    async def _refresh_locked(self) -> None:
        async with self._lock:
            if self.ready and time.time() - self._fetched_at < REFRESH_TTL:
                return
            try:
                async with httpx.AsyncClient(
                    timeout=FETCH_TIMEOUT, headers=FETCH_HEADERS
                ) as client:
                    results = await asyncio.gather(
                        *(_fetch_json(client, url) for url in _URLS),
                        return_exceptions=True,
                    )
            except Exception as e:
                logger.error("pricing fetch failed: %s", e)
                return

            ok = False
            for i, (result, parse, name) in enumerate(
                zip(results, _PARSERS, _SOURCE_NAMES)
            ):
                if isinstance(result, BaseException):
                    # Keep the stale dict for this source; the hourly loop
                    # retries because _fetched_at only advances on success.
                    logger.warning("pricing source %s failed: %s", name, result)
                    continue
                try:
                    self._sources[i] = parse(result)
                except Exception as e:
                    logger.warning("pricing source %s parse failed: %s", name, e)
                    continue
                ok = True

            if ok:
                self._fetched_at = time.time()
                self._save_cache()
                logger.info(
                    "pricing refreshed (%d/%d/%d entries)",
                    *(len(s) for s in self._sources),
                )

    def start_background(self) -> None:
        """Start the startup-prefetch + hourly refresh loop (non-blocking)."""
        if self._task is None:
            self._task = asyncio.create_task(self._loop())

    def stop_background(self) -> None:
        if self._task is not None:
            self._task.cancel()
            self._task = None

    async def _loop(self) -> None:
        try:
            await self._refresh_locked()  # startup prefetch (skipped if fresh)
        except Exception as e:
            logger.error("pricing prefetch failed: %s", e)
        while True:
            await asyncio.sleep(REFRESH_CHECK_INTERVAL)
            try:
                await self._refresh_locked()
            except Exception as e:
                logger.error("pricing refresh failed: %s", e)

    def _load_cache(self) -> None:
        try:
            raw = json.loads(self._cache_path.read_text(encoding="utf-8"))
            sources_raw = raw.get("sources", {})
            self._sources = [
                {
                    k: ModelPrice(**v)
                    for k, v in (sources_raw.get(name) or {}).items()
                }
                for name in _SOURCE_NAMES
            ]
            self._fetched_at = float(raw.get("fetched_at", 0))
            logger.info(
                "pricing cache loaded (%d/%d/%d entries)",
                *(len(s) for s in self._sources),
            )
        except (OSError, ValueError, TypeError) as e:
            logger.info("no usable pricing cache (%s); starting empty", e)

    def _save_cache(self) -> None:
        payload = {
            "fetched_at": self._fetched_at,
            "sources": {
                name: {
                    key: {
                        "input": p.input,
                        "output": p.output,
                        "cache_read": p.cache_read,
                        "cache_write": p.cache_write,
                    }
                    for key, p in source.items()
                }
                for name, source in zip(_SOURCE_NAMES, self._sources)
            },
        }
        tmp = self._cache_path.with_name(self._cache_path.name + ".tmp")
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        tmp.replace(self._cache_path)


_service: PricingService | None = None


def get_pricing_service(usage_db_path: Path) -> PricingService:
    """Module singleton; the cache lives beside the usage DB so the docker
    ``./data`` volume preserves it across rebuilds."""
    global _service
    if _service is None:
        _service = PricingService(Path(usage_db_path).parent / "pricing-cache.json")
    return _service
