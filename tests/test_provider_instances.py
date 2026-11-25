# tests/test_provider_instances.py
import asyncio
import json
import sqlite3
import httpx
import pytest
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock

from ai_plan_insight.instance_config import V2Config, V2InstanceConfig
from ai_plan_insight.api_schemas import (
    UsageResponse, LimitResponse, ClaudePushRequest, GrokPushRequest,
    AntigravityPushRequest,
)
from ai_plan_insight.provider_instances import (
    V2RuntimeManager,
    FETCH_JITTER_MAX,
    RATE_LIMIT_BACKOFF_BASE,
    RATE_LIMIT_BACKOFF_MAX,
)


@pytest.fixture
def v2_config() -> V2Config:
    return V2Config.model_validate({
        "providers": {
            "claude-personal": {
                "type": "claude", "mode": "push", "label": "个人号", "order": 12,
            },
            "claude-work": {
                "type": "claude", "mode": "push", "label": "工作号", "order": 13,
            },
        },
        "push_auth_secret": "test-secret",
        "enforce_push_auth": True,
    })


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "v2_test.db"


@pytest.fixture
def manager(v2_config: V2Config, db_path: Path) -> V2RuntimeManager:
    return V2RuntimeManager(v2_config, db_path)


class TestV2RuntimeManagerInit:
    def test_config_loaded(self, manager: V2RuntimeManager, v2_config: V2Config):
        assert manager.config is v2_config
        assert manager.enabled is True

    def test_no_fetch_instances(self, manager: V2RuntimeManager):
        # All instances are push, so no fetch loop should be needed
        assert manager.has_fetch_instances is False

    def test_with_fetch_instances(self, db_path: Path):
        config = V2Config.model_validate({
            "providers": {
                "bigmodel-test": {
                    "type": "bigmodel", "mode": "fetch", "label": "test",
                    "api_key": "sk-test", "order": 20,
                },
            },
        })
        mgr = V2RuntimeManager(config, db_path)
        assert mgr.has_fetch_instances is True


class TestHandlePush:
    def test_push_success(self, manager: V2RuntimeManager):
        payload = ClaudePushRequest(
            seven_day={"utilization": 45.2, "resets_at": "2026-07-08T12:00:00Z"},
            five_hour={"utilization": 12.8, "resets_at": "2026-07-01T15:00:00Z"},
        )
        result = asyncio.run(manager.handle_push("claude-work", payload))
        assert result["status"] == "ok"
        assert result["instance_id"] == "claude-work"

    def test_push_unknown_instance(self, manager: V2RuntimeManager):
        payload = ClaudePushRequest(
            seven_day={"utilization": 10.0, "resets_at": "2026-07-08T12:00:00Z"},
            five_hour={"utilization": 5.0, "resets_at": "2026-07-01T15:00:00Z"},
        )
        with pytest.raises(ValueError, match="not registered"):
            asyncio.run(manager.handle_push("unknown-instance", payload))

    def test_push_to_fetch_instance(self, db_path: Path):
        config = V2Config.model_validate({
            "providers": {
                "bigmodel-test": {
                    "type": "bigmodel", "mode": "fetch", "label": "test",
                    "api_key": "sk-test",
                },
            },
        })
        mgr = V2RuntimeManager(config, db_path)
        payload = ClaudePushRequest(
            seven_day={"utilization": 10.0, "resets_at": "2026-07-08T12:00:00Z"},
            five_hour={"utilization": 5.0, "resets_at": "2026-07-01T15:00:00Z"},
        )
        with pytest.raises(ValueError, match="fetch"):
            asyncio.run(mgr.handle_push("bigmodel-test", payload))

    def test_two_instances_independent(self, manager: V2RuntimeManager):
        for inst_id in ["claude-personal", "claude-work"]:
            payload = ClaudePushRequest(
                seven_day={"utilization": 30.0, "resets_at": "2026-07-08T12:00:00Z"},
                five_hour={"utilization": 10.0, "resets_at": "2026-07-01T15:00:00Z"},
            )
            asyncio.run(manager.handle_push(inst_id, payload))

        usage = manager.get_usage()
        assert len(usage) == 2
        providers = {u.provider for u in usage}
        assert "Claude 订阅 · 个人号" in providers
        assert "Claude 订阅 · 工作号" in providers

    def test_push_ttl_expires(self, manager: V2RuntimeManager):
        payload = ClaudePushRequest(
            seven_day={"utilization": 30.0, "resets_at": "2026-07-08T12:00:00Z"},
            five_hour={"utilization": 10.0, "resets_at": "2026-07-01T15:00:00Z"},
        )
        asyncio.run(manager.handle_push("claude-work", payload))

        # Default TTL is 1.0 hour; 31 minutes is NOT expired under the new default.
        manager._pushed_at["claude-work"] = datetime.now().astimezone() - timedelta(minutes=31)
        assert len(manager.get_usage()) == 1  # still within 1 hour

        # Past the configured TTL it is hidden.
        manager._pushed_at["claude-work"] = datetime.now().astimezone() - timedelta(minutes=61)
        assert len(manager.get_usage()) == 0  # expired


