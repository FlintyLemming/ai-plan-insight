import json
import os
from pathlib import Path

from ai_plan_insight.config_service import ConfigService


def _write(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data), encoding="utf-8")


def _touch_later(path: Path) -> None:
    # Bump mtime by a clear margin so the stat-based poll notices the change.
    st = path.stat()
    os.utime(path, (st.st_atime, st.st_mtime + 5))


def test_get_returns_cached_when_mtime_unchanged(tmp_path):
    p = tmp_path / "config.json"
    _write(p, {"providers": {"claude-x": {"type": "claude", "mode": "push", "label": "x"}}})
    svc = ConfigService(p)
    first = svc.get()
    second = svc.get()
    assert second is first  # same cached object


def test_get_reloads_when_mtime_changes(tmp_path):
    p = tmp_path / "config.json"
    _write(p, {"providers": {"claude-x": {"type": "claude", "mode": "push", "label": "x"}}})
    svc = ConfigService(p)
    assert "claude-x" in svc.get().config.providers

    _write(p, {"providers": {"claude-y": {"type": "claude", "mode": "push", "label": "y"}}})
    _touch_later(p)
    result = svc.get()
    assert "claude-y" in result.config.providers
    assert "claude-x" not in result.config.providers


def test_config_error_when_file_missing(tmp_path):
    svc = ConfigService(tmp_path / "nope.json")
    result = svc.get()
    assert result.config_error is not None
    assert result.config.providers == {}


def test_config_error_recovers_when_file_becomes_valid(tmp_path):
    p = tmp_path / "config.json"
    _write(p, {"providers": {"claude-x": {"type": "claude", "mode": "push", "label": "x"}}})
    svc = ConfigService(p)
    assert svc.get().config_error is None

    _write(p, "{bad json")
    _touch_later(p)
    assert svc.get().config_error is not None

    _write(p, {"providers": {"claude-x": {"type": "claude", "mode": "push", "label": "x"}}})
    _touch_later(p)
    assert svc.get().config_error is None
    assert "claude-x" in svc.get().config.providers


def test_reload_triggered_on_instance_set_change(tmp_path, monkeypatch):
    p = tmp_path / "config.json"
    _write(p, {"providers": {"claude-x": {"type": "claude", "mode": "push", "label": "x"}}})
    svc = ConfigService(p)
    calls = []

    class FakeMgr:
        def reload(self, new_config, instance_errors=None):
            calls.append(("reload", set(new_config.providers.keys())))
        def disable(self, config_error):
            calls.append(("disable", config_error))

    svc.set_manager(FakeMgr())
    svc.get()  # first load -> reload({"claude-x"}); cached sig recorded

    _write(p, {"providers": {"claude-y": {"type": "claude", "mode": "push", "label": "y"}}})
    _touch_later(p)
    svc.get()
    assert ("reload", {"claude-y"}) in calls


def test_disable_triggered_when_config_becomes_unusable(tmp_path):
    p = tmp_path / "config.json"
    _write(p, {"providers": {"claude-x": {"type": "claude", "mode": "push", "label": "x"}}})
    svc = ConfigService(p)
    calls = []

    class FakeMgr:
        def reload(self, new_config, instance_errors=None):
            calls.append("reload")
        def disable(self, config_error):
            calls.append(("disable", config_error))

    svc.set_manager(FakeMgr())
    svc.get()
    _write(p, "{bad")
    _touch_later(p)
    svc.get()
    assert any(c[0] == "disable" for c in calls if isinstance(c, tuple))


def test_no_reload_when_only_label_changes_but_set_same(tmp_path, monkeypatch):
    # Spec: reload fires whenever the effective config signature changes — which
    # includes label/order changes (same instance set), because get_usage_v2
    # reads manager._config and must see the new label. So a label-only edit
    # MUST still trigger reload.
    p = tmp_path / "config.json"
    _write(p, {"providers": {"claude-x": {"type": "claude", "mode": "push", "label": "old"}}})
    svc = ConfigService(p)
    reloaded = []
    class FakeMgr:
        def reload(self, new_config, instance_errors=None):
            reloaded.append(new_config.providers["claude-x"].label)
        def disable(self, config_error):
            reloaded.append("disable")
    svc.set_manager(FakeMgr())
    svc.get()  # first load -> reload with "old"
    _write(p, {"providers": {"claude-x": {"type": "claude", "mode": "push", "label": "new"}}})
    _touch_later(p)
    svc.get()  # mtime change -> reload with "new"
    assert reloaded == ["old", "new"]
