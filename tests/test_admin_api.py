import sqlite3
from contextlib import closing
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from config_fixtures import ADMIN_PASSWORD, login, seed_admin_password, seed_config

from ai_plan_insight import admin_auth, config_store, usage_store, web
from ai_plan_insight.pricing import ModelPrice
from ai_plan_insight.usage_store import UTC8

PROVIDERS = {
    "kimi-main": {"type": "kimi", "mode": "fetch", "label": "主账号",
                  "order": 30, "api_key": "sk-abcdef1234"},
    "claude-x": {"type": "claude", "mode": "push", "label": "个人号", "order": 12},
}


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
    monkeypatch.delenv("ADMIN_PASSWORD_RESET", raising=False)
    admin_auth.rate_limiter.record_success("testclient")


@pytest.fixture
def db(tmp_path):
    path = seed_config(tmp_path / "usage.db", providers=dict(PROVIDERS),
                       push_auth_secret="s3cret-token", enforce_push_auth=True)
    seed_admin_password(path)
    web._config_path = str(tmp_path / "config.json")
    web._usage_db_path = path
    web._v2_manager = None
    web._config_service = None
    yield path
    web._v2_manager = None
    web._config_service = None


@pytest.fixture
def client(db):
    with TestClient(web.app) as c:
        yield c


@pytest.fixture
def admin(client):
    login(client)
    return client


class TestMasking:
    def test_long_secret_keeps_head_and_tail(self):
        from ai_plan_insight.admin_api import mask_secret
        assert mask_secret("sk-1234ab12") == "sk-••••ab12"

    def test_short_secret_is_fully_masked(self):
        from ai_plan_insight.admin_api import mask_secret
        assert mask_secret("short") == "••••"

    def test_empty_secret_stays_empty(self):
        from ai_plan_insight.admin_api import mask_secret
        assert mask_secret("") == ""


class TestSessionEndpoint:
    def test_public_and_reports_unauthenticated(self, client):
        data = client.get("/api/admin/session").json()
        assert data == {"authenticated": False, "password_configured": True}

    def test_reports_authenticated_after_login(self, admin):
        assert admin.get("/api/admin/session").json()["authenticated"] is True

    def test_reports_password_not_configured(self, tmp_path):
        path = seed_config(tmp_path / "usage.db")
        web._config_path = str(tmp_path / "config.json")
        web._usage_db_path = path
        web._v2_manager = None
        web._config_service = None
        with TestClient(web.app) as c:
            assert c.get("/api/admin/session").json() == {
                "authenticated": False, "password_configured": False,
            }
        web._v2_manager = None
        web._config_service = None


class TestLogin:
    def test_right_password_sets_an_httponly_cookie(self, client):
        resp = client.post("/api/admin/login", json={"password": ADMIN_PASSWORD})
        assert resp.status_code == 200
        cookie = resp.headers["set-cookie"]
        assert cookie.startswith("apiadmin=")
        assert "HttpOnly" in cookie
        # Starlette lower-cases the attribute value; it is case-insensitive.
        assert "samesite=lax" in cookie.lower()
        assert "Path=/" in cookie

    def test_wrong_password_is_401(self, client):
        assert client.post("/api/admin/login", json={"password": "nope"}).status_code == 401

    def test_lockout_after_five_failures(self, client):
        for _ in range(5):
            client.post("/api/admin/login", json={"password": "nope"})
        resp = client.post("/api/admin/login", json={"password": ADMIN_PASSWORD})
        assert resp.status_code == 429

    def test_login_is_503_without_a_configured_password(self, tmp_path):
        path = seed_config(tmp_path / "usage.db")
        web._config_path = str(tmp_path / "config.json")
        web._usage_db_path = path
        web._v2_manager = None
        web._config_service = None
        with TestClient(web.app) as c:
            resp = c.post("/api/admin/login", json={"password": "whatever"})
            assert resp.status_code == 503
        web._v2_manager = None
        web._config_service = None


