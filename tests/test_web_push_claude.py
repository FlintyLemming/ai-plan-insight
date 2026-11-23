import sqlite3
from pathlib import Path
from fastapi.testclient import TestClient

import ai_plan_insight.web as web
from ai_plan_insight import usage_store

VALID_PAYLOAD = {
    "seven_day": {"utilization": 45.2, "resets_at": "2026-07-08T12:00:00Z"},
    "five_hour": {"utilization": 12.8, "resets_at": "2026-07-01T15:00:00Z"},
}


def _client(tmp_path, monkeypatch, secret="abc", enforce=False):
    from config_fixtures import seed_config
    db = seed_config(
        tmp_path / "usage.db",
        providers={
            "claude-main": {"type": "claude", "mode": "push", "label": "主力", "order": 1},
        },
        push_auth_secret=secret,
        enforce_push_auth=enforce,
    )
    web._config_path = str(tmp_path / "config.json")
    web._usage_db_path = db
    web._v2_manager = None
    web._config_service = None
    return TestClient(web.app)


def test_push_claude_returns_ok(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch, secret="abc", enforce=True) as c:
        resp = c.post("/api/push/v2/claude-main", json=VALID_PAYLOAD,
                      headers={"Authorization": "Bearer abc"})
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok", "instance_id": "claude-main"}


def test_push_claude_without_token_records_auth_invalid(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch, secret="abc", enforce=False) as c:
        resp = c.post("/api/push/v2/claude-main", json=VALID_PAYLOAD)
        assert resp.status_code == 200
        conn = sqlite3.connect(tmp_path / "usage.db")
        rows = usage_store.get_source_auth_status(conn)
        claude = [r for r in rows if r["source_id"] == "claude-main"]
        assert claude and claude[0]["auth_valid"] is False
    web._v2_manager = None
    web._config_service = None


def test_push_claude_hard_invalid_returns_401(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch, secret="abc", enforce=True) as c:
        resp = c.post("/api/push/v2/claude-main", json=VALID_PAYLOAD,
                      headers={"Authorization": "Bearer wrong"})
        assert resp.status_code == 401
    web._v2_manager = None
    web._config_service = None


def test_push_claude_appears_in_usage_v2(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch, secret="abc", enforce=True) as c:
        c.post("/api/push/v2/claude-main", json=VALID_PAYLOAD,
               headers={"Authorization": "Bearer abc"})
        data = c.get("/api/usage/v2").json()
        assert len(data) == 1
        assert data[0]["instance_id"] == "claude-main"
        assert data[0]["type"] == "claude"
    web._v2_manager = None
    web._config_service = None


def test_push_claude_auth_error_and_recovery(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch, secret="abc", enforce=True) as c:
        resp = c.post(
            "/api/push/v2/claude-main",
            json={"error": "需要重新登录"},
            headers={"Authorization": "Bearer abc"},
        )
        assert resp.status_code == 200
        data = c.get("/api/usage/v2").json()
        assert len(data) == 1
        assert data[0]["instance_id"] == "claude-main"
        assert data[0]["error"] == "需要重新登录"
        assert data[0]["limits"] == []

        # Pushing normal usage afterwards clears the error state
        resp = c.post(
            "/api/push/v2/claude-main",
            json=VALID_PAYLOAD,
            headers={"Authorization": "Bearer abc"},
        )
        assert resp.status_code == 200
        data = c.get("/api/usage/v2").json()
        assert data[0]["error"] is None
        assert len(data[0]["limits"]) == 2
    web._v2_manager = None
    web._config_service = None


def test_push_claude_rejects_empty_error_without_windows(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch, secret="abc", enforce=True) as c:
        resp = c.post(
            "/api/push/v2/claude-main",
            json={"error": "   "},
            headers={"Authorization": "Bearer abc"},
        )
        assert resp.status_code == 422
    web._v2_manager = None
    web._config_service = None


def test_push_claude_allows_null_resets_at_and_partial_windows(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch, secret="abc", enforce=True) as c:
        resp = c.post(
            "/api/push/v2/claude-main",
            json={
                "five_hour": {"utilization": 0.0, "resets_at": None},
                "seven_day": {"utilization": 10.0, "resets_at": "2026-07-08T12:00:00Z"},
            },
            headers={"Authorization": "Bearer abc"},
        )
        assert resp.status_code == 200
        data = c.get("/api/usage/v2").json()
        assert len(data[0]["limits"]) == 2
        assert data[0]["limits"][0]["reset_time"] is None
    web._v2_manager = None
    web._config_service = None


def test_index_html_renders_error_card_with_header_and_escaped_message():
    html = (Path(__file__).resolve().parents[1] / "ai_plan_insight" / "index.html").read_text(encoding="utf-8")
    assert "card card-error" in html
    assert "card-error-msg" in html
    assert "escapeHtml(data.error)" in html
