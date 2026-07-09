import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

import ai_plan_insight.web as web
from ai_plan_insight import usage_store
from ai_plan_insight.config import Config


VALID_PAYLOAD = {
    "weekly": {"utilization": 45.2, "resets_at": "2026-07-10T04:01:09Z"},
    "plan": "SuperGrok",
}


def _reset_push_state(tmp_path, monkeypatch):
    """Clear in-memory push state and point the web layer at a fresh temp DB."""
    web._pushed_results.clear()
    web._pushed_at.clear()
    db = tmp_path / "usage.db"
    monkeypatch.setattr(web, "_usage_db_path", db)
    usage_store.init_db(db)
    monkeypatch.setattr(
        web,
        "load_config",
        lambda _=None: Config(
            providers={}, push_auth_secret="abc", enforce_push_auth=False
        ),
    )


def test_push_grok_returns_ok(tmp_path, monkeypatch):
    _reset_push_state(tmp_path, monkeypatch)
    client = TestClient(web.app)
    resp = client.post("/api/push/grok", json=VALID_PAYLOAD)
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_usage_returns_grok_limit_after_push(tmp_path, monkeypatch):
    _reset_push_state(tmp_path, monkeypatch)
    client = TestClient(web.app)

    client.post("/api/push/grok", json=VALID_PAYLOAD)
    resp = client.get("/api/usage")

    assert resp.status_code == 200
    providers = [u for u in resp.json() if u["provider"] == "Grok 订阅"]
    assert len(providers) == 1
    card = providers[0]
    assert card["membership_level"] == "SuperGrok"
    limits = card["limits"]
    assert len(limits) == 1
    limit = limits[0]
    assert limit["duration"] == 7
    assert limit["time_unit"] == "天"
    assert limit["limit"] == "100"
    assert limit["used"] == "45"  # int(45.2)
    assert limit["remaining"] == "54"  # int(100 - 45.2)
    assert limit["reset_time"] == "2026-07-10T04:01:09Z"
    assert limit["limit_type"] == ""


def test_push_grok_without_plan_has_null_membership(tmp_path, monkeypatch):
    _reset_push_state(tmp_path, monkeypatch)
    client = TestClient(web.app)

    payload = {
        "weekly": {"utilization": 12.0, "resets_at": "2026-07-10T04:01:09Z"},
    }
    client.post("/api/push/grok", json=payload)
    resp = client.get("/api/usage")
    providers = [u for u in resp.json() if u["provider"] == "Grok 订阅"]
    assert len(providers) == 1
    assert providers[0]["membership_level"] is None


def test_push_grok_blank_plan_treated_as_missing(tmp_path, monkeypatch):
    _reset_push_state(tmp_path, monkeypatch)
    client = TestClient(web.app)

    payload = {
        "weekly": {"utilization": 12.0, "resets_at": "2026-07-10T04:01:09Z"},
        "plan": "  ",
    }
    client.post("/api/push/grok", json=payload)
    resp = client.get("/api/usage")
    providers = [u for u in resp.json() if u["provider"] == "Grok 订阅"]
    assert len(providers) == 1
    assert providers[0]["membership_level"] is None


def test_push_grok_missing_weekly_returns_422(tmp_path, monkeypatch):
    _reset_push_state(tmp_path, monkeypatch)
    client = TestClient(web.app)

    resp = client.post("/api/push/grok", json={"plan": "SuperGrok"})
    assert resp.status_code == 422


def test_expired_grok_push_is_not_returned(tmp_path, monkeypatch):
    _reset_push_state(tmp_path, monkeypatch)
    client = TestClient(web.app)

    push_resp = client.post("/api/push/grok", json=VALID_PAYLOAD)
    assert push_resp.status_code == 200
    web._pushed_at["grok"] = datetime.now().astimezone() - timedelta(minutes=31)

    resp = client.get("/api/usage")

    assert resp.status_code == 200
    providers = [u for u in resp.json() if u["provider"] == "Grok 订阅"]
    assert providers == []


def test_push_grok_without_token_records_auth_invalid(tmp_path, monkeypatch):
    _reset_push_state(tmp_path, monkeypatch)
    client = TestClient(web.app)
    resp = client.post("/api/push/grok", json=VALID_PAYLOAD)
    assert resp.status_code == 200
    conn = sqlite3.connect(tmp_path / "usage.db")
    rows = usage_store.get_source_auth_status(conn)
    grok = [r for r in rows if r["source_id"] == "grok"]
    assert grok and grok[0]["auth_valid"] is False