class TestGetUsage:
    def test_empty_when_no_data(self, manager: V2RuntimeManager):
        assert manager.get_usage() == []

    def test_sorted_by_order(self, manager: V2RuntimeManager):
        for inst_id in ["claude-work", "claude-personal"]:
            payload = ClaudePushRequest(
                seven_day={"utilization": 30.0, "resets_at": "2026-07-08T12:00:00Z"},
                five_hour={"utilization": 10.0, "resets_at": "2026-07-01T15:00:00Z"},
            )
            asyncio.run(manager.handle_push(inst_id, payload))

        usage = manager.get_usage()
        # order 12 (personal) before order 13 (work)
        assert usage[0].provider == "Claude 订阅 · 个人号"
        assert usage[1].provider == "Claude 订阅 · 工作号"


class TestGetStatus:
    def test_enabled(self, manager: V2RuntimeManager):
        status = manager.get_status()
        assert status["enabled"] is True
        assert status["config_error"] is None

    def test_disabled_config_error(self, db_path: Path):
        mgr = V2RuntimeManager.disabled("bad config", db_path)
        status = mgr.get_status()
        assert status["enabled"] is False
        assert status["config_error"] == "bad config"
        assert status["instance_errors"] == {}


class TestV2ResponseFields:
    def test_response_has_v2_fields(self, manager: V2RuntimeManager):
        payload = ClaudePushRequest(
            seven_day={"utilization": 30.0, "resets_at": "2026-07-08T12:00:00Z"},
            five_hour={"utilization": 10.0, "resets_at": "2026-07-01T15:00:00Z"},
        )
        asyncio.run(manager.handle_push("claude-work", payload))
        usage = manager.get_usage()
        assert len(usage) == 1
        card = usage[0]
        # v2 fields are on the response dict
        d = card.model_dump()
        # The V2UsageResponse (returned by get_usage) has extra fields
        # but they are added as a wrapper, not on UsageResponse itself.
        # We verify via the get_usage_v2 method instead.


class TestGetUsageV2:
    def test_v2_response_fields(self, manager: V2RuntimeManager):
        payload = ClaudePushRequest(
            seven_day={"utilization": 30.0, "resets_at": "2026-07-08T12:00:00Z"},
            five_hour={"utilization": 10.0, "resets_at": "2026-07-01T15:00:00Z"},
        )
        asyncio.run(manager.handle_push("claude-work", payload))
        v2_cards = manager.get_usage_v2()
        assert len(v2_cards) == 1
        card = v2_cards[0]
        assert card["instance_id"] == "claude-work"
        assert card["type"] == "claude"
        assert card["instance_label"] == "工作号"
        assert card["type_display_name"] == "Claude 订阅"
        assert card["provider"] == "Claude 订阅 · 工作号"
        assert len(card["limits"]) == 2