class TestAuthGate:
    @pytest.mark.parametrize("method,path", [
        ("get", "/api/admin/instances"),
        ("get", "/api/admin/provider-types"),
        ("get", "/api/admin/instances/kimi-main/secrets"),
        ("post", "/api/admin/instances"),
        ("put", "/api/admin/instances/kimi-main"),
        ("delete", "/api/admin/instances/kimi-main"),
        ("post", "/api/admin/logout"),
        ("post", "/api/admin/logout-all"),
        ("post", "/api/admin/password"),
        ("get", "/api/admin/sources"),
    ])
    def test_requires_login(self, client, method, path):
        # httpx's get/delete take no body, so only send one where it is legal.
        kwargs = {"json": {}} if method in ("post", "put") else {}
        resp = getattr(client, method)(path, **kwargs)
        assert resp.status_code == 401

    def test_503_when_password_not_configured(self, tmp_path):
        path = seed_config(tmp_path / "usage.db")
        web._config_path = str(tmp_path / "config.json")
        web._usage_db_path = path
        web._v2_manager = None
        web._config_service = None
        with TestClient(web.app) as c:
            assert c.get("/api/admin/instances").status_code == 503
        web._v2_manager = None
        web._config_service = None


class TestLogout:
    def test_logout_invalidates_the_session(self, admin):
        assert admin.post("/api/admin/logout").status_code == 200
        assert admin.get("/api/admin/instances").status_code == 401

    def test_logout_all_clears_every_session(self, admin, db):
        with closing(sqlite3.connect(db)) as conn:
            admin_auth.create_session(conn)
        assert admin.post("/api/admin/logout-all").status_code == 200
        with closing(sqlite3.connect(db)) as conn:
            assert conn.execute("SELECT COUNT(*) FROM admin_session").fetchone()[0] == 0


class TestPasswordChange:
    def test_changes_the_password(self, admin, db):
        resp = admin.post("/api/admin/password", json={"old": ADMIN_PASSWORD, "new": "new-pass-99"})
        assert resp.status_code == 200
        with closing(sqlite3.connect(db)) as conn:
            stored = config_store.get_setting(conn, "admin_password_hash")
        assert admin_auth.verify_password("new-pass-99", stored)

    def test_wrong_old_password_is_403(self, admin):
        resp = admin.post("/api/admin/password", json={"old": "wrong", "new": "new-pass-99"})
        assert resp.status_code == 403

    def test_short_new_password_is_422(self, admin):
        resp = admin.post("/api/admin/password", json={"old": ADMIN_PASSWORD, "new": "short"})
        assert resp.status_code == 422

    def test_current_client_stays_logged_in(self, admin):
        admin.post("/api/admin/password", json={"old": ADMIN_PASSWORD, "new": "new-pass-99"})
        assert admin.get("/api/admin/instances").status_code == 200

    def test_other_sessions_are_dropped(self, admin, db):
        with closing(sqlite3.connect(db)) as conn:
            other = admin_auth.create_session(conn)
        admin.post("/api/admin/password", json={"old": ADMIN_PASSWORD, "new": "new-pass-99"})
        with closing(sqlite3.connect(db)) as conn:
            assert admin_auth.validate_session(conn, other) is False


class TestProviderTypes:
    def test_returns_the_full_table(self, admin):
        entries = {e["name"]: e for e in admin.get("/api/admin/provider-types").json()}
        assert "kimi" in entries and "claude" in entries
        assert entries["antigravity"]["modes"] == ["fetch", "push"]
        assert entries["kimi"]["fields"][0]["label"]


class TestListInstances:
    def test_lists_everything_including_disabled(self, admin, db):
        with closing(sqlite3.connect(db)) as conn:
            config_store.update_instance(conn, "claude-x", config_store.InstanceRow(
                instance_id="claude-x", type="claude", mode="push", label="个人号",
                order=12, enabled=False,
            ))
        data = admin.get("/api/admin/instances").json()
        by_id = {i["instance_id"]: i for i in data["instances"]}
        assert set(by_id) == {"kimi-main", "claude-x"}
        assert by_id["claude-x"]["enabled"] is False

    def test_masks_secret_credentials(self, admin):
        data = admin.get("/api/admin/instances").json()
        kimi = next(i for i in data["instances"] if i["instance_id"] == "kimi-main")
        assert kimi["credentials"]["api_key"] == "sk-••••1234"
        assert "sk-abcdef1234" not in str(data)

    def test_reports_rows_that_no_longer_validate(self, admin, db):
        with closing(sqlite3.connect(db)) as conn:
            conn.execute("UPDATE provider_instance SET type='retired' WHERE instance_id='kimi-main'")
            conn.commit()
        assert "kimi-main" in admin.get("/api/admin/instances").json()["errors"]


class TestSecretsEndpoint:
    def test_returns_plaintext(self, admin):
        resp = admin.get("/api/admin/instances/kimi-main/secrets")
        assert resp.json() == {"credentials": {"api_key": "sk-abcdef1234"}}

    def test_unknown_instance_is_404(self, admin):
        assert admin.get("/api/admin/instances/ghost/secrets").status_code == 404


