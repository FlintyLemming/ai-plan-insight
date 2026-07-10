"""Startup restore of push card snapshots from SQLite."""
import sqlite3
from datetime import datetime, timedelta

from fastapi.testclient import TestClient

import ai_plan_insight.web as web
from ai_plan_insight import usage_store
from ai_plan_insight.api_schemas import LimitResponse, UsageResponse
from ai_plan_insight.config import Config, ProviderConfig


def _fresh_app_state(tmp_path, monkeypatch):
    """Point web at a temp DB and clear in-memory push state."""
    db = tmp_path / "usage.db"
    monkeypatch.setattr(web, "_usage_db_path", db)
    monkeypatch.setattr(web, "_cached_results", [])
    web._pushed_results.clear()
    web._pushed_at.clear()
    monkeypatch.setattr(
        web,
        "load_config",
        lambda _=None: Config(
            providers={"claude": ProviderConfig(order=12)},
            push_auth_secret="abc",
            enforce_push_auth=False,
        ),
    )
    return db


def test_startup_restores_fresh_push_snapshot(tmp_path, monkeypatch):
    db = _fresh_app_state(tmp_path, monkeypatch)
    usage_store.init_db(db)

    usage = UsageResponse(
        provider="Claude 订阅",
        limits=[
            LimitResponse(
                duration=5, time_unit="小时", limit="100", used="12", remaining="88"
            )
        ],
    )
    now = datetime.now().astimezone()
    with sqlite3.connect(db) as conn:
        usage_store.upsert_push_card_snapshot(
            conn, "claude", usage, recorded_at=now.isoformat()
        )
        conn.commit()

    # Lifespan should load the snapshot into memory.
    with TestClient(web.app) as client:
        assert "claude" in web._pushed_results
        assert web._pushed_results["claude"].provider == "Claude 订阅"
        assert web._pushed_at["claude"].isoformat() == now.isoformat()

        resp = client.get("/api/usage")
        providers = [u for u in resp.json() if u["provider"] == "Claude 订阅"]
        assert len(providers) == 1
        assert providers[0]["limits"][0]["used"] == "12"


def test_startup_restores_but_ttl_hides_stale_snapshot(tmp_path, monkeypatch):
    db = _fresh_app_state(tmp_path, monkeypatch)
    usage_store.init_db(db)

    usage = UsageResponse(provider="Claude 订阅", limits=[])
    stale = datetime.now().astimezone() - timedelta(minutes=31)
    with sqlite3.connect(db) as conn:
        usage_store.upsert_push_card_snapshot(
            conn, "claude", usage, recorded_at=stale.isoformat()
        )
        conn.commit()

    with TestClient(web.app) as client:
        # Restored into memory…
        assert "claude" in web._pushed_results
        # …but TTL filter keeps it out of /api/usage.
        providers = [
            u for u in client.get("/api/usage").json() if u["provider"] == "Claude 订阅"
        ]
        assert providers == []


def test_push_persists_card_snapshot(tmp_path, monkeypatch):
    db = _fresh_app_state(tmp_path, monkeypatch)
    usage_store.init_db(db)

    with TestClient(web.app) as client:
        resp = client.post(
            "/api/push/claude",
            json={
                "seven_day": {"utilization": 45.2, "resets_at": "2026-07-08T12:00:00Z"},
                "five_hour": {"utilization": 12.8, "resets_at": "2026-07-01T15:00:00Z"},
            },
        )
        assert resp.status_code == 200

    with sqlite3.connect(db) as conn:
        rows = conn.execute(
            "SELECT push_key, raw_json FROM push_card_snapshot"
        ).fetchall()
    assert len(rows) == 1
    assert rows[0][0] == "claude"
    assert "Claude 订阅" in rows[0][1]