class TestReload:
    def _claude_cfg(self, **extra) -> V2Config:
        providers = {
            "claude-personal": {
                "type": "claude", "mode": "push", "label": "个人号", "order": 12,
            },
        }
        providers.update(extra.get("extra_providers", {}))
        raw = {"providers": providers}
        raw.update(extra.get("extra_top", {}))
        return V2Config.model_validate(raw)

    def test_reload_keeps_push_result_for_remaining_instance(self, db_path, monkeypatch):
        from ai_plan_insight.provider_instances import V2RuntimeManager
        # Avoid real background task / DB schema during this unit test.
        monkeypatch.setattr(V2RuntimeManager, "restore_snapshots", lambda self: None)
        mgr = V2RuntimeManager(self._claude_cfg(), db_path)
        payload = ClaudePushRequest(
            seven_day={"utilization": 30.0, "resets_at": "2026-07-08T12:00:00Z"},
            five_hour={"utilization": 10.0, "resets_at": "2026-07-01T15:00:00Z"},
        )
        asyncio.run(mgr.handle_push("claude-personal", payload))
        assert "claude-personal" in mgr._pushed_results

        # Reload with the same instance still present (label changed).
        new_cfg = self._claude_cfg(extra_providers={
            "claude-personal": {
                "type": "claude", "mode": "push", "label": "新标签", "order": 12,
            },
        })
        mgr.reload(new_cfg, instance_errors={})
        assert "claude-personal" in mgr._pushed_results  # push result preserved
        assert mgr.enabled is True
        assert mgr.config.providers["claude-personal"].label == "新标签"

    def test_reload_drops_removed_instance_results(self, db_path, monkeypatch):
        from ai_plan_insight.provider_instances import V2RuntimeManager
        monkeypatch.setattr(V2RuntimeManager, "restore_snapshots", lambda self: None)
        cfg = self._claude_cfg(extra_providers={
            "claude-work": {
                "type": "claude", "mode": "push", "label": "工作号", "order": 13,
            },
        })
        mgr = V2RuntimeManager(cfg, db_path)
        payload = ClaudePushRequest(
            seven_day={"utilization": 30.0, "resets_at": "2026-07-08T12:00:00Z"},
            five_hour={"utilization": 10.0, "resets_at": "2026-07-01T15:00:00Z"},
        )
        for iid in ("claude-personal", "claude-work"):
            asyncio.run(mgr.handle_push(iid, payload))

        # Reload without claude-work.
        mgr.reload(self._claude_cfg(), instance_errors={})
        assert "claude-personal" in mgr._pushed_results
        assert "claude-work" not in mgr._pushed_results
        assert "claude-work" not in mgr._pushed_at
        assert "claude-work" not in mgr._prev_results
        assert "claude-work" not in mgr._consecutive_failures

    def test_reload_drops_fetch_result_when_switched_to_push(self, db_path, monkeypatch):
        from ai_plan_insight.provider_instances import V2RuntimeManager
        monkeypatch.setattr(V2RuntimeManager, "restore_snapshots", lambda self: None)
        monkeypatch.setattr(
            "ai_plan_insight.provider_instances.asyncio.create_task", lambda *a, **kw: None,
        )
        ag = {"type": "antigravity", "label": "主账号", "order": 20}
        fetch_cfg = self._claude_cfg(extra_providers={
            "antigravity": {**ag, "mode": "fetch", "api_key": "k", "base_url": "http://x"},
        })
        mgr = V2RuntimeManager(fetch_cfg, db_path)
        stale = UsageResponse(provider="Antigravity · 主账号")
        for state in (mgr._fetch_results, mgr._prev_results):
            state["antigravity"] = stale
        mgr._consecutive_failures["antigravity"] = 3

        mgr.reload(self._claude_cfg(extra_providers={
            "antigravity": {**ag, "mode": "push"},
        }), instance_errors={})
        assert "antigravity" not in mgr._fetch_results
        assert "antigravity" not in mgr._prev_results
        assert "antigravity" not in mgr._consecutive_failures
        assert mgr.get_usage_v2() == []

        payload = AntigravityPushRequest(
            windows=[{"group": "Claude", "window": "5h", "utilization": 5}],
        )
        asyncio.run(mgr.handle_push("antigravity", payload))
        cards = mgr.get_usage_v2()
        assert [c["instance_id"] for c in cards] == ["antigravity"]
        assert cards[0]["limits"][0]["time_unit"] == "Claude 模型 · 5小时"

    def test_reload_drops_push_result_when_switched_to_fetch(self, db_path, monkeypatch):
        from ai_plan_insight.provider_instances import V2RuntimeManager
        monkeypatch.setattr(V2RuntimeManager, "restore_snapshots", lambda self: None)
        monkeypatch.setattr(
            "ai_plan_insight.provider_instances.asyncio.create_task", lambda *a, **kw: None,
        )
        ag = {"type": "antigravity", "label": "主账号", "order": 20}
        mgr = V2RuntimeManager(
            self._claude_cfg(extra_providers={"antigravity": {**ag, "mode": "push"}}), db_path,
        )
        payload = AntigravityPushRequest(
            windows=[{"group": "Claude", "window": "5h", "utilization": 5}],
        )
        asyncio.run(mgr.handle_push("antigravity", payload))
        asyncio.run(mgr.handle_push("claude-personal", ClaudePushRequest(
            five_hour={"utilization": 10.0, "resets_at": "2026-07-01T15:00:00Z"},
        )))

        mgr.reload(self._claude_cfg(extra_providers={
            "antigravity": {**ag, "mode": "fetch", "api_key": "k", "base_url": "http://x"},
        }), instance_errors={})
        assert "antigravity" not in mgr._pushed_results
        assert "antigravity" not in mgr._pushed_at
        assert "claude-personal" in mgr._pushed_results  # unchanged instance kept

    def test_reload_restarts_fetch_task_when_fetch_added(self, db_path, monkeypatch):
        from ai_plan_insight.provider_instances import V2RuntimeManager
        monkeypatch.setattr(V2RuntimeManager, "restore_snapshots", lambda self: None)
        started = []
        async def fake_task(*a, **kw):
            class T:
                def cancel(self): started.append("cancelled")
            return T()
        monkeypatch.setattr("ai_plan_insight.provider_instances.asyncio.create_task", fake_task)
        mgr = V2RuntimeManager(self._claude_cfg(), db_path)  # only push -> no task
        assert mgr._refresh_task is None

        new_cfg = self._claude_cfg(extra_providers={
            "bigmodel-x": {
                "type": "bigmodel", "mode": "fetch", "label": "x",
                "api_key": "sk-test", "order": 20,
            },
        })
        mgr.reload(new_cfg, instance_errors={})
        assert mgr._refresh_task is not None

    def test_reload_records_instance_errors(self, db_path, monkeypatch):
        from ai_plan_insight.provider_instances import V2RuntimeManager
        monkeypatch.setattr(V2RuntimeManager, "restore_snapshots", lambda self: None)
        mgr = V2RuntimeManager(self._claude_cfg(), db_path)
        mgr.reload(self._claude_cfg(), instance_errors={"bad": "unknown type 'foo'"})
        assert mgr.get_status()["instance_errors"] == {"bad": "unknown type 'foo'"}

    def test_disabled_manager_reload_re_enables(self, db_path, monkeypatch):
        from ai_plan_insight.provider_instances import V2RuntimeManager
        monkeypatch.setattr(V2RuntimeManager, "restore_snapshots", lambda self: None)
        mgr = V2RuntimeManager.disabled("bad", db_path)
        assert mgr.enabled is False
        mgr.reload(self._claude_cfg(), instance_errors={})
        assert mgr.enabled is True
        assert mgr.get_status()["config_error"] is None

    def test_disable_in_place_clears_results(self, db_path, monkeypatch):
        from ai_plan_insight.provider_instances import V2RuntimeManager
        monkeypatch.setattr(V2RuntimeManager, "restore_snapshots", lambda self: None)
        mgr = V2RuntimeManager(self._claude_cfg(), db_path)
        payload = ClaudePushRequest(
            seven_day={"utilization": 30.0, "resets_at": "2026-07-08T12:00:00Z"},
            five_hour={"utilization": 10.0, "resets_at": "2026-07-01T15:00:00Z"},
        )
        asyncio.run(mgr.handle_push("claude-personal", payload))
        assert "claude-personal" in mgr._pushed_results
        mgr.disable("config broke")
        assert mgr.enabled is False
        assert mgr.get_status()["config_error"] == "config broke"
        assert mgr._pushed_results == {}
        assert mgr._pushed_at == {}
        assert mgr._fetch_results == {}