class TestCreateInstance:
    def test_creates_and_bumps_version(self, admin, db):
        with closing(sqlite3.connect(db)) as conn:
            before = config_store.get_config_version(conn)
        resp = admin.post("/api/admin/instances", json={
            "instance_id": "bigmodel-new", "type": "bigmodel", "mode": "fetch",
            "label": "新账号", "order": 20, "enabled": True,
            "credentials": {"api_key": "sk-newkey5678"},
        })
        assert resp.status_code == 201
        with closing(sqlite3.connect(db)) as conn:
            assert config_store.get_instance(conn, "bigmodel-new").label == "新账号"
            assert config_store.get_config_version(conn) == before + 1

    def test_illegal_instance_id_is_422(self, admin):
        resp = admin.post("/api/admin/instances", json={
            "instance_id": "has space", "type": "claude", "mode": "push", "label": "x",
        })
        assert resp.status_code == 422

    def test_duplicate_instance_id_is_422(self, admin):
        resp = admin.post("/api/admin/instances", json={
            "instance_id": "kimi-main", "type": "claude", "mode": "push", "label": "x",
        })
        assert resp.status_code == 422

    def test_missing_required_credential_is_422(self, admin):
        resp = admin.post("/api/admin/instances", json={
            "instance_id": "kimi-two", "type": "kimi", "mode": "fetch", "label": "x",
        })
        assert resp.status_code == 422

    def test_invalid_create_leaves_nothing_behind(self, admin, db):
        admin.post("/api/admin/instances", json={
            "instance_id": "kimi-two", "type": "kimi", "mode": "fetch", "label": "x",
        })
        with closing(sqlite3.connect(db)) as conn:
            assert config_store.get_instance(conn, "kimi-two") is None


class TestUpdateInstance:
    def test_updates_structural_fields(self, admin, db):
        resp = admin.put("/api/admin/instances/kimi-main", json={
            "type": "kimi", "mode": "fetch", "label": "改名了", "order": 5,
            "enabled": False, "credentials": {"api_key": None},
        })
        assert resp.status_code == 200
        with closing(sqlite3.connect(db)) as conn:
            row = config_store.get_instance(conn, "kimi-main")
        assert row.label == "改名了"
        assert row.order == 5
        assert row.enabled is False

    def test_null_credential_keeps_the_stored_value(self, admin, db):
        admin.put("/api/admin/instances/kimi-main", json={
            "type": "kimi", "mode": "fetch", "label": "主账号", "order": 30,
            "enabled": True, "credentials": {"api_key": None},
        })
        with closing(sqlite3.connect(db)) as conn:
            assert config_store.get_instance(conn, "kimi-main").credentials == {
                "api_key": "sk-abcdef1234",
            }

    def test_new_value_replaces_the_stored_value(self, admin, db):
        admin.put("/api/admin/instances/kimi-main", json={
            "type": "kimi", "mode": "fetch", "label": "主账号", "order": 30,
            "enabled": True, "credentials": {"api_key": "sk-replaced999"},
        })
        with closing(sqlite3.connect(db)) as conn:
            assert config_store.get_instance(conn, "kimi-main").credentials == {
                "api_key": "sk-replaced999",
            }

    def test_empty_string_clears_the_credential(self, admin, db):
        # api_key is required for kimi/fetch, so clearing it must be rejected
        # and must not touch the stored row.
        resp = admin.put("/api/admin/instances/kimi-main", json={
            "type": "kimi", "mode": "fetch", "label": "主账号", "order": 30,
            "enabled": True, "credentials": {"api_key": ""},
        })
        assert resp.status_code == 422
        with closing(sqlite3.connect(db)) as conn:
            assert config_store.get_instance(conn, "kimi-main").credentials == {
                "api_key": "sk-abcdef1234",
            }

    def test_empty_string_clears_an_optional_credential(self, admin, db):
        admin.post("/api/admin/instances", json={
            "instance_id": "codex-x", "type": "codex", "mode": "fetch", "label": "中转",
            "credentials": {"api_key": "sk-codex12345", "base_url": "https://a.test"},
        })
        admin.put("/api/admin/instances/codex-x", json={
            "type": "codex", "mode": "fetch", "label": "中转", "order": 999,
            "enabled": True, "credentials": {"api_key": None, "base_url": ""},
        })
        with closing(sqlite3.connect(db)) as conn:
            assert config_store.get_instance(conn, "codex-x").credentials == {
                "api_key": "sk-codex12345",
            }

    def test_changing_type_drops_credentials_the_new_type_rejects(self, admin, db):
        resp = admin.put("/api/admin/instances/kimi-main", json={
            "type": "claude", "mode": "push", "label": "主账号", "order": 30,
            "enabled": True, "credentials": {},
        })
        assert resp.status_code == 200
        with closing(sqlite3.connect(db)) as conn:
            assert config_store.get_instance(conn, "kimi-main").credentials == {}

    def test_unknown_instance_is_404(self, admin):
        resp = admin.put("/api/admin/instances/ghost", json={
            "type": "claude", "mode": "push", "label": "x",
        })
        assert resp.status_code == 404


