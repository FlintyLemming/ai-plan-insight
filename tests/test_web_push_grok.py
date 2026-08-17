import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from fastapi.testclient import TestClient

import ai_plan_insight.web as web
from ai_plan_insight import usage_store

VALID_PAYLOAD = {
    "weekly": {"utilization": 45.2, "resets_at": "2026-07-10T04:01:09Z"},
    "plan": "SuperGrok",
}


def _client(tmp_path, monkeypatch, secret="abc", enforce=False):
    from config_fixtures import seed_config
    db = seed_config(
        tmp_path / "usage.db",
        providers={
            "grok-main": {"type": "grok", "mode": "push", "label": "主力", "order": 1},
        },
        push_auth_secret=secret,
        enforce_push_auth=enforce,
    )
    web._config_path = str(tmp_path / "config.json")
    web._usage_db_path = db
    web._v2_manager = None
    web._config_service = None
    return TestClient(web.app)


def test_push_grok_returns_ok(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch, secret="abc", enforce=True) as c:
        resp = c.post("/api/push/v2/grok-main", json=VALID_PAYLOAD,
                      headers={"Authorization": "Bearer abc"})
        assert resp.status_code == 200
    web._v2_manager = None
    web._config_service = None


def test_usage_v2_returns_grok_limit_after_push(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch, secret="abc", enforce=True) as c:
        c.post("/api/push/v2/grok-main", json=VALID_PAYLOAD,
               headers={"Authorization": "Bearer abc"})
        data = c.get("/api/usage/v2").json()
        grok = [d for d in data if d["instance_id"] == "grok-main"]
        assert len(grok) == 1
        limits = grok[0]["limits"]
        assert len(limits) == 1
        assert limits[0]["time_unit"] == "天"
        assert limits[0]["used"] == "45"
        assert limits[0]["remaining"] == "54"
    web._v2_manager = None
    web._config_service = None


def test_push_grok_missing_weekly_returns_422(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch, secret="abc", enforce=True) as c:
        resp = c.post("/api/push/v2/grok-main", json={"plan": "SuperGrok"},
                      headers={"Authorization": "Bearer abc"})
        assert resp.status_code == 422
    web._v2_manager = None
    web._config_service = None


def test_push_grok_ignores_legacy_monthly(tmp_path, monkeypatch):
    payload = {
        **VALID_PAYLOAD,
        "monthly": {"used": 448, "limit": 15000, "resets_at": "2026-08-01T00:00:00+00:00"},
    }
    with _client(tmp_path, monkeypatch, secret="abc", enforce=True) as c:
        resp = c.post("/api/push/v2/grok-main", json=payload,
                      headers={"Authorization": "Bearer abc"})
        assert resp.status_code == 200
        limits = c.get("/api/usage/v2").json()[0]["limits"]
        assert len(limits) == 1
        assert limits[0]["time_unit"] == "天"
    web._v2_manager = None
    web._config_service = None


def test_push_grok_without_token_records_auth_invalid(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch, secret="abc", enforce=False) as c:
        c.post("/api/push/v2/grok-main", json=VALID_PAYLOAD)
        conn = sqlite3.connect(tmp_path / "usage.db")
        rows = usage_store.get_source_auth_status(conn)
        grok = [r for r in rows if r["source_id"] == "grok-main"]
        assert grok and grok[0]["auth_valid"] is False
    web._v2_manager = None
    web._config_service = None
