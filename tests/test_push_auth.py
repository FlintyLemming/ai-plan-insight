import sqlite3

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from ai_plan_insight import usage_store, web
from ai_plan_insight.config import Config


def test_config_push_auth_defaults():
    cfg = Config(providers={})
    assert cfg.push_auth_secret == ""
    assert cfg.enforce_push_auth is False


def test_config_push_auth_parses_secret_and_enforce():
    cfg = Config(providers={}, push_auth_secret="abc", enforce_push_auth=True)
    assert cfg.push_auth_secret == "abc"
    assert cfg.enforce_push_auth is True


def test_source_auth_columns_added_on_init(tmp_path):
    db = tmp_path / "usage.db"
    usage_store.init_db(db)
    conn = sqlite3.connect(db)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(source)")}
    assert "auth_valid" in cols
    assert "last_auth_at" in cols


def test_update_and_get_source_auth_status(tmp_path):
    db = tmp_path / "usage.db"
    usage_store.init_db(db)
    conn = sqlite3.connect(db)
    usage_store.upsert_points(
        conn,
        source_id="my-agent",
        source_label="My Agent",
        points=[
            {"date": "2026-07-06", "model_id": "m1", "input_tokens": 1, "output_tokens": 1}
        ],
        reported_at="2026-07-06",
        now="2026-07-06T12:00:00+08:00",
    )
    conn.commit()
    usage_store.update_source_auth(
        conn, "my-agent", True, "2026-07-06T12:00:00+08:00"
    )
    conn.commit()
    rows = usage_store.get_source_auth_status(conn)
    assert len(rows) == 1
    assert rows[0] == {
        "source_id": "my-agent",
        "label": "My Agent",
        "last_seen": "2026-07-06T12:00:00+08:00",
        "auth_valid": True,
        "last_auth_at": "2026-07-06T12:00:00+08:00",
    }


def _cfg(secret="", enforce=False):
    return Config(providers={}, push_auth_secret=secret, enforce_push_auth=enforce)


@pytest.fixture
def auth_client(monkeypatch):
    monkeypatch.setattr(web, "load_config", lambda _=None: _cfg(secret="abc"))
    app = FastAPI()

    def _verify(request: Request):
        is_valid, reason = web._verify_push_auth(request, "src")
        return {"is_valid": is_valid, "reason": reason}

    app.post("/test")(_verify)
    return TestClient(app)


def test_verify_auth_valid_token(auth_client):
    resp = auth_client.post("/test", headers={"Authorization": "Bearer abc"})
    assert resp.json() == {"is_valid": True, "reason": None}


def test_verify_auth_missing_header(auth_client):
    resp = auth_client.post("/test")
    assert resp.json() == {"is_valid": False, "reason": "missing"}


def test_verify_auth_malformed_header(auth_client):
    resp = auth_client.post("/test", headers={"Authorization": "Basic abc"})
    assert resp.json() == {"is_valid": False, "reason": "malformed"}


def test_verify_auth_invalid_token(auth_client):
    resp = auth_client.post("/test", headers={"Authorization": "Bearer wrong"})
    assert resp.json() == {"is_valid": False, "reason": "invalid"}


def test_verify_auth_no_secret_config():
    web.load_config = lambda _=None: _cfg(secret="")
    app = FastAPI()

    def _verify(request: Request):
        is_valid, reason = web._verify_push_auth(request, "src")
        return {"is_valid": is_valid, "reason": reason}

    app.post("/test")(_verify)
    client = TestClient(app)
    resp = client.post("/test", headers={"Authorization": "Bearer abc"})
    assert resp.json() == {"is_valid": False, "reason": "invalid"}


from datetime import datetime

VALID_CLAUDE_PAYLOAD = {
    "seven_day": {"utilization": 45.2, "resets_at": "2026-07-08T12:00:00Z"},
    "five_hour": {"utilization": 12.8, "resets_at": "2026-07-01T15:00:00Z"},
}


def _setup(tmp_path, monkeypatch, secret="abc", enforce=False):
    db = tmp_path / "usage.db"
    monkeypatch.setattr(web, "_usage_db_path", db)
    usage_store.init_db(db)
    monkeypatch.setattr(
        web, "load_config", lambda _=None: Config(
            providers={}, push_auth_secret=secret, enforce_push_auth=enforce
        )
    )
    web._pushed_results.clear()
    web._pushed_at.clear()


def test_push_claude_soft_no_token_still_ok(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, secret="abc", enforce=False)
    client = TestClient(web.app)
    resp = client.post("/api/push/claude", json=VALID_CLAUDE_PAYLOAD)
    assert resp.status_code == 200


def test_push_claude_soft_invalid_token_still_ok(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, secret="abc", enforce=False)
    client = TestClient(web.app)
    resp = client.post(
        "/api/push/claude",
        json=VALID_CLAUDE_PAYLOAD,
        headers={"Authorization": "Bearer wrong"},
    )
    assert resp.status_code == 200


def test_push_claude_hard_invalid_token_returns_401(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, secret="abc", enforce=True)
    client = TestClient(web.app)
    resp = client.post(
        "/api/push/claude",
        json=VALID_CLAUDE_PAYLOAD,
        headers={"Authorization": "Bearer wrong"},
    )
    assert resp.status_code == 401


def test_push_claude_valid_token_sets_auth_valid(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, secret="abc", enforce=False)
    client = TestClient(web.app)
    client.post(
        "/api/push/claude",
        json=VALID_CLAUDE_PAYLOAD,
        headers={"Authorization": "Bearer abc"},
    )
    conn = sqlite3.connect(tmp_path / "usage.db")
    rows = usage_store.get_source_auth_status(conn)
    claude = [r for r in rows if r["source_id"] == "claude"]
    assert claude and claude[0]["auth_valid"] is True
    assert claude[0]["last_auth_at"] is not None


