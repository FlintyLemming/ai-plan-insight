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


class TestAliases:
    def test_empty_initially(self, conn):
        assert config_store.get_aliases(conn) == {}

    def test_replace_then_read_back_preserves_order(self, conn):
        config_store.replace_aliases(conn, {"GLM 5.2": ["glm-5.2", "glm5.2"], "GPT 5.2": ["gpt-5.2"]})
        assert config_store.get_aliases(conn) == {
            "GLM 5.2": ["glm-5.2", "glm5.2"], "GPT 5.2": ["gpt-5.2"],
        }

    def test_replace_is_wholesale(self, conn):
        config_store.replace_aliases(conn, {"A": ["x"]})
        config_store.replace_aliases(conn, {"B": ["y"]})
        assert config_store.get_aliases(conn) == {"B": ["y"]}

    def test_duplicate_raw_id_last_definition_wins(self, conn):
        config_store.replace_aliases(conn, {"A": ["x"], "B": ["x"]})
        assert config_store.get_aliases(conn) == {"B": ["x"]}

    def test_replace_bumps_config_version(self, conn):
        before = config_store.get_config_version(conn)
        config_store.replace_aliases(conn, {"A": ["x"]})
        assert config_store.get_config_version(conn) == before + 1

    def test_empty_label_rejected(self, conn):
        with pytest.raises(ValueError):
            config_store.replace_aliases(conn, {"  ": ["x"]})

    def test_non_list_raw_ids_rejected(self, conn):
        with pytest.raises(ValueError):
            config_store.replace_aliases(conn, {"A": "x"})

    def test_empty_raw_id_rejected(self, conn):
        with pytest.raises(ValueError):
            config_store.replace_aliases(conn, {"A": [""]})


class TestPrices:
    def test_table_is_created(self, conn):
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        assert "model_price" in tables

    def test_empty_initially(self, conn):
        assert config_store.get_prices(conn) == {}

    def test_replace_then_read_back(self, conn):
        config_store.replace_prices(conn, {
            "cursor-grok-4.5-high": {"input": 2, "output": 6, "cache_read": 0.5},
        })
        assert config_store.get_prices(conn) == {
            "cursor-grok-4.5-high": {
                "input": 2.0, "output": 6.0, "cache_read": 0.5, "cache_write": 0.0,
            },
        }

    def test_model_id_is_lowercased(self, conn):
        config_store.replace_prices(conn, {"Cursor-GROK": {"input": 1, "output": 2}})
        assert list(config_store.get_prices(conn)) == ["cursor-grok"]

    def test_read_back_is_sorted_by_model_id(self, conn):
        config_store.replace_prices(conn, {
            "zeta": {"input": 1, "output": 1},
            "alpha": {"input": 1, "output": 1},
        })
        assert list(config_store.get_prices(conn)) == ["alpha", "zeta"]

    def test_replace_is_wholesale(self, conn):
        config_store.replace_prices(conn, {"a": {"input": 1, "output": 2}})
        config_store.replace_prices(conn, {"b": {"input": 3, "output": 4}})
        assert list(config_store.get_prices(conn)) == ["b"]

    def test_replace_bumps_config_version(self, conn):
        before = config_store.get_config_version(conn)
        config_store.replace_prices(conn, {"a": {"input": 1, "output": 2}})
        assert config_store.get_config_version(conn) == before + 1

    def test_non_object_mapping_rejected(self, conn):
        with pytest.raises(ValueError):
            config_store.validate_prices([])

    def test_empty_model_id_rejected(self, conn):
        with pytest.raises(ValueError):
            config_store.validate_prices({"  ": {"input": 1, "output": 2}})

    def test_non_object_value_rejected(self, conn):
        with pytest.raises(ValueError):
            config_store.validate_prices({"a": 5})

    def test_unknown_key_rejected(self, conn):
        # guards against typos like "cache-read"
        with pytest.raises(ValueError):
            config_store.validate_prices({"a": {"input": 1, "output": 2, "cache-read": 1}})

    def test_negative_value_rejected(self, conn):
        with pytest.raises(ValueError):
            config_store.validate_prices({"a": {"input": -1, "output": 2}})

    def test_non_numeric_value_rejected(self, conn):
        with pytest.raises(ValueError):
            config_store.validate_prices({"a": {"input": "2", "output": 2}})

    def test_bool_value_rejected(self, conn):
        # True would otherwise silently become 1.0
        with pytest.raises(ValueError):
            config_store.validate_prices({"a": {"input": True, "output": 2}})

    def test_non_finite_value_rejected(self, conn):
        with pytest.raises(ValueError):
            config_store.validate_prices({"a": {"input": float("inf"), "output": 2}})

    def test_input_and_output_both_zero_rejected(self, conn):
        # clearing a row means dropping it from the mapping, not sending zeros
        with pytest.raises(ValueError):
            config_store.validate_prices({"a": {"cache_read": 1}})

    def test_missing_keys_default_to_zero(self, conn):
        config_store.replace_prices(conn, {"a": {"output": 3}})
        assert config_store.get_prices(conn)["a"] == {
            "input": 0.0, "output": 3.0, "cache_read": 0.0, "cache_write": 0.0,
        }

    def test_invalid_mapping_writes_nothing(self, conn):
        config_store.replace_prices(conn, {"a": {"input": 1, "output": 2}})
        with pytest.raises(ValueError):
            config_store.replace_prices(conn, {"b": {"input": -1, "output": 2}})
        assert list(config_store.get_prices(conn)) == ["a"]


