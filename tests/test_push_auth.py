import sqlite3
from datetime import datetime
from fastapi.testclient import TestClient

from ai_plan_insight import usage_store, web
from ai_plan_insight.instance_config import V2Config


def test_v2config_push_auth_defaults():
    cfg = V2Config(providers={})
    assert cfg.push_auth_secret == ""
    assert cfg.enforce_push_auth is False


def test_source_auth_columns_added_on_init(tmp_path):
    db = tmp_path / "usage.db"
    usage_store.init_db(db)
    conn = sqlite3.connect(db)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(source)")}
    assert "auth_valid" in cols
    assert "last_auth_at" in cols


def _client(tmp_path, monkeypatch, secret="abc", enforce=False, providers=None):
    from config_fixtures import seed_config
    db = seed_config(
        tmp_path / "usage.db",
        providers=providers or {},
        push_auth_secret=secret,
        enforce_push_auth=enforce,
    )
    web._config_path = str(tmp_path / "config.json")
    web._usage_db_path = db
    web._v2_manager = None
    web._config_service = None
    return TestClient(web.app)


TODAY = datetime.now(usage_store.UTC8).date().isoformat()


def test_report_soft_no_token_still_saves(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch, secret="abc", enforce=False) as c:
        resp = c.post("/api/usage/report", json={
            "source_id": "macbook-flinty",
            "points": [{"date": TODAY, "model_id": "glm-5.2", "input_tokens": 10, "output_tokens": 5}],
        })
        assert resp.status_code == 200
        assert resp.json()["upserted"] == 1
    web._v2_manager = None
    web._config_service = None


def test_report_hard_invalid_token_returns_401(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch, secret="abc", enforce=True) as c:
        resp = c.post("/api/usage/report", json={
            "source_id": "macbook-flinty",
            "points": [{"date": TODAY, "model_id": "glm-5.2", "input_tokens": 10, "output_tokens": 5}],
        }, headers={"Authorization": "Bearer wrong"})
        assert resp.status_code == 401
    web._v2_manager = None
    web._config_service = None


def test_report_valid_token_sets_auth_valid(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch, secret="abc", enforce=False) as c:
        c.post("/api/usage/report", json={
            "source_id": "macbook-flinty", "source_label": "MacBook Pro",
            "points": [{"date": TODAY, "model_id": "glm-5.2", "input_tokens": 10, "output_tokens": 5}],
        }, headers={"Authorization": "Bearer abc"})
        conn = sqlite3.connect(tmp_path / "usage.db")
        rows = usage_store.get_source_auth_status(conn)
        assert rows[0]["source_id"] == "macbook-flinty"
        assert rows[0]["auth_valid"] is True
    web._v2_manager = None
    web._config_service = None


def test_report_valid_x_report_key_fallback_sets_auth_valid(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch, secret="abc", enforce=True) as c:
        resp = c.post("/api/usage/report", json={
            "source_id": "macbook-flinty", "source_label": "MacBook Pro",
            "points": [{"date": TODAY, "model_id": "glm-5.2", "input_tokens": 10, "output_tokens": 5}],
        }, headers={"X-Report-Key": "abc"})
        assert resp.status_code == 200
        assert resp.json()["upserted"] == 1
        conn = sqlite3.connect(tmp_path / "usage.db")
        rows = usage_store.get_source_auth_status(conn)
        assert rows[0]["source_id"] == "macbook-flinty"
        assert rows[0]["auth_valid"] is True
    web._v2_manager = None
    web._config_service = None


def test_report_x_report_key_case_insensitive(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch, secret="abc", enforce=True) as c:
        resp = c.post("/api/usage/report", json={
            "source_id": "macbook-flinty",
            "points": [{"date": TODAY, "model_id": "glm-5.2", "input_tokens": 10, "output_tokens": 5}],
        }, headers={"x-report-key": " abc "})
        assert resp.status_code == 200
    web._v2_manager = None
    web._config_service = None


def test_report_hard_invalid_x_report_key_returns_401(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch, secret="abc", enforce=True) as c:
        resp = c.post("/api/usage/report", json={
            "source_id": "macbook-flinty",
            "points": [{"date": TODAY, "model_id": "glm-5.2", "input_tokens": 10, "output_tokens": 5}],
        }, headers={"X-Report-Key": "wrong"})
        assert resp.status_code == 401
    web._v2_manager = None
    web._config_service = None


