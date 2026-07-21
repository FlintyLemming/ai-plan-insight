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
    import json
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({
        "providers": providers or {},
        "push_auth_secret": secret,
        "enforce_push_auth": enforce,
    }))
    web._config_path = str(cfg)
    web._usage_db_path = tmp_path / "usage.db"
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


def test_admin_sources_lists_auth_status(tmp_path, monkeypatch):
    db = tmp_path / "usage.db"
    web._usage_db_path = db
    usage_store.init_db(db)
    conn = sqlite3.connect(db)
    usage_store.upsert_points(
        conn, source_id="my-agent", source_label="My Agent",
        points=[{"date": "2026-07-06", "model_id": "m1", "input_tokens": 1, "output_tokens": 1}],
        reported_at="2026-07-06", now="2026-07-06T12:00:00+08:00",
    )
    usage_store.update_source_auth(conn, "my-agent", True, "2026-07-06T12:00:00+08:00")
    conn.commit()
    client = TestClient(web.app)
    resp = client.get("/api/admin/sources")
    assert resp.status_code == 200
    assert resp.json() == [{
        "source_id": "my-agent", "label": "My Agent",
        "last_seen": "2026-07-06T12:00:00+08:00",
        "auth_valid": True, "last_auth_at": "2026-07-06T12:00:00+08:00",
    }]