class TestPushAuth:
    def test_defaults(self, conn):
        assert config_store.get_push_auth(conn) == ("", False)

    def test_set_then_get(self, conn):
        config_store.set_push_auth(conn, "top-secret", True)
        assert config_store.get_push_auth(conn) == ("top-secret", True)

    def test_set_bumps_config_version(self, conn):
        before = config_store.get_config_version(conn)
        config_store.set_push_auth(conn, "s", False)
        assert config_store.get_config_version(conn) == before + 1


class TestPushCardTtl:
    def test_default_when_unset(self, conn):
        assert config_store.get_push_card_ttl(conn) == 1.0

    def test_set_then_get(self, conn):
        config_store.set_push_card_ttl(conn, 1.5)
        assert config_store.get_push_card_ttl(conn) == 1.5

    def test_set_bumps_config_version(self, conn):
        before = config_store.get_config_version(conn)
        config_store.set_push_card_ttl(conn, 2.0)
        assert config_store.get_config_version(conn) == before + 1

    def test_set_accepts_int(self, conn):
        config_store.set_push_card_ttl(conn, 2)
        assert config_store.get_push_card_ttl(conn) == 2.0

    def test_set_accepts_half_hour_minimum(self, conn):
        config_store.set_push_card_ttl(conn, 0.5)
        assert config_store.get_push_card_ttl(conn) == 0.5


class TestValidatePushCardTtl:
    def test_returns_float_for_int(self):
        assert config_store.validate_push_card_ttl(2) == 2.0

    def test_returns_float_for_float(self):
        assert config_store.validate_push_card_ttl(1.5) == 1.5

    def test_below_minimum_rejected(self):
        with pytest.raises(ValueError):
            config_store.validate_push_card_ttl(0.4)

    def test_zero_rejected(self):
        with pytest.raises(ValueError):
            config_store.validate_push_card_ttl(0)

    def test_negative_rejected(self):
        with pytest.raises(ValueError):
            config_store.validate_push_card_ttl(-1)

    def test_bool_rejected(self):
        # True must not silently become 1.0
        with pytest.raises(ValueError):
            config_store.validate_push_card_ttl(True)
        with pytest.raises(ValueError):
            config_store.validate_push_card_ttl(False)

    def test_string_rejected(self):
        with pytest.raises(ValueError):
            config_store.validate_push_card_ttl("1.5")

    def test_nan_rejected(self):
        with pytest.raises(ValueError):
            config_store.validate_push_card_ttl(float("nan"))

    def test_inf_rejected(self):
        with pytest.raises(ValueError):
            config_store.validate_push_card_ttl(float("inf"))

    def test_minimum_boundary_accepted(self):
        assert config_store.validate_push_card_ttl(0.5) == 0.5


def test_get_push_card_ttl_falls_back_on_garbage_value(conn):
    # The read path runs inside load_config and must never raise on one bad row.
    config_store._set_setting(conn, "push_card_ttl_hours", "not-a-number")
    conn.commit()
    assert config_store.get_push_card_ttl(conn) == 1.0