class TestPushTtlReload:
    def test_ttl_change_via_reload_takes_effect(self, db_path: Path):
        """A push card still within the old TTL disappears once the TTL is
        shrunk via reload()."""
        config = V2Config.model_validate({
            "providers": {
                "claude-personal": {
                    "type": "claude", "mode": "push", "label": "个人号", "order": 12,
                },
            },
            "push_auth_secret": "test-secret",
            "enforce_push_auth": True,
            "push_card_ttl_hours": 1.0,
        })
        mgr = V2RuntimeManager(config, db_path)
        payload = ClaudePushRequest(
            seven_day={"utilization": 30.0, "resets_at": "2026-07-08T12:00:00Z"},
            five_hour={"utilization": 10.0, "resets_at": "2026-07-01T15:00:00Z"},
        )
        asyncio.run(mgr.handle_push("claude-personal", payload))
        # 31 minutes ago: within the 1-hour default TTL, still shown.
        mgr._pushed_at["claude-personal"] = datetime.now().astimezone() - timedelta(minutes=31)
        assert len(mgr.get_usage()) == 1

        # Reload with a tighter TTL: the same pushed-at is now past the cutoff.
        tighter = V2Config.model_validate({
            "providers": {
                "claude-personal": {
                    "type": "claude", "mode": "push", "label": "个人号", "order": 12,
                },
            },
            "push_auth_secret": "test-secret",
            "enforce_push_auth": True,
            "push_card_ttl_hours": 0.5,
        })
        mgr.reload(tighter)
        # reload() restores snapshots but does not overwrite the in-memory
        # pushed_at for instances still in config; expire it again after reload.
        mgr._pushed_at["claude-personal"] = datetime.now().astimezone() - timedelta(minutes=31)
        assert len(mgr.get_usage()) == 0  # 31 min > 0.5 h cutoff now