class TestDeleteInstance:
    def test_deletes_and_cleans_snapshots(self, admin, db):
        with closing(sqlite3.connect(db)) as conn:
            conn.execute(
                "INSERT INTO provider_v2_snapshot "
                "(instance_id, type, mode, label, recorded_at, raw_json) "
                "VALUES ('kimi-main', 'kimi', 'fetch', '主账号', '2026-07-27T00:00:00+08:00', '{}')"
            )
            conn.commit()
        assert admin.delete("/api/admin/instances/kimi-main").status_code == 200
        with closing(sqlite3.connect(db)) as conn:
            assert config_store.get_instance(conn, "kimi-main") is None
            assert conn.execute(
                "SELECT COUNT(*) FROM provider_v2_snapshot WHERE instance_id='kimi-main'"
            ).fetchone()[0] == 0

    def test_unknown_instance_is_404(self, admin):
        assert admin.delete("/api/admin/instances/ghost").status_code == 404


class TestSources:
    def test_returns_auth_status_when_logged_in(self, admin):
        assert admin.get("/api/admin/sources").status_code == 200


class TestAliases:
    def test_get_returns_stored_aliases(self, admin, db):
        with closing(sqlite3.connect(db)) as conn:
            config_store.replace_aliases(conn, {"GLM 5.2": ["glm-5.2", "glm5.2"]})
        assert admin.get("/api/admin/aliases").json() == {
            "model_aliases": {"GLM 5.2": ["glm-5.2", "glm5.2"]},
        }

    def test_put_replaces_wholesale(self, admin, db):
        admin.put("/api/admin/aliases", json={"model_aliases": {"A": ["x"]}})
        resp = admin.put("/api/admin/aliases", json={"model_aliases": {"B": ["y"]}})
        assert resp.status_code == 200
        with closing(sqlite3.connect(db)) as conn:
            assert config_store.get_aliases(conn) == {"B": ["y"]}

    def test_put_rejects_an_empty_raw_id(self, admin):
        resp = admin.put("/api/admin/aliases", json={"model_aliases": {"A": [""]}})
        assert resp.status_code == 422

    def test_requires_login(self, client):
        assert client.get("/api/admin/aliases").status_code == 401


class _StubPricing:
    """Test double for web._pricing_service (no network, deterministic)."""

    def __init__(self, table=None, ready=True):
        self._table = table or {}
        self.ready = ready

    async def ensure_ready(self, timeout=5.0):
        pass

    def price_for(self, raw_id):
        return self._table.get(raw_id.strip().lower())

    def stop_background(self):
        # the TestClient lifespan calls this on shutdown
        pass


def _seed_usage(db, tokens_by_model, days_ago=0):
    """Write one day's usage for several models in ONE upsert.

    upsert_points wipes the whole (date, source_id) slice first, so seeding
    two models in two calls would lose the first one.
    """
    date = (datetime.now(UTC8).date() - timedelta(days=days_ago)).isoformat()
    with closing(sqlite3.connect(db)) as conn:
        usage_store.upsert_points(conn, "m1", None, [
            {"date": date, "model_id": model_id,
             "input_tokens": tokens, "output_tokens": 0}
            for model_id, tokens in tokens_by_model.items()
        ])
        conn.commit()