class TestIsConfigEmpty:
    def test_true_on_fresh_db(self, conn):
        assert config_store.is_config_empty(conn) is True

    def test_false_with_an_instance(self, conn):
        config_store.create_instance(conn, _row())
        assert config_store.is_config_empty(conn) is False

    def test_false_with_an_alias(self, conn):
        config_store.replace_aliases(conn, {"A": ["x"]})
        assert config_store.is_config_empty(conn) is False

    def test_false_with_a_push_secret(self, conn):
        config_store.set_push_auth(conn, "s", False)
        assert config_store.is_config_empty(conn) is False

    def test_true_when_secret_is_empty_string(self, conn):
        config_store.set_push_auth(conn, "", False)
        assert config_store.is_config_empty(conn) is True

    def test_prices_alone_still_count_as_empty(self, conn):
        # is_config_empty decides whether to run the config.json migration;
        # prices never came from config.json, so they must not block it.
        config_store.replace_prices(conn, {"a": {"input": 1, "output": 2}})
        assert config_store.is_config_empty(conn) is True

    def test_ttl_alone_still_counts_as_empty(self, conn):
        # is_config_empty decides whether to run the config.json migration;
        # the TTL never came from config.json, so it must not block it.
        config_store.set_push_card_ttl(conn, 2.0)
        assert config_store.is_config_empty(conn) is True


class TestLoadConfig:
    def test_empty_db_loads_empty_config(self, conn):
        result = config_store.load_config(conn)
        assert result.config_error is None
        assert result.config.providers == {}
        assert result.instance_errors == {}

    def test_loads_enabled_instances_only(self, conn):
        config_store.create_instance(conn, _row("on-inst"))
        config_store.create_instance(conn, _row("off-inst", enabled=False))
        result = config_store.load_config(conn)
        assert set(result.config.providers) == {"on-inst"}

    def test_maps_sort_order_to_order(self, conn):
        config_store.create_instance(conn, _row(order=7))
        assert config_store.load_config(conn).config.providers["kimi-main"].order == 7

    def test_carries_aliases_and_push_auth(self, conn):
        config_store.replace_aliases(conn, {"GLM 5.2": ["glm-5.2"]})
        config_store.set_push_auth(conn, "s3cret", True)
        cfg = config_store.load_config(conn).config
        assert cfg.model_aliases == {"GLM 5.2": ["glm-5.2"]}
        assert cfg.alias_lookup == {"glm-5.2": "GLM 5.2"}
        assert cfg.push_auth_secret == "s3cret"
        assert cfg.enforce_push_auth is True

    def test_row_that_became_illegal_goes_to_instance_errors(self, conn):
        config_store.create_instance(conn, _row())
        # Simulate a code upgrade that removed this type, bypassing validation.
        conn.execute("UPDATE provider_instance SET type = 'retired_type'")
        conn.commit()
        result = config_store.load_config(conn)
        assert result.config.providers == {}
        assert "kimi-main" in result.instance_errors
        assert result.config_error is None

    def test_other_instances_still_load_around_a_bad_row(self, conn):
        config_store.create_instance(conn, _row("good-inst"))
        config_store.create_instance(conn, _row("bad-inst"))
        conn.execute("UPDATE provider_instance SET type = 'retired_type' WHERE instance_id = 'bad-inst'")
        conn.commit()
        result = config_store.load_config(conn)
        assert set(result.config.providers) == {"good-inst"}
        assert set(result.instance_errors) == {"bad-inst"}

    def test_unreadable_db_yields_config_error(self, tmp_path):
        c = sqlite3.connect(tmp_path / "empty.db")  # no schema at all
        try:
            result = config_store.load_config(c)
        finally:
            c.close()
        assert result.config_error is not None
        assert result.config.providers == {}

    def test_carries_model_prices(self, conn):
        config_store.replace_prices(conn, {"cursor-grok": {"input": 2, "output": 6}})
        cfg = config_store.load_config(conn).config
        assert cfg.model_prices["cursor-grok"].input == 2.0
        assert cfg.model_prices["cursor-grok"].output == 6.0

    def test_empty_db_has_no_model_prices(self, conn):
        assert config_store.load_config(conn).config.model_prices == {}

    def test_carries_push_card_ttl(self, conn):
        config_store.set_push_card_ttl(conn, 2.5)
        cfg = config_store.load_config(conn).config
        assert cfg.push_card_ttl_hours == 2.5

    def test_empty_db_uses_default_ttl(self, conn):
        assert config_store.load_config(conn).config.push_card_ttl_hours == 1.0