def test_report_hard_empty_x_report_key_returns_401(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch, secret="abc", enforce=True) as c:
        resp = c.post("/api/usage/report", json={
            "source_id": "macbook-flinty",
            "points": [{"date": TODAY, "model_id": "glm-5.2", "input_tokens": 10, "output_tokens": 5}],
        }, headers={"X-Report-Key": "   "})
        assert resp.status_code == 401
    web._v2_manager = None
    web._config_service = None


def test_report_authorization_takes_precedence_over_x_report_key(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch, secret="abc", enforce=True) as c:
        resp = c.post("/api/usage/report", json={
            "source_id": "macbook-flinty",
            "points": [{"date": TODAY, "model_id": "glm-5.2", "input_tokens": 10, "output_tokens": 5}],
        }, headers={"Authorization": "Bearer wrong", "X-Report-Key": "abc"})
        assert resp.status_code == 401
    web._v2_manager = None
    web._config_service = None


def test_report_x_report_key_rejected_when_secret_unconfigured(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch, secret="", enforce=True) as c:
        resp = c.post("/api/usage/report", json={
            "source_id": "macbook-flinty",
            "points": [{"date": TODAY, "model_id": "glm-5.2", "input_tokens": 10, "output_tokens": 5}],
        }, headers={"X-Report-Key": "abc"})
        assert resp.status_code == 401
    web._v2_manager = None
    web._config_service = None


def test_push_v2_x_report_key_fallback(tmp_path, monkeypatch):
    providers = {
        "claude-main": {"type": "claude", "mode": "push", "label": "主力", "order": 1},
    }
    payload = {
        "seven_day": {"utilization": 45.2, "resets_at": "2026-07-08T12:00:00Z"},
        "five_hour": {"utilization": 12.8, "resets_at": "2026-07-01T15:00:00Z"},
    }
    with _client(tmp_path, monkeypatch, secret="abc", enforce=True, providers=providers) as c:
        ok = c.post("/api/push/v2/claude-main", json=payload, headers={"X-Report-Key": "abc"})
        assert ok.status_code == 200
        conn = sqlite3.connect(tmp_path / "usage.db")
        rows = usage_store.get_source_auth_status(conn)
        claude = [r for r in rows if r["source_id"] == "claude-main"]
        assert claude and claude[0]["auth_valid"] is True

        bad = c.post("/api/push/v2/claude-main", json=payload, headers={"X-Report-Key": "wrong"})
        assert bad.status_code == 401
    web._v2_manager = None
    web._config_service = None


def test_admin_sources_requires_login(tmp_path):
    from config_fixtures import seed_admin_password, seed_config
    db = seed_config(tmp_path / "usage.db")
    seed_admin_password(db)
    web._config_path = str(tmp_path / "config.json")
    web._usage_db_path = db
    client = TestClient(web.app)
    assert client.get("/api/admin/sources").status_code == 401


def test_admin_sources_lists_auth_status(tmp_path, monkeypatch):
    from config_fixtures import login, seed_admin_password, seed_config
    db = seed_config(tmp_path / "usage.db")
    seed_admin_password(db)
    web._config_path = str(tmp_path / "config.json")
    web._usage_db_path = db
    conn = sqlite3.connect(db)
    usage_store.upsert_points(
        conn, source_id="my-agent", source_label="My Agent",
        points=[{"date": "2026-07-06", "model_id": "m1", "input_tokens": 1, "output_tokens": 1}],
        reported_at="2026-07-06", now="2026-07-06T12:00:00+08:00",
    )
    usage_store.update_source_auth(conn, "my-agent", True, "2026-07-06T12:00:00+08:00")
    conn.commit()
    client = TestClient(web.app)
    login(client)
    resp = client.get("/api/admin/sources")
    assert resp.status_code == 200
    assert resp.json() == [{
        "source_id": "my-agent", "label": "My Agent",
        "last_seen": "2026-07-06T12:00:00+08:00",
        "auth_valid": True, "last_auth_at": "2026-07-06T12:00:00+08:00",
    }]