class TestModelPrices:
    def test_requires_login(self, client):
        assert client.get("/api/admin/model-prices").status_code == 401
        assert client.put(
            "/api/admin/model-prices", json={"model_prices": {}}
        ).status_code == 401

    def test_lists_only_models_the_upstream_sources_do_not_know(
        self, admin, db, monkeypatch
    ):
        _seed_usage(db, {"grok-4.5": 100, "cursor-grok-4.5-high": 50})
        monkeypatch.setattr(web, "_pricing_service", _StubPricing({
            "grok-4.5": ModelPrice(input=1e-6, output=2e-6),
        }))
        body = admin.get("/api/admin/model-prices").json()
        assert body["window_days"] == 90
        assert body["pricing_ready"] is True
        assert body["models"] == [
            {
                "model_id": "cursor-grok-4.5-high",
                "total_tokens": 50,
                "price": None,
                "hidden": False,
            },
        ]

    def test_orders_unpriced_models_by_tokens_desc(self, admin, db, monkeypatch):
        _seed_usage(db, {"small": 5, "big": 500})
        monkeypatch.setattr(web, "_pricing_service", _StubPricing())
        models = admin.get("/api/admin/model-prices").json()["models"]
        assert [m["model_id"] for m in models] == ["big", "small"]

    def test_shows_the_stored_price_for_a_filled_row(self, admin, db, monkeypatch):
        _seed_usage(db, {"cursor-grok": 50})
        monkeypatch.setattr(web, "_pricing_service", _StubPricing())
        admin.put("/api/admin/model-prices", json={
            "model_prices": {"cursor-grok": {"input": 2, "output": 6}},
        })
        models = admin.get("/api/admin/model-prices").json()["models"]
        assert models[0]["price"] == {
            "input": 2.0, "output": 6.0, "cache_read": 0.0, "cache_write": 0.0,
        }

    def test_overridden_model_stays_listed_even_with_an_upstream_price(
        self, admin, db, monkeypatch
    ):
        """Otherwise the row vanishes the day upstream learns the model,
        while the override keeps silently applying."""
        _seed_usage(db, {"grok-4.5": 100})
        monkeypatch.setattr(web, "_pricing_service", _StubPricing({
            "grok-4.5": ModelPrice(input=1e-6, output=2e-6),
        }))
        admin.put("/api/admin/model-prices", json={
            "model_prices": {"grok-4.5": {"input": 2, "output": 6}},
        })
        models = admin.get("/api/admin/model-prices").json()["models"]
        assert [m["model_id"] for m in models] == ["grok-4.5"]

    def test_override_without_recent_usage_is_listed_last_with_zero_tokens(
        self, admin, db, monkeypatch
    ):
        _seed_usage(db, {"busy": 100})
        monkeypatch.setattr(web, "_pricing_service", _StubPricing())
        admin.put("/api/admin/model-prices", json={
            "model_prices": {"retired-model": {"input": 1, "output": 1}},
        })
        models = admin.get("/api/admin/model-prices").json()["models"]
        assert [m["model_id"] for m in models] == ["busy", "retired-model"]
        assert models[-1]["total_tokens"] == 0

    def test_usage_outside_the_window_is_not_listed(self, admin, db, monkeypatch):
        _seed_usage(db, {"ancient": 100}, days_ago=100)
        monkeypatch.setattr(web, "_pricing_service", _StubPricing())
        assert admin.get("/api/admin/model-prices").json()["models"] == []

    def test_not_ready_returns_no_models(self, admin, db, monkeypatch):
        _seed_usage(db, {"cursor-grok": 50})
        monkeypatch.setattr(web, "_pricing_service", _StubPricing(ready=False))
        body = admin.get("/api/admin/model-prices").json()
        assert body["pricing_ready"] is False
        assert body["models"] == []

    def test_missing_pricing_service_reads_as_not_ready(self, admin, db, monkeypatch):
        _seed_usage(db, {"cursor-grok": 50})
        monkeypatch.setattr(web, "_pricing_service", None)
        body = admin.get("/api/admin/model-prices").json()
        assert body["pricing_ready"] is False
        assert body["models"] == []

    def test_put_saves_and_replaces_wholesale(self, admin, db, monkeypatch):
        monkeypatch.setattr(web, "_pricing_service", _StubPricing())
        admin.put("/api/admin/model-prices", json={
            "model_prices": {"a": {"input": 1, "output": 2}},
        })
        resp = admin.put("/api/admin/model-prices", json={
            "model_prices": {"b": {"input": 3, "output": 4}},
        })
        assert resp.status_code == 200
        with closing(sqlite3.connect(db)) as conn:
            assert list(config_store.get_prices(conn)) == ["b"]

    def test_put_with_an_empty_mapping_clears_every_override(self, admin, db):
        admin.put("/api/admin/model-prices", json={
            "model_prices": {"a": {"input": 1, "output": 2}},
        })
        assert admin.put(
            "/api/admin/model-prices", json={"model_prices": {}}
        ).status_code == 200
        with closing(sqlite3.connect(db)) as conn:
            assert config_store.get_prices(conn) == {}

    def test_put_rejects_a_cache_only_row(self, admin):
        resp = admin.put("/api/admin/model-prices", json={
            "model_prices": {"a": {"cache_read": 1}},
        })
        assert resp.status_code == 422
        assert "input" in resp.json()["detail"]

    def test_put_rejects_a_negative_price(self, admin):
        assert admin.put("/api/admin/model-prices", json={
            "model_prices": {"a": {"input": -1, "output": 2}},
        }).status_code == 422

    def test_put_rejects_an_unknown_price_key(self, admin):
        assert admin.put("/api/admin/model-prices", json={
            "model_prices": {"a": {"input": 1, "output": 2, "cache-read": 1}},
        }).status_code == 422

    def test_put_hidden_requires_login(self, client):
        assert client.put(
            "/api/admin/model-prices/hidden", json={"hidden_models": ["a"]}
        ).status_code == 401

    def test_put_hidden_marks_model_hidden_and_persists(self, admin, db, monkeypatch):
        _seed_usage(db, {"cursor-free": 120, "cursor-paid": 500})
        monkeypatch.setattr(web, "_pricing_service", _StubPricing())
        resp = admin.put("/api/admin/model-prices/hidden", json={
            "hidden_models": ["Cursor-Free"],
        })
        assert resp.status_code == 200
        assert resp.json() == {"ok": True, "hidden_models": ["cursor-free"]}

        models = admin.get("/api/admin/model-prices").json()["models"]
        by_id = {m["model_id"]: m for m in models}
        assert by_id["cursor-free"]["hidden"] is True
        assert by_id["cursor-paid"]["hidden"] is False

    def test_hidden_model_without_recent_usage_stays_listed_as_hidden(
        self, admin, db, monkeypatch
    ):
        _seed_usage(db, {"busy": 100})
        monkeypatch.setattr(web, "_pricing_service", _StubPricing())
        admin.put("/api/admin/model-prices/hidden", json={
            "hidden_models": ["old-unused-model"],
        })
        models = admin.get("/api/admin/model-prices").json()["models"]
        assert models[-1] == {
            "model_id": "old-unused-model",
            "total_tokens": 0,
            "price": None,
            "hidden": True,
        }

    def test_hidden_model_stays_listed_even_if_upstream_learns_price(
        self, admin, db, monkeypatch
    ):
        _seed_usage(db, {"grok-4.5": 100})
        monkeypatch.setattr(web, "_pricing_service", _StubPricing({
            "grok-4.5": ModelPrice(input=1e-6, output=2e-6),
        }))
        admin.put("/api/admin/model-prices/hidden", json={"hidden_models": ["grok-4.5"]})
        models = admin.get("/api/admin/model-prices").json()["models"]
        assert models == [{
            "model_id": "grok-4.5", "total_tokens": 100, "price": None, "hidden": True,
        }]

    def test_saving_price_for_hidden_model_unhides_it(self, admin, db, monkeypatch):
        _seed_usage(db, {"cursor-free": 100})
        monkeypatch.setattr(web, "_pricing_service", _StubPricing())
        admin.put("/api/admin/model-prices/hidden", json={"hidden_models": ["cursor-free"]})
        admin.put("/api/admin/model-prices", json={
            "model_prices": {"cursor-free": {"input": 1, "output": 2}},
        })
        models = admin.get("/api/admin/model-prices").json()["models"]
        assert models[0]["hidden"] is False
        assert models[0]["price"]["input"] == 1.0
        with closing(sqlite3.connect(db)) as conn:
            assert config_store.get_hidden_price_models(conn) == []

    def test_put_hidden_rejects_invalid_payload(self, admin):
        assert admin.put(
            "/api/admin/model-prices/hidden", json={"hidden_models": "not-a-list"}
        ).status_code == 422
        assert admin.put(
            "/api/admin/model-prices/hidden", json={"hidden_models": [""]}
        ).status_code == 422


