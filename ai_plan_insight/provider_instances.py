# ai_plan_insight/provider_instances.py
"""v2 runtime manager: fetch scheduling, push dispatch, caching, and sorting.

Encapsulates all v2 runtime state so web.py only needs thin endpoint wrappers.
"""
from __future__ import annotations

import asyncio
import logging
import random
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

import httpx

from .instance_config import V2Config, V2InstanceConfig
from .api_schemas import UsageResponse
from .provider_registry import (
    build_fetch_provider,
    convert_push_payload,
    make_card_title,
    get_push_request_model,
    get_type_display_name,
)
from . import provider_v2_store

logger = logging.getLogger(__name__)

REFRESH_INTERVAL = 30

# Per-instance random delay before each fetch, so instances sharing one upstream
# site (and one source IP) don't fire simultaneously every round.
FETCH_JITTER_MAX = 3.0

# After a 429 the instance is skipped for a while: 60s, 120s, 240s ... capped.
# A ``Retry-After`` header, when present, wins over the exponential schedule.
RATE_LIMIT_BACKOFF_BASE = 60.0
RATE_LIMIT_BACKOFF_MAX = 900.0


class V2RuntimeManager:
    """Manages v2 provider instances: fetch loop, push handling, caching, DB."""

    def __init__(
        self,
        config: V2Config,
        db_path: Path,
        instance_errors: dict[str, str] | None = None,
    ) -> None:
        self._config = config
        self._config_error: str | None = None
        self._instance_errors: dict[str, str] = instance_errors or {}
        self._enabled = True
        self._db_path = db_path

        # Runtime state keyed by instance_id
        self._fetch_results: dict[str, UsageResponse] = {}
        self._pushed_results: dict[str, UsageResponse] = {}
        self._pushed_at: dict[str, datetime] = {}
        self._consecutive_failures: dict[str, int] = {}
        self._prev_results: dict[str, UsageResponse] = {}
        self._backoff_until: dict[str, datetime] = {}
        self._rate_limit_hits: dict[str, int] = {}
        self._last_updated: str | None = None
        self._refresh_task: asyncio.Task | None = None

    @classmethod
    def disabled(cls, config_error: str, db_path: Path) -> "V2RuntimeManager":
        """A manager that is not enabled and only surfaces a config error."""
        mgr = cls.__new__(cls)
        mgr._config = V2Config(providers={})
        mgr._config_error = config_error
        mgr._instance_errors = {}
        mgr._enabled = False
        mgr._db_path = db_path
        mgr._fetch_results = {}
        mgr._pushed_results = {}
        mgr._pushed_at = {}
        mgr._consecutive_failures = {}
        mgr._prev_results = {}
        mgr._backoff_until = {}
        mgr._rate_limit_hits = {}
        mgr._last_updated = None
        mgr._refresh_task = None
        return mgr

    @property
    def config(self) -> V2Config:
        return self._config

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def has_fetch_instances(self) -> bool:
        return any(
            inst.mode == "fetch" for inst in self._config.providers.values()
        )

    def _conn(self) -> sqlite3.Connection:
        return sqlite3.connect(self._db_path)

    def _persist_snapshot(
        self,
        instance_id: str,
        type_name: str,
        mode: str,
        label: str,
        usage: UsageResponse,
    ) -> None:
        """Best-effort DB persist; never raises."""
        try:
            with closing(self._conn()) as conn:
                provider_v2_store.upsert_v2_snapshot(
                    conn, instance_id, type_name, mode, label, usage,
                )
                conn.commit()
        except Exception as e:
            logger.warning("v2 persist failed for %s: %s", instance_id, e)

    async def _fetch_one_instance(
        self, instance_id: str, cfg: V2InstanceConfig,
    ) -> UsageResponse:
        """Fetch usage for one fetch-mode instance."""
        title = make_card_title(cfg.type, cfg.label)
        provider = build_fetch_provider(instance_id, cfg)
        provider.authenticate()
        raw = await provider.fetch_usage()
        parsed = provider.parse_usage(raw)

        if hasattr(provider, "fetch_token_usage"):
            parsed.token_usage = await provider.fetch_token_usage()

        if hasattr(provider, "fetch_history_usage"):
            try:
                parsed.history_usage = await provider.fetch_history_usage()
            except Exception as e:
                logger.warning("v2 history fetch failed for %s: %s", instance_id, e)

        # Override the provider name with the v2 title
        parsed.provider = title

        # Convert to UsageResponse (mirrors web.py _fetch_one logic)
        from .api_schemas import (
            LimitResponse, UsageDetailResponse, TokenUsageResponse,
            HistoryUsagePeriodResponse, HistoryModelUsageResponse, ModelStatResponse,
        )

        limits = []
        for lim in parsed.limits:
            details = [
                UsageDetailResponse(model_code=d.model_code, usage=d.usage)
                for d in lim.usage_details
            ]
            limits.append(LimitResponse(
                duration=lim.duration, time_unit=lim.time_unit,
                limit=lim.limit, used=lim.used, remaining=lim.remaining,
                reset_time=lim.reset_time.isoformat() if lim.reset_time else None,
                usage_details=details, limit_type=lim.limit_type,
            ))

        token_usage = [
            TokenUsageResponse(
                period=t.period, total_tokens=t.total_tokens, total_calls=t.total_calls,
            )
            for t in parsed.token_usage
        ]

        history_usage = None
        if parsed.history_usage:
            hu = parsed.history_usage
            history_usage = HistoryUsagePeriodResponse(
                period=hu.period, granularity=hu.granularity,
                x_time=hu.x_time, tokens_usage=hu.tokens_usage,
                model_call_count=hu.model_call_count,
                total_tokens=hu.total_tokens, total_calls=hu.total_calls,
                models=[
                    HistoryModelUsageResponse(
                        model_name=m.model_name, total_tokens=m.total_tokens,
                        total_calls=m.total_calls, tokens_usage=m.tokens_usage,
                    )
                    for m in hu.models
                ],
            )

        model_stats = [
            ModelStatResponse(model=m.model, total_tokens=m.total_tokens, requests=m.requests)
            for m in parsed.model_stats
        ]

        return UsageResponse(
            provider=title, user_id=parsed.user_id,
            membership_level=parsed.membership_level,
            limits=limits, balances=parsed.balances,
            token_usage=token_usage, history_usage=history_usage,
            model_stats=model_stats,
        )

    def _jitter_delay(self) -> float:
        return random.uniform(0.0, FETCH_JITTER_MAX)

    async def _fetch_one_jittered(
        self, instance_id: str, cfg: V2InstanceConfig,
    ) -> UsageResponse:
        """``_fetch_one_instance`` preceded by a short random delay."""
        delay = self._jitter_delay()
        if delay > 0:
            await asyncio.sleep(delay)
        return await self._fetch_one_instance(instance_id, cfg)

    @staticmethod
    def _retry_after_seconds(exc: BaseException) -> float | None:
        """Seconds to wait per a 429's ``Retry-After``; None if absent/unparseable."""
        if not isinstance(exc, httpx.HTTPStatusError):
            return None
        raw = exc.response.headers.get("retry-after")
        if not raw:
            return None
        raw = raw.strip()
        try:
            return max(0.0, float(int(raw)))
        except ValueError:
            pass
        try:
            when = parsedate_to_datetime(raw)
        except (TypeError, ValueError):
            return None
        if when is None:
            return None
        if when.tzinfo is None:
            when = when.astimezone()
        return max(0.0, (when - datetime.now().astimezone()).total_seconds())

    def _note_rate_limited(self, instance_id: str, exc: BaseException, now: datetime) -> None:
        """Record a 429 and schedule the next allowed fetch for this instance."""
        hits = self._rate_limit_hits.get(instance_id, 0) + 1
        self._rate_limit_hits[instance_id] = hits

        retry_after = self._retry_after_seconds(exc)
        if retry_after is None:
            delay = RATE_LIMIT_BACKOFF_BASE * (2 ** (hits - 1))
        else:
            delay = retry_after
        delay = min(delay, RATE_LIMIT_BACKOFF_MAX)

        self._backoff_until[instance_id] = now + timedelta(seconds=delay)
        logger.warning(
            "v2 %s rate limited (429, hit #%d); backing off %.0fs",
            instance_id, hits, delay,
        )

    @staticmethod
    def _is_rate_limited(exc: BaseException) -> bool:
        return (
            isinstance(exc, httpx.HTTPStatusError)
            and exc.response.status_code == 429
        )

    async def _fetch_all(self) -> None:
        """Fetch all fetch-mode instances concurrently, skipping backed-off ones."""
        now = datetime.now().astimezone()
        fetch_instances = {}
        for iid, cfg in self._config.providers.items():
            if cfg.mode != "fetch":
                continue
            until = self._backoff_until.get(iid)
            if until and until > now:
                logger.debug(
                    "v2 %s still backing off for %.0fs, skipping",
                    iid, (until - now).total_seconds(),
                )
                continue
            fetch_instances[iid] = cfg
        if not fetch_instances:
            return

        tasks = {
            iid: self._fetch_one_jittered(iid, cfg)
            for iid, cfg in fetch_instances.items()
        }
        results = await asyncio.gather(*tasks.values(), return_exceptions=True)

        now = datetime.now().astimezone()
        for (iid, _), result in zip(tasks.items(), results):
            cfg = fetch_instances[iid]
            if isinstance(result, UsageResponse):
                self._consecutive_failures[iid] = 0
                self._rate_limit_hits.pop(iid, None)
                self._backoff_until.pop(iid, None)
                self._prev_results[iid] = result
                self._fetch_results[iid] = result
                self._persist_snapshot(iid, cfg.type, cfg.mode, cfg.label, result)
            else:
                if self._is_rate_limited(result):
                    self._note_rate_limited(iid, result, now)
                self._consecutive_failures[iid] = self._consecutive_failures.get(iid, 0) + 1
                title = make_card_title(cfg.type, cfg.label)
                if self._consecutive_failures[iid] < 3 and iid in self._prev_results:
                    self._fetch_results[iid] = self._prev_results[iid]
                else:
                    self._fetch_results[iid] = UsageResponse(provider=title, error=str(result))

    async def _background_refresh(self) -> None:
        """Background fetch loop for fetch-mode instances."""
        while True:
            try:
                await self._fetch_all()
                self._last_updated = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
            except Exception as e:
                logger.error("v2 background refresh failed: %s", e)
            await asyncio.sleep(REFRESH_INTERVAL)

    def restore_snapshots(self) -> None:
        """Restore v2 snapshots from DB for instances still in config."""
        active_ids = set(self._config.providers.keys())
        type_mode_map = {
            iid: (cfg.type, cfg.mode) for iid, cfg in self._config.providers.items()
        }
        label_map = {
            iid: cfg.label for iid, cfg in self._config.providers.items()
        }
        try:
            with closing(self._conn()) as conn:
                rows = provider_v2_store.load_v2_snapshots(
                    conn, active_ids, type_mode_map, label_map,
                )
        except Exception as e:
            logger.warning("v2 snapshot restore failed: %s", e)
            return

        latest_ts: datetime | None = None
        for instance_id, type_name, mode, label, recorded_at, usage in rows:
            title = make_card_title(type_name, label)
            usage.provider = title  # Use current config title
            ts: datetime | None = None
            try:
                ts = datetime.fromisoformat(recorded_at)
                if ts.tzinfo is None:
                    ts = ts.astimezone()
            except Exception:
                ts = None
            if mode == "push":
                if ts is None:
                    continue
                self._pushed_results[instance_id] = usage
                self._pushed_at[instance_id] = ts
            elif mode == "fetch":
                self._fetch_results[instance_id] = usage
                self._prev_results[instance_id] = usage
            if ts is not None and (latest_ts is None or ts > latest_ts):
                latest_ts = ts

        if latest_ts is not None and self._last_updated is None:
            self._last_updated = latest_ts.astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")

        if rows:
            logger.info("v2: restored %d snapshot(s)", len(rows))

    def start(self) -> None:
        """Initialize DB schema, restore snapshots, start fetch loop if needed."""
        try:
            provider_v2_store.init_v2_db(self._db_path)
        except Exception as e:
            logger.error("v2 DB init failed: %s", e)

        self.restore_snapshots()

        if self.has_fetch_instances:
            self._refresh_task = asyncio.create_task(self._background_refresh())

    def stop(self) -> None:
        """Cancel the background fetch loop."""
        if self._refresh_task:
            self._refresh_task.cancel()
            self._refresh_task = None

    def reload(
        self,
        new_config: V2Config,
        instance_errors: dict[str, str] | None = None,
    ) -> None:
        """Atomically replace the runtime config (hot reload).

        1. Stop the old fetch task.
        2. Drop in-memory results for instances that are no longer in config,
           or whose type/mode changed (a fetch card would otherwise linger
           after switching to push, since nothing refreshes it any more).
           Results for unchanged instances are preserved.
        3. Swap in the new config + instance_errors, mark enabled.
        4. restore_snapshots() — only restores instances still in the new config.
        5. Restart the fetch task if the new config has fetch instances.
        """
        self.stop()

        old_providers = self._config.providers
        keep_ids = {
            iid for iid, cfg in new_config.providers.items()
            if iid not in old_providers
            or (old_providers[iid].type, old_providers[iid].mode) == (cfg.type, cfg.mode)
        }
        for state in (
            self._fetch_results,
            self._pushed_results,
            self._pushed_at,
            self._prev_results,
            self._consecutive_failures,
            self._backoff_until,
            self._rate_limit_hits,
        ):
            for iid in list(state.keys()):
                if iid not in keep_ids:
                    del state[iid]

        self._config = new_config
        self._instance_errors = instance_errors or {}
        self._enabled = True
        self._config_error = None

        self.restore_snapshots()

        if self.has_fetch_instances:
            self._refresh_task = asyncio.create_task(self._background_refresh())

    def disable(self, config_error: str) -> None:
        """Mark this manager unusable in place (top-level config error).

        Stops the fetch task, drops all in-memory instance results, and clears
        the config. Used by ConfigService when the config file becomes
        unparseable. (For initial construction use the `disabled` classmethod.)
        """
        self.stop()
        self._fetch_results.clear()
        self._pushed_results.clear()
        self._pushed_at.clear()
        self._prev_results.clear()
        self._consecutive_failures.clear()
        self._backoff_until.clear()
        self._rate_limit_hits.clear()
        self._config = V2Config(providers={})
        self._config_error = config_error
        self._instance_errors = {}
        self._enabled = False

    async def handle_push(self, instance_id: str, payload: Any) -> dict:
        """Process a push payload for one instance. Raises ValueError on error."""
        if instance_id not in self._config.providers:
            raise ValueError(f"instance {instance_id!r} not registered")

        cfg = self._config.providers[instance_id]
        if cfg.mode != "push":
            raise ValueError(f"instance {instance_id!r} is a fetch instance")

        title = make_card_title(cfg.type, cfg.label)
        usage = convert_push_payload(cfg.type, instance_id, title, payload)

        self._pushed_results[instance_id] = usage
        now = datetime.now().astimezone()
        self._pushed_at[instance_id] = now
        self._last_updated = now.strftime("%Y-%m-%d %H:%M:%S %Z")

        self._persist_snapshot(instance_id, cfg.type, cfg.mode, cfg.label, usage)

        return {"status": "ok", "instance_id": instance_id}

    def _sort_key(self, instance_id: str, provider_title: str) -> tuple[int, str, str]:
        cfg = self._config.providers.get(instance_id)
        order = cfg.order if cfg else 999
        return (order, provider_title, instance_id)

    def get_usage(self) -> list[UsageResponse]:
        """Return combined fetch + valid push results, sorted by (order, title, id)."""
        now = datetime.now().astimezone()
        cutoff = now - timedelta(hours=self._config.push_card_ttl_hours)

        valid_pushed = [
            r for iid, r in self._pushed_results.items()
            if self._pushed_at.get(iid) and self._pushed_at[iid] >= cutoff
        ]

        combined = list(self._fetch_results.values()) + valid_pushed

        # Build a map from provider title back to instance_id for sorting
        title_to_iid: dict[str, str] = {}
        for iid, cfg in self._config.providers.items():
            title = make_card_title(cfg.type, cfg.label)
            title_to_iid[title] = iid

        combined.sort(
            key=lambda r: self._sort_key(
                title_to_iid.get(r.provider, ""), r.provider,
            )
        )
        return combined

    def get_usage_v2(self) -> list[dict]:
        """Return v2 response dicts with instance_id, type, instance_label."""
        cards = self.get_usage()
        result = []
        for card in cards:
            d = card.model_dump()
            # Find the matching instance
            for iid, cfg in self._config.providers.items():
                title = make_card_title(cfg.type, cfg.label)
                if card.provider == title:
                    d["instance_id"] = iid
                    d["type"] = cfg.type
                    d["instance_label"] = cfg.label
                    d["type_display_name"] = get_type_display_name(cfg.type)
                    break
            result.append(d)
        return result

    def get_status(self) -> dict:
        return {
            "enabled": self._enabled,
            "last_updated": self._last_updated,
            "config_error": self._config_error,
            "instance_errors": dict(self._instance_errors),
        }
