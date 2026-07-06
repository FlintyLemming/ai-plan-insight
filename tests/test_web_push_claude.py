import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

import ai_plan_insight.web as web
from ai_plan_insight import usage_store
from ai_plan_insight.config import Config


VALID_PAYLOAD = {
    "seven_day": {"utilization": 45.2, "resets_at": "2026-07-08T12:00:00Z"},
    "five_hour": {"utilization": 12.8, "resets_at": "2026-07-01T15:00:00Z"},
}


def _reset_push_state(tmp_path, monkeypatch):
    """Clear in-memory push state and point the web layer at a fresh temp DB."""
    web._pushed_results.clear()
    web._pushed_at.clear()
    db = tmp_path / "usage.db"
    monkeypatch.setattr(web, "_usage_db_path", db)
    usage_store.init_db(db)
    monkeypatch.setattr(
        web, "load_config", lambda _=None: Config(
            providers={}, push_auth_secret="abc", enforce_push_auth=False
        )
    )


def test_push_claude_returns_ok(tmp_path, monkeypatch):
    _reset_push_state(tmp_path, monkeypatch)
    client = TestClient(web.app)
    resp = client.post("/api/push/claude", json=VALID_PAYLOAD)
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_push_claude_without_token_records_auth_invalid(tmp_path, monkeypatch):
    _reset_push_state(tmp_path, monkeypatch)
    client = TestClient(web.app)
    resp = client.post("/api/push/claude", json=VALID_PAYLOAD)
    assert resp.status_code == 200
    conn = sqlite3.connect(tmp_path / "usage.db")
    rows = usage_store.get_source_auth_status(conn)
    claude = [r for r in rows if r["source_id"] == "claude"]
    assert claude and claude[0]["auth_valid"] is False


def test_usage_returns_two_claude_limits_after_push(tmp_path, monkeypatch):
    _reset_push_state(tmp_path, monkeypatch)
    client = TestClient(web.app)

    client.post("/api/push/claude", json=VALID_PAYLOAD)
    resp = client.get("/api/usage")

    assert resp.status_code == 200
    providers = [u for u in resp.json() if u["provider"] == "Claude 订阅"]
    assert len(providers) == 1
    limits = providers[0]["limits"]
    assert len(limits) == 2

    five = limits[0]
    assert five["duration"] == 5
    assert five["time_unit"] == "小时"
    assert five["limit"] == "100"
    assert five["used"] == "12"
    assert five["remaining"] == "87"
    assert five["reset_time"] == "2026-07-01T15:00:00Z"
    assert five["limit_type"] == ""

    seven = limits[1]
    assert seven["duration"] == 7
    assert seven["time_unit"] == "天"
    assert seven["limit"] == "100"
    assert seven["used"] == "45"
    assert seven["remaining"] == "54"
    assert seven["reset_time"] == "2026-07-08T12:00:00Z"
    assert seven["limit_type"] == ""


def test_expired_push_is_not_returned(tmp_path, monkeypatch):
    _reset_push_state(tmp_path, monkeypatch)
    client = TestClient(web.app)

    client.post("/api/push/claude", json=VALID_PAYLOAD)
    # 手动把推送时间改为 31 分钟前，模拟 TTL 过期
    web._pushed_at["claude"] = datetime.now().astimezone() - timedelta(minutes=31)

    resp = client.get("/api/usage")

    assert resp.status_code == 200
    providers = [u for u in resp.json() if u["provider"] == "Claude 订阅"]
    assert providers == []