class TestPushAuthEndpoint:
    def test_get_masks_the_secret(self, admin):
        data = admin.get("/api/admin/push-auth").json()
        assert data["push_auth_secret"] == "s3c••••oken"
        assert data["enforce_push_auth"] is True

    def test_secret_endpoint_returns_plaintext(self, admin):
        assert admin.get("/api/admin/push-auth/secret").json() == {
            "push_auth_secret": "s3cret-token",
        }

    def test_put_writes_both_fields(self, admin, db):
        resp = admin.put("/api/admin/push-auth", json={
            "push_auth_secret": "brand-new-secret", "enforce_push_auth": False,
        })
        assert resp.status_code == 200
        with closing(sqlite3.connect(db)) as conn:
            assert config_store.get_push_auth(conn) == ("brand-new-secret", False)

    def test_requires_login(self, client):
        assert client.get("/api/admin/push-auth/secret").status_code == 401


class TestDisplaySettings:
    def test_requires_login(self, client):
        assert client.get("/api/admin/display-settings").status_code == 401
        assert client.put(
            "/api/admin/display-settings", json={"push_card_ttl_hours": 1.5}
        ).status_code == 401

    def test_get_returns_default(self, admin):
        assert admin.get("/api/admin/display-settings").json() == {
            "push_card_ttl_hours": 1.0,
        }

    def test_get_returns_stored_value(self, admin, db):
        with closing(sqlite3.connect(db)) as conn:
            config_store.set_push_card_ttl(conn, 2.5)
        assert admin.get("/api/admin/display-settings").json() == {
            "push_card_ttl_hours": 2.5,
        }

    def test_put_saves_and_get_reflects_it(self, admin, db):
        resp = admin.put("/api/admin/display-settings", json={
            "push_card_ttl_hours": 1.5,
        })
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}
        assert admin.get("/api/admin/display-settings").json() == {
            "push_card_ttl_hours": 1.5,
        }
        with closing(sqlite3.connect(db)) as conn:
            assert config_store.get_push_card_ttl(conn) == 1.5

    def test_put_below_minimum_is_422(self, admin):
        resp = admin.put("/api/admin/display-settings", json={
            "push_card_ttl_hours": 0.4,
        })
        assert resp.status_code == 422
        assert "0.5" in resp.json()["detail"]

    def test_put_rejects_non_numeric(self, admin):
        resp = admin.put("/api/admin/display-settings", json={
            "push_card_ttl_hours": "soon",
        })
        assert resp.status_code == 422