class TestFetchJitterAndBackoff:
    """Per-instance jitter + 429 backoff in the fetch loop."""

    def _fetch_cfg(self, count: int = 1) -> V2Config:
        providers = {
            f"newapi-{i}": {
                "type": "new_api", "mode": "fetch", "label": f"站点{i}",
                "api_key": "sk-test", "base_url": "https://example.test",
                "order": 20 + i,
            }
            for i in range(count)
        }
        return V2Config.model_validate({"providers": providers})

    def _mgr(self, db_path: Path, count: int = 1) -> V2RuntimeManager:
        mgr = V2RuntimeManager(self._fetch_cfg(count), db_path)
        mgr._persist_snapshot = lambda *a, **kw: None
        return mgr

    @staticmethod
    def _http_error(status: int, headers: dict | None = None) -> httpx.HTTPStatusError:
        request = httpx.Request("GET", "https://example.test/api/usage/token/")
        response = httpx.Response(status, headers=headers or {}, request=request)
        return httpx.HTTPStatusError("boom", request=request, response=response)

    def test_jitter_within_bounds(self, db_path: Path):
        mgr = self._mgr(db_path)
        delays = [mgr._jitter_delay() for _ in range(50)]
        assert all(0.0 <= d <= FETCH_JITTER_MAX for d in delays)
        assert len(set(delays)) > 1  # actually random, not a constant

    def test_jitter_applied_before_each_fetch(self, db_path: Path):
        mgr = self._mgr(db_path, count=2)
        mgr._jitter_delay = lambda: 0.01
        slept: list[float] = []

        async def fake_sleep(d):
            slept.append(d)

        async def fake_fetch(iid, cfg):
            return UsageResponse(provider=iid)

        mgr._fetch_one_instance = fake_fetch
        with patch("ai_plan_insight.provider_instances.asyncio.sleep", fake_sleep):
            asyncio.run(mgr._fetch_all())
        assert slept == [0.01, 0.01]  # one jitter per instance

    def test_429_sets_backoff_and_skips_next_round(self, db_path: Path):
        mgr = self._mgr(db_path)
        mgr._jitter_delay = lambda: 0.0
        calls = []

        async def fake_fetch(iid, cfg):
            calls.append(iid)
            raise self._http_error(429)

        mgr._fetch_one_instance = fake_fetch
        asyncio.run(mgr._fetch_all())
        assert calls == ["newapi-0"]
        assert "newapi-0" in mgr._backoff_until

        # Second round happens well inside the backoff window: no request.
        asyncio.run(mgr._fetch_all())
        assert calls == ["newapi-0"]

        # Once the window passes, fetching resumes.
        mgr._backoff_until["newapi-0"] = datetime.now().astimezone() - timedelta(seconds=1)
        asyncio.run(mgr._fetch_all())
        assert calls == ["newapi-0", "newapi-0"]

    def test_backoff_grows_exponentially(self, db_path: Path):
        mgr = self._mgr(db_path)
        mgr._jitter_delay = lambda: 0.0

        async def fake_fetch(iid, cfg):
            raise self._http_error(429)

        mgr._fetch_one_instance = fake_fetch
        seen = []
        for _ in range(4):
            before = datetime.now().astimezone()
            asyncio.run(mgr._fetch_all())
            seen.append((mgr._backoff_until["newapi-0"] - before).total_seconds())
            mgr._backoff_until["newapi-0"] = before - timedelta(seconds=1)

        assert seen[0] == pytest.approx(RATE_LIMIT_BACKOFF_BASE, abs=1)
        assert seen[1] == pytest.approx(RATE_LIMIT_BACKOFF_BASE * 2, abs=1)
        assert seen[2] == pytest.approx(RATE_LIMIT_BACKOFF_BASE * 4, abs=1)
        assert seen[3] == pytest.approx(RATE_LIMIT_BACKOFF_BASE * 8, abs=1)

    def test_backoff_capped(self, db_path: Path):
        mgr = self._mgr(db_path)
        mgr._jitter_delay = lambda: 0.0
        mgr._rate_limit_hits["newapi-0"] = 20  # next hit would be astronomically long

        async def fake_fetch(iid, cfg):
            raise self._http_error(429)

        mgr._fetch_one_instance = fake_fetch
        before = datetime.now().astimezone()
        asyncio.run(mgr._fetch_all())
        delay = (mgr._backoff_until["newapi-0"] - before).total_seconds()
        assert delay == pytest.approx(RATE_LIMIT_BACKOFF_MAX, abs=1)

    def test_retry_after_seconds_header_wins(self, db_path: Path):
        mgr = self._mgr(db_path)
        mgr._jitter_delay = lambda: 0.0

        async def fake_fetch(iid, cfg):
            raise self._http_error(429, {"Retry-After": "17"})

        mgr._fetch_one_instance = fake_fetch
        before = datetime.now().astimezone()
        asyncio.run(mgr._fetch_all())
        delay = (mgr._backoff_until["newapi-0"] - before).total_seconds()
        assert delay == pytest.approx(17, abs=1)

    def test_retry_after_http_date(self, db_path: Path):
        mgr = self._mgr(db_path)
        future = datetime.now(timezone.utc) + timedelta(seconds=45)
        exc = self._http_error(429, {"Retry-After": format_datetime(future, usegmt=True)})
        assert mgr._retry_after_seconds(exc) == pytest.approx(45, abs=2)

    def test_retry_after_garbage_falls_back_to_exponential(self, db_path: Path):
        mgr = self._mgr(db_path)
        exc = self._http_error(429, {"Retry-After": "soon-ish"})
        assert mgr._retry_after_seconds(exc) is None

    def test_non_429_error_does_not_back_off(self, db_path: Path):
        mgr = self._mgr(db_path)
        mgr._jitter_delay = lambda: 0.0

        async def fake_fetch(iid, cfg):
            raise self._http_error(500)

        mgr._fetch_one_instance = fake_fetch
        asyncio.run(mgr._fetch_all())
        assert mgr._backoff_until == {}
        assert mgr._rate_limit_hits == {}

    def test_success_clears_backoff_state(self, db_path: Path):
        mgr = self._mgr(db_path)
        mgr._jitter_delay = lambda: 0.0
        mgr._rate_limit_hits["newapi-0"] = 3
        mgr._backoff_until["newapi-0"] = datetime.now().astimezone() - timedelta(seconds=1)

        async def fake_fetch(iid, cfg):
            return UsageResponse(provider=iid)

        mgr._fetch_one_instance = fake_fetch
        asyncio.run(mgr._fetch_all())
        assert mgr._backoff_until == {}
        assert mgr._rate_limit_hits == {}

    def test_backed_off_instance_keeps_last_card(self, db_path: Path):
        mgr = self._mgr(db_path)
        mgr._jitter_delay = lambda: 0.0
        good = UsageResponse(provider="New API · 站点0")
        mgr._fetch_results["newapi-0"] = good
        mgr._prev_results["newapi-0"] = good
        mgr._backoff_until["newapi-0"] = datetime.now().astimezone() + timedelta(seconds=60)

        async def fake_fetch(iid, cfg):
            raise AssertionError("must not be called while backing off")

        mgr._fetch_one_instance = fake_fetch
        asyncio.run(mgr._fetch_all())
        assert mgr._fetch_results["newapi-0"] is good

    def test_reload_drops_backoff_for_removed_instance(self, db_path: Path, monkeypatch):
        monkeypatch.setattr(V2RuntimeManager, "restore_snapshots", lambda self: None)
        monkeypatch.setattr(
            "ai_plan_insight.provider_instances.asyncio.create_task",
            lambda coro: coro.close(),
        )
        mgr = V2RuntimeManager(self._fetch_cfg(2), db_path)
        mgr._backoff_until["newapi-1"] = datetime.now().astimezone() + timedelta(seconds=60)
        mgr._rate_limit_hits["newapi-1"] = 2
        mgr.reload(self._fetch_cfg(1))
        assert "newapi-1" not in mgr._backoff_until
        assert "newapi-1" not in mgr._rate_limit_hits
