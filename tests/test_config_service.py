import sqlite3
from contextlib import closing

from config_fixtures import seed_config

from ai_plan_insight import config_store
from ai_plan_insight.config_service import ConfigService


def _db(tmp_path, **kwargs):
    return seed_config(tmp_path / "usage.db", **kwargs)


def _edit(db, fn):
    with closing(sqlite3.connect(db)) as conn:
        fn(conn)


def _push_instance(label="x"):
    return {"type": "claude", "mode": "push", "label": label}


def test_get_returns_cached_when_version_unchanged(tmp_path):
    db = _db(tmp_path, providers={"claude-x": _push_instance()})
    svc = ConfigService(db)
    assert svc.get() is svc.get()  # same cached object


def test_get_reloads_when_version_changes(tmp_path):
    db = _db(tmp_path, providers={"claude-x": _push_instance()})
    svc = ConfigService(db)
    assert "claude-x" in svc.get().config.providers

    _edit(db, lambda c: config_store.delete_instance(c, "claude-x"))
    _edit(db, lambda c: config_store.create_instance(c, config_store.InstanceRow(
        instance_id="claude-y", type="claude", mode="push", label="y",
    )))
    result = svc.get()
    assert "claude-y" in result.config.providers
    assert "claude-x" not in result.config.providers


def test_empty_db_is_a_valid_empty_config(tmp_path):
    svc = ConfigService(_db(tmp_path))
    result = svc.get()
    assert result.config_error is None
    assert result.config.providers == {}


def test_config_error_when_db_cannot_be_opened(tmp_path):
    unopenable = tmp_path / "not-a-file"
    unopenable.mkdir()
    result = ConfigService(unopenable).get()
    assert result.config_error is not None
    assert result.config.providers == {}


def test_config_error_recovers_when_db_becomes_readable(tmp_path):
    db = tmp_path / "usage.db"  # no schema yet -> unreadable config
    svc = ConfigService(db)
    assert svc.get().config_error is not None

    seed_config(db, providers={"claude-x": _push_instance()})
    result = svc.get()
    assert result.config_error is None
    assert "claude-x" in result.config.providers


def test_disabled_instances_are_not_loaded(tmp_path):
    db = _db(tmp_path, providers={
        "on-inst": _push_instance("on"),
        "off-inst": {**_push_instance("off"), "enabled": False},
    })
    assert set(ConfigService(db).get().config.providers) == {"on-inst"}


class FakeMgr:
    def __init__(self):
        self.calls = []

    def reload(self, new_config, instance_errors=None):
        self.calls.append(("reload", set(new_config.providers), dict(instance_errors or {})))

    def disable(self, config_error):
        self.calls.append(("disable", config_error))


def test_reload_triggered_on_instance_set_change(tmp_path):
    db = _db(tmp_path, providers={"claude-x": _push_instance()})
    svc = ConfigService(db)
    mgr = FakeMgr()
    svc.set_manager(mgr)
    svc.get()  # first load -> reload({"claude-x"})

    _edit(db, lambda c: config_store.create_instance(c, config_store.InstanceRow(
        instance_id="claude-y", type="claude", mode="push", label="y",
    )))
    svc.get()
    assert ("reload", {"claude-x", "claude-y"}, {}) in mgr.calls


def test_reload_triggered_on_label_only_change(tmp_path):
    # get_usage_v2 reads manager._config for the card title, so a label-only
    # edit must still reach the manager.
    db = _db(tmp_path, providers={"claude-x": _push_instance("old")})
    svc = ConfigService(db)
    labels = []

    class LabelMgr(FakeMgr):
        def reload(self, new_config, instance_errors=None):
            labels.append(new_config.providers["claude-x"].label)

    svc.set_manager(LabelMgr())
    svc.get()
    _edit(db, lambda c: config_store.update_instance(c, "claude-x", config_store.InstanceRow(
        instance_id="claude-x", type="claude", mode="push", label="new",
    )))
    svc.get()
    assert labels == ["old", "new"]


def test_disable_triggered_when_db_becomes_unusable(tmp_path):
    db = _db(tmp_path, providers={"claude-x": _push_instance()})
    svc = ConfigService(db)
    mgr = FakeMgr()
    svc.set_manager(mgr)
    svc.get()

    # app_setting has to go too: it holds config_version, and a readable
    # unchanged version is a cache hit that never reaches load_config.
    # Losing only provider_instance without bumping the version is the DB
    # equivalent of editing config.json without touching its mtime.
    _edit(db, lambda c: c.execute("DROP TABLE provider_instance"))
    _edit(db, lambda c: c.execute("DROP TABLE app_setting"))
    svc.get()
    assert any(call[0] == "disable" for call in mgr.calls)


def test_no_reload_when_only_a_non_config_setting_changes(tmp_path):
    db = _db(tmp_path, providers={"claude-x": _push_instance()})
    svc = ConfigService(db)
    mgr = FakeMgr()
    svc.set_manager(mgr)
    svc.get()
    _edit(db, lambda c: config_store.set_setting(c, "migration_notice", "dismissed"))
    svc.get()
    assert len(mgr.calls) == 1
