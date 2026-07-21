# tests/test_provider_instances.py
import asyncio
import json
import sqlite3
import pytest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock

from ai_plan_insight.instance_config import V2Config, V2InstanceConfig
from ai_plan_insight.api_schemas import (
    UsageResponse, LimitResponse, ClaudePushRequest, GrokPushRequest,
)
from ai_plan_insight.provider_instances import V2RuntimeManager


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

        # Manually expire the push
        manager._pushed_at["claude-work"] = datetime.now().astimezone() - timedelta(minutes=31)
        usage = manager.get_usage()
        assert len(usage) == 0  # expired, not shown


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