class TestExportImport:
    def test_export_contains_plaintext_credentials(self, admin):
        data = admin.get("/api/admin/export").json()
        assert data["providers"]["kimi-main"]["api_key"] == "sk-abcdef1234"
        assert data["providers"]["kimi-main"]["enabled"] is True
        assert data["push_auth_secret"] == "s3cret-token"

    def test_export_never_contains_the_admin_password(self, admin):
        assert "admin_password_hash" not in admin.get("/api/admin/export").text

    def test_import_replaces_everything(self, admin, db):
        resp = admin.post("/api/admin/import", json={
            "providers": {
                "only-one": {"type": "claude", "mode": "push", "label": "唯一", "order": 1},
            },
            "model_aliases": {"A": ["x"]},
            "push_auth_secret": "imported",
            "enforce_push_auth": False,
        })
        assert resp.status_code == 200
        with closing(sqlite3.connect(db)) as conn:
            assert [r.instance_id for r in config_store.list_instances(conn)] == ["only-one"]
            assert config_store.get_aliases(conn) == {"A": ["x"]}
            assert config_store.get_push_auth(conn) == ("imported", False)

    def test_export_and_import_carry_model_prices(self, admin, db):
        assert admin.put("/api/admin/model-prices", json={
            "model_prices": {"cursor-grok": {"input": 2, "output": 6}},
        }).status_code == 200
        assert admin.put("/api/admin/model-prices/hidden", json={
            "hidden_models": ["cursor-free"],
        }).status_code == 200
        exported = admin.get("/api/admin/export").json()
        assert exported["model_prices"]["cursor-grok"]["input"] == 2.0
        assert exported["hidden_price_models"] == ["cursor-free"]
        assert admin.post("/api/admin/import", json=exported).status_code == 200
        with closing(sqlite3.connect(db)) as conn:
            assert list(config_store.get_prices(conn)) == ["cursor-grok"]
            assert config_store.get_hidden_price_models(conn) == ["cursor-free"]

    def test_import_roundtrips_an_export(self, admin):
        exported = admin.get("/api/admin/export").json()
        assert admin.post("/api/admin/import", json=exported).status_code == 200
        assert admin.get("/api/admin/export").json() == exported

    def test_import_reports_every_error_and_writes_nothing(self, admin, db):
        resp = admin.post("/api/admin/import", json={
            "providers": {
                "bad-type": {"type": "nonexistent", "mode": "fetch", "label": "x"},
                "bad id": {"type": "claude", "mode": "push", "label": "y"},
            },
        })
        assert resp.status_code == 422
        assert set(resp.json()["detail"]["errors"]) == {"bad-type", "bad id"}
        with closing(sqlite3.connect(db)) as conn:
            assert {r.instance_id for r in config_store.list_instances(conn)} == {
                "kimi-main", "claude-x",
            }

    def test_import_requires_login(self, client):
        assert client.post("/api/admin/import", json={"providers": {}}).status_code == 401

    def test_export_and_import_carry_push_card_ttl(self, admin, db):
        assert admin.put("/api/admin/display-settings", json={
            "push_card_ttl_hours": 2.5,
        }).status_code == 200
        exported = admin.get("/api/admin/export").json()
        assert exported["push_card_ttl_hours"] == 2.5
        assert admin.post("/api/admin/import", json=exported).status_code == 200
        with closing(sqlite3.connect(db)) as conn:
            assert config_store.get_push_card_ttl(conn) == 2.5