class TestExportImport:
    def test_export_shape(self, conn):
        config_store.create_instance(conn, _row())
        config_store.replace_aliases(conn, {"GLM 5.2": ["glm-5.2"]})
        config_store.set_push_auth(conn, "s3cret", True)
        assert config_store.export_config(conn) == {
            "providers": {
                "kimi-main": {
                    "type": "kimi", "mode": "fetch", "label": "主账号",
                    "order": 30, "enabled": True, "api_key": "sk-abcdef1234",
                },
            },
            "model_aliases": {"GLM 5.2": ["glm-5.2"]},
            "model_prices": {},
            "push_auth_secret": "s3cret",
            "enforce_push_auth": True,
        }

    def test_export_includes_disabled_instances(self, conn):
        config_store.create_instance(conn, _row("off-inst", enabled=False))
        assert config_store.export_config(conn)["providers"]["off-inst"]["enabled"] is False

    def test_roundtrip(self, conn):
        config_store.create_instance(conn, _row())
        config_store.create_instance(conn, _row("claude-x", type="claude", mode="push",
                                                label="个人号", order=12, credentials={}))
        config_store.replace_aliases(conn, {"GLM 5.2": ["glm-5.2"]})
        config_store.set_push_auth(conn, "s3cret", True)
        exported = config_store.export_config(conn)

        config_store.import_config(conn, exported)
        assert config_store.export_config(conn) == exported

    def test_import_is_wholesale_replacement(self, conn):
        config_store.create_instance(conn, _row("old-inst"))
        config_store.import_config(conn, {
            "providers": {
                "new-inst": {"type": "claude", "mode": "push", "label": "新的", "order": 1},
            },
        })
        assert [r.instance_id for r in config_store.list_instances(conn)] == ["new-inst"]

    def test_import_bumps_config_version_once(self, conn):
        before = config_store.get_config_version(conn)
        config_store.import_config(conn, {"providers": {}})
        assert config_store.get_config_version(conn) == before + 1

    def test_import_collects_every_error_and_writes_nothing(self, conn):
        config_store.create_instance(conn, _row("survivor"))
        with pytest.raises(config_store.ImportValidationError) as exc:
            config_store.import_config(conn, {
                "providers": {
                    "bad-type": {"type": "nonexistent", "mode": "fetch", "label": "x"},
                    "bad id": {"type": "claude", "mode": "push", "label": "y"},
                },
            })
        assert set(exc.value.errors) == {"bad-type", "bad id"}
        assert [r.instance_id for r in config_store.list_instances(conn)] == ["survivor"]

    def test_import_rejects_non_object_providers(self, conn):
        with pytest.raises(config_store.ImportValidationError):
            config_store.import_config(conn, {"providers": []})

    def test_import_defaults_enabled_to_true(self, conn):
        config_store.import_config(conn, {
            "providers": {"claude-x": {"type": "claude", "mode": "push", "label": "x"}},
        })
        assert config_store.get_instance(conn, "claude-x").enabled is True

    def test_import_honours_enabled_false(self, conn):
        config_store.import_config(conn, {
            "providers": {
                "claude-x": {"type": "claude", "mode": "push", "label": "x", "enabled": False},
            },
        })
        assert config_store.get_instance(conn, "claude-x").enabled is False

    def test_export_includes_model_prices(self, conn):
        config_store.replace_prices(conn, {"cursor-grok": {"input": 2, "output": 6}})
        assert config_store.export_config(conn)["model_prices"] == {
            "cursor-grok": {
                "input": 2.0, "output": 6.0, "cache_read": 0.0, "cache_write": 0.0,
            },
        }

    def test_price_roundtrip(self, conn):
        config_store.replace_prices(conn, {"cursor-grok": {"input": 2, "output": 6}})
        exported = config_store.export_config(conn)
        config_store.import_config(conn, exported)
        assert config_store.export_config(conn) == exported

    def test_import_without_model_prices_clears_them(self, conn):
        # wholesale replacement: an old backup drops the price table, same as
        # it drops aliases
        config_store.replace_prices(conn, {"cursor-grok": {"input": 2, "output": 6}})
        config_store.import_config(conn, {"providers": {}})
        assert config_store.get_prices(conn) == {}

    def test_import_rejects_invalid_prices_and_writes_nothing(self, conn):
        config_store.replace_prices(conn, {"keep-me": {"input": 1, "output": 1}})
        with pytest.raises(config_store.ImportValidationError) as exc:
            config_store.import_config(conn, {
                "providers": {}, "model_prices": {"bad": {"input": -1, "output": 1}},
            })
        assert "_model_prices" in exc.value.errors
        assert list(config_store.get_prices(conn)) == ["keep-me"]

    def test_parse_import_payload_returns_five_values(self):
        rows, aliases, prices, secret, enforce = config_store.parse_import_payload({
            "providers": {}, "model_prices": {"a": {"input": 1, "output": 2}},
        })
        assert prices == {"a": {"input": 1, "output": 2}}
