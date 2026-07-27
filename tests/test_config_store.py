import sqlite3

import pytest

from ai_plan_insight import config_store


@pytest.fixture
def conn(tmp_path):
    c = sqlite3.connect(tmp_path / "usage.db")
    config_store.init_config_schema(c)
    yield c
    c.close()


def _row(instance_id="kimi-main", **over):
    kwargs = dict(
        instance_id=instance_id, type="kimi", mode="fetch", label="主账号",
        order=30, enabled=True, credentials={"api_key": "sk-abcdef1234"},
    )
    kwargs.update(over)
    return config_store.InstanceRow(**kwargs)


class TestSchema:
    def test_creates_all_config_tables(self, conn):
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        assert tables >= {"app_setting", "provider_instance", "model_alias", "admin_session"}

    def test_is_idempotent(self, conn):
        config_store.init_config_schema(conn)
        assert config_store.get_config_version(conn) == 0

    def test_creates_v2_snapshot_tables_for_delete_cleanup(self, conn):
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        assert {"provider_v2_snapshot", "provider_v2_item"} <= tables


class TestSettings:
    def test_get_missing_returns_default(self, conn):
        assert config_store.get_setting(conn, "nope") is None
        assert config_store.get_setting(conn, "nope", "x") == "x"

    def test_set_then_get(self, conn):
        config_store.set_setting(conn, "admin_password_hash", "scrypt$...")
        assert config_store.get_setting(conn, "admin_password_hash") == "scrypt$..."

    def test_set_overwrites(self, conn):
        config_store.set_setting(conn, "k", "a")
        config_store.set_setting(conn, "k", "b")
        assert config_store.get_setting(conn, "k") == "b"

    def test_set_setting_does_not_bump_config_version(self, conn):
        config_store.set_setting(conn, "migration_notice", "pending")
        assert config_store.get_config_version(conn) == 0


class TestCreate:
    def test_create_then_read_back(self, conn):
        config_store.create_instance(conn, _row())
        got = config_store.get_instance(conn, "kimi-main")
        assert got.type == "kimi"
        assert got.mode == "fetch"
        assert got.label == "主账号"
        assert got.order == 30
        assert got.enabled is True
        assert got.credentials == {"api_key": "sk-abcdef1234"}

    def test_create_bumps_config_version(self, conn):
        before = config_store.get_config_version(conn)
        config_store.create_instance(conn, _row())
        assert config_store.get_config_version(conn) == before + 1

    def test_duplicate_id_rejected(self, conn):
        config_store.create_instance(conn, _row())
        with pytest.raises(ValueError, match="already exists"):
            config_store.create_instance(conn, _row())

    def test_illegal_instance_id_rejected(self, conn):
        with pytest.raises(ValueError):
            config_store.create_instance(conn, _row(instance_id="has space"))

    def test_unknown_type_rejected(self, conn):
        with pytest.raises(ValueError):
            config_store.create_instance(conn, _row(type="nonexistent"))

    def test_missing_required_credential_rejected(self, conn):
        with pytest.raises(ValueError):
            config_store.create_instance(conn, _row(credentials={}))

    def test_type_mode_mismatch_rejected(self, conn):
        with pytest.raises(ValueError):
            config_store.create_instance(conn, _row(type="claude", mode="fetch", credentials={}))

    def test_invalid_row_leaves_no_row_behind(self, conn):
        with pytest.raises(ValueError):
            config_store.create_instance(conn, _row(type="nonexistent"))
        assert config_store.list_instances(conn) == []
        assert config_store.get_config_version(conn) == 0


class TestListAndUpdate:
    def test_list_is_ordered_by_sort_order_then_id(self, conn):
        config_store.create_instance(conn, _row("b-inst", order=10))
        config_store.create_instance(conn, _row("a-inst", order=10))
        config_store.create_instance(conn, _row("c-inst", order=1))
        assert [r.instance_id for r in config_store.list_instances(conn)] == [
            "c-inst", "a-inst", "b-inst",
        ]

    def test_include_disabled_false_filters_them_out(self, conn):
        config_store.create_instance(conn, _row("on-inst"))
        config_store.create_instance(conn, _row("off-inst", enabled=False))
        ids = [r.instance_id for r in config_store.list_instances(conn, include_disabled=False)]
        assert ids == ["on-inst"]

    def test_update_replaces_fields_and_bumps_version(self, conn):
        config_store.create_instance(conn, _row())
        before = config_store.get_config_version(conn)
        config_store.update_instance(conn, "kimi-main", _row(label="改名了", order=5))
        got = config_store.get_instance(conn, "kimi-main")
        assert got.label == "改名了"
        assert got.order == 5
        assert config_store.get_config_version(conn) == before + 1

    def test_update_unknown_instance_raises_keyerror(self, conn):
        with pytest.raises(KeyError):
            config_store.update_instance(conn, "ghost", _row("ghost"))

    def test_update_cannot_rename_instance_id(self, conn):
        config_store.create_instance(conn, _row())
        with pytest.raises(ValueError, match="instance_id"):
            config_store.update_instance(conn, "kimi-main", _row("kimi-renamed"))

    def test_get_unknown_returns_none(self, conn):
        assert config_store.get_instance(conn, "ghost") is None


class TestDelete:
    def test_delete_removes_row_and_bumps_version(self, conn):
        config_store.create_instance(conn, _row())
        before = config_store.get_config_version(conn)
        config_store.delete_instance(conn, "kimi-main")
        assert config_store.get_instance(conn, "kimi-main") is None
        assert config_store.get_config_version(conn) == before + 1

    def test_delete_unknown_raises_keyerror(self, conn):
        with pytest.raises(KeyError):
            config_store.delete_instance(conn, "ghost")

    def test_delete_cleans_up_v2_snapshot_rows(self, conn):
        config_store.create_instance(conn, _row())
        conn.execute(
            "INSERT INTO provider_v2_snapshot "
            "(instance_id, type, mode, label, recorded_at, raw_json) "
            "VALUES ('kimi-main', 'kimi', 'fetch', '主账号', '2026-07-27T00:00:00+08:00', '{}')"
        )
        conn.execute(
            "INSERT INTO provider_v2_item (instance_id, item_kind, name) "
            "VALUES ('kimi-main', 'limit', '天')"
        )
        conn.commit()

        config_store.delete_instance(conn, "kimi-main")

        assert conn.execute(
            "SELECT COUNT(*) FROM provider_v2_snapshot WHERE instance_id='kimi-main'"
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM provider_v2_item WHERE instance_id='kimi-main'"
        ).fetchone()[0] == 0