class TestConnectionTest:
    def test_push_mode_returns_url_and_schema(self, admin):
        resp = admin.post("/api/admin/instances/test", json={
            "type": "claude", "mode": "push", "instance_id": "claude-x", "credentials": {},
        })
        data = resp.json()
        assert data["ok"] is True
        assert data["push"]["url"] == "/api/push/v2/claude-x"
        assert data["push"]["push_auth_configured"] is True
        assert "properties" in data["push"]["payload_schema"]

    def test_push_mode_without_instance_id_uses_a_placeholder(self, admin):
        data = admin.post("/api/admin/instances/test", json={
            "type": "claude", "mode": "push", "credentials": {},
        }).json()
        assert data["push"]["url"] == "/api/push/v2/{instance_id}"

    def test_fetch_mode_reports_a_summary_on_success(self, admin, monkeypatch):
        from ai_plan_insight import admin_api
        from ai_plan_insight.models import LimitDetail, UsageInfo

        async def fake_fetch(cfg):
            return UsageInfo(
                provider="Kimi", membership_level="Pro", raw_response={},
                limits=[LimitDetail(duration=1, time_unit="day", limit="100",
                                    used="10", remaining="90")],
                balances={"余额": "12.34"},
            )

        monkeypatch.setattr(admin_api, "_run_fetch_test", fake_fetch)
        data = admin.post("/api/admin/instances/test", json={
            "type": "kimi", "mode": "fetch", "credentials": {"api_key": "sk-testkey123"},
        }).json()
        assert data["ok"] is True
        assert data["summary"]["membership_level"] == "Pro"
        assert data["summary"]["balances"] == {"余额": "12.34"}
        assert len(data["summary"]["limits"]) == 1

    def test_fetch_mode_reports_the_error_on_failure(self, admin, monkeypatch):
        from ai_plan_insight import admin_api

        async def boom(cfg):
            raise RuntimeError("401 unauthorized")

        monkeypatch.setattr(admin_api, "_run_fetch_test", boom)
        data = admin.post("/api/admin/instances/test", json={
            "type": "kimi", "mode": "fetch", "credentials": {"api_key": "sk-testkey123"},
        }).json()
        assert data["ok"] is False
        assert "401 unauthorized" in data["error"]

    def test_fetch_mode_does_not_touch_the_database(self, admin, monkeypatch, db):
        from ai_plan_insight import admin_api
        from ai_plan_insight.models import UsageInfo

        async def fake_fetch(cfg):
            return UsageInfo(provider="Kimi", raw_response={})

        monkeypatch.setattr(admin_api, "_run_fetch_test", fake_fetch)
        with closing(sqlite3.connect(db)) as conn:
            before = config_store.get_config_version(conn)
        admin.post("/api/admin/instances/test", json={
            "type": "kimi", "mode": "fetch", "credentials": {"api_key": "sk-testkey123"},
        })
        with closing(sqlite3.connect(db)) as conn:
            assert config_store.get_config_version(conn) == before
            assert {r.instance_id for r in config_store.list_instances(conn)} == {
                "kimi-main", "claude-x",
            }

    def test_missing_required_credential_is_422(self, admin):
        resp = admin.post("/api/admin/instances/test", json={
            "type": "kimi", "mode": "fetch", "credentials": {},
        })
        assert resp.status_code == 422

    def test_unsupported_mode_is_422(self, admin):
        resp = admin.post("/api/admin/instances/test", json={
            "type": "claude", "mode": "fetch", "credentials": {},
        })
        assert resp.status_code == 422

    def test_requires_login(self, client):
        resp = client.post("/api/admin/instances/test", json={
            "type": "claude", "mode": "push", "credentials": {},
        })
        assert resp.status_code == 401
