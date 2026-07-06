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