def test_push_claude_invalid_token_sets_auth_invalid(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, secret="abc", enforce=False)
    client = TestClient(web.app)
    client.post(
        "/api/push/claude",
        json=VALID_CLAUDE_PAYLOAD,
        headers={"Authorization": "Bearer wrong"},
    )
    conn = sqlite3.connect(tmp_path / "usage.db")
    rows = usage_store.get_source_auth_status(conn)
    claude = [r for r in rows if r["source_id"] == "claude"]
    assert claude and claude[0]["auth_valid"] is False


TODAY = datetime.now(usage_store.UTC8).date().isoformat()


def _setup_report(tmp_path, monkeypatch, secret="abc", enforce=False):
    import json
    from ai_plan_insight.config_service import ConfigService

    db = tmp_path / "usage.db"
    monkeypatch.setattr(web, "_usage_db_path", db)
    usage_store.init_db(db)
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({
        "providers": {},
        "push_auth_secret": secret,
        "enforce_push_auth": enforce,
    }))
    monkeypatch.setattr(web, "_config_service", ConfigService(cfg))


def test_report_soft_no_token_still_saves(tmp_path, monkeypatch):
    _setup_report(tmp_path, monkeypatch, secret="abc", enforce=False)
    client = TestClient(web.app)
    resp = client.post("/api/usage/report", json={
        "source_id": "macbook-flinty",
        "points": [{"date": TODAY, "model_id": "glm-5.2", "input_tokens": 10, "output_tokens": 5}],
    })
    assert resp.status_code == 200
    assert resp.json()["upserted"] == 1


def test_report_soft_invalid_token_still_saves(tmp_path, monkeypatch):
    _setup_report(tmp_path, monkeypatch, secret="abc", enforce=False)
    client = TestClient(web.app)
    resp = client.post("/api/usage/report", json={
        "source_id": "macbook-flinty",
        "points": [{"date": TODAY, "model_id": "glm-5.2", "input_tokens": 10, "output_tokens": 5}],
    }, headers={"Authorization": "Bearer wrong"})
    assert resp.status_code == 200


def test_report_hard_invalid_token_returns_401(tmp_path, monkeypatch):
    _setup_report(tmp_path, monkeypatch, secret="abc", enforce=True)
    client = TestClient(web.app)
    resp = client.post("/api/usage/report", json={
        "source_id": "macbook-flinty",
        "points": [{"date": TODAY, "model_id": "glm-5.2", "input_tokens": 10, "output_tokens": 5}],
    }, headers={"Authorization": "Bearer wrong"})
    assert resp.status_code == 401
    conn = sqlite3.connect(tmp_path / "usage.db")
    rows = usage_store.query_timeseries(conn, 7, {})
    assert len(rows) == 0


def test_report_valid_token_sets_auth_valid(tmp_path, monkeypatch):
    _setup_report(tmp_path, monkeypatch, secret="abc", enforce=False)
    client = TestClient(web.app)
    resp = client.post("/api/usage/report", json={
        "source_id": "macbook-flinty",
        "source_label": "MacBook Pro",
        "points": [{"date": TODAY, "model_id": "glm-5.2", "input_tokens": 10, "output_tokens": 5}],
    }, headers={"Authorization": "Bearer abc"})
    assert resp.status_code == 200
    conn = sqlite3.connect(tmp_path / "usage.db")
    rows = usage_store.get_source_auth_status(conn)
    assert rows[0]["source_id"] == "macbook-flinty"
    assert rows[0]["auth_valid"] is True


def test_report_invalid_token_sets_auth_invalid(tmp_path, monkeypatch):
    _setup_report(tmp_path, monkeypatch, secret="abc", enforce=False)
    client = TestClient(web.app)
    resp = client.post("/api/usage/report", json={
        "source_id": "macbook-flinty",
        "points": [{"date": TODAY, "model_id": "glm-5.2", "input_tokens": 10, "output_tokens": 5}],
    }, headers={"Authorization": "Bearer wrong"})
    assert resp.status_code == 200
    conn = sqlite3.connect(tmp_path / "usage.db")
    rows = usage_store.get_source_auth_status(conn)
    assert rows[0]["auth_valid"] is False


def test_admin_sources_lists_auth_status(tmp_path, monkeypatch):
    db = tmp_path / "usage.db"
    monkeypatch.setattr(web, "_usage_db_path", db)
    usage_store.init_db(db)
    monkeypatch.setattr(web, "load_config", lambda _=None: Config(providers={}))
    conn = sqlite3.connect(db)
    usage_store.upsert_points(
        conn,
        source_id="my-agent",
        source_label="My Agent",
        points=[{"date": "2026-07-06", "model_id": "m1", "input_tokens": 1, "output_tokens": 1}],
        reported_at="2026-07-06",
        now="2026-07-06T12:00:00+08:00",
    )
    usage_store.update_source_auth(conn, "my-agent", True, "2026-07-06T12:00:00+08:00")
    conn.commit()

    client = TestClient(web.app)
    resp = client.get("/api/admin/sources")
    assert resp.status_code == 200
    assert resp.json() == [{
        "source_id": "my-agent",
        "label": "My Agent",
        "last_seen": "2026-07-06T12:00:00+08:00",
        "auth_valid": True,
        "last_auth_at": "2026-07-06T12:00:00+08:00",
    }]
