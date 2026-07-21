# tests/test_v2_api.py
import json
import pytest
from pathlib import Path
from fastapi.testclient import TestClient


def _config_json(providers: dict, **top) -> dict:
    d = {"providers": providers}
    d.update(top)
    return d


@pytest.fixture
def config_path(tmp_path: Path) -> Path:
    p = tmp_path / "config.json"
    p.write_text(json.dumps(_config_json(
        {
            "claude-personal": {"type": "claude", "mode": "push", "label": "个人号", "order": 12},
            "claude-work": {"type": "claude", "mode": "push", "label": "工作号", "order": 13},
        },
        push_auth_secret="test-secret",
        enforce_push_auth=True,
    )))
    return p


@pytest.fixture
def client(config_path: Path, tmp_path: Path) -> TestClient:
    import ai_plan_insight.web as web_mod
    web_mod._config_path = str(config_path)
    web_mod._usage_db_path = tmp_path / "usage.db"
    web_mod._v2_manager = None
    web_mod._config_service = None
    from ai_plan_insight.web import app
    with TestClient(app) as c:
        yield c
    web_mod._v2_manager = None
    web_mod._config_service = None


class TestV2Status:
    def test_status_enabled(self, client: TestClient):
        resp = client.get("/api/status/v2")
        assert resp.status_code == 200
        data = resp.json()
        assert data["enabled"] is True
        assert data["config_error"] is None
        assert "instance_errors" in data
        assert data["instance_errors"] == {}
        assert "v1_has_providers" not in data

    def test_status_exposes_instance_errors_key(self, client: TestClient):
        resp = client.get("/api/status/v2")
        data = resp.json()
        assert "instance_errors" in data
        assert data["instance_errors"] == {}


class TestV2Usage:
    def test_empty_initially(self, client: TestClient):
        resp = client.get("/api/usage/v2")
        assert resp.status_code == 200
        assert resp.json() == []


class TestV2Push:
    def test_push_success(self, client: TestClient):
        resp = client.post(
            "/api/push/v2/claude-work",
            json={
                "seven_day": {"utilization": 45.2, "resets_at": "2026-07-08T12:00:00Z"},
                "five_hour": {"utilization": 12.8, "resets_at": "2026-07-01T15:00:00Z"},
            },
            headers={"Authorization": "Bearer test-secret"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["instance_id"] == "claude-work"

    def test_push_unknown_instance(self, client: TestClient):
        resp = client.post(
            "/api/push/v2/nonexistent",
            json={
                "seven_day": {"utilization": 10.0, "resets_at": "2026-07-08T12:00:00Z"},
                "five_hour": {"utilization": 5.0, "resets_at": "2026-07-01T15:00:00Z"},
            },
            headers={"Authorization": "Bearer test-secret"},
        )
        assert resp.status_code == 404

    def test_push_auth_required(self, client: TestClient):
        resp = client.post(
            "/api/push/v2/claude-work",
            json={
                "seven_day": {"utilization": 10.0, "resets_at": "2026-07-08T12:00:00Z"},
                "five_hour": {"utilization": 5.0, "resets_at": "2026-07-01T15:00:00Z"},
            },
            # No auth header
        )
        assert resp.status_code == 401

    def test_push_bad_schema(self, client: TestClient):
        resp = client.post(
            "/api/push/v2/claude-work",
            json={"bad": "data"},
            headers={"Authorization": "Bearer test-secret"},
        )
        assert resp.status_code == 422

    def test_push_appears_in_usage(self, client: TestClient):
        # Push to both instances
        for inst_id in ["claude-personal", "claude-work"]:
            client.post(
                f"/api/push/v2/{inst_id}",
                json={
                    "seven_day": {"utilization": 30.0, "resets_at": "2026-07-08T12:00:00Z"},
                    "five_hour": {"utilization": 10.0, "resets_at": "2026-07-01T15:00:00Z"},
                },
                headers={"Authorization": "Bearer test-secret"},
            )

        resp = client.get("/api/usage/v2")
        data = resp.json()
        assert len(data) == 2
        providers = {d["provider"] for d in data}
        assert "Claude 订阅 · 个人号" in providers
        assert "Claude 订阅 · 工作号" in providers
        # Check v2 fields
        for d in data:
            assert "instance_id" in d
            assert "type" in d
            assert "instance_label" in d
            assert "type_display_name" in d

    def test_push_ordering(self, client: TestClient):
        for inst_id in ["claude-work", "claude-personal"]:
            client.post(
                f"/api/push/v2/{inst_id}",
                json={
                    "seven_day": {"utilization": 30.0, "resets_at": "2026-07-08T12:00:00Z"},
                    "five_hour": {"utilization": 10.0, "resets_at": "2026-07-01T15:00:00Z"},
                },
                headers={"Authorization": "Bearer test-secret"},
            )
        resp = client.get("/api/usage/v2")
        data = resp.json()
        # order 12 (personal) before order 13 (work)
        assert data[0]["instance_id"] == "claude-personal"
        assert data[1]["instance_id"] == "claude-work"


class TestV2Disabled:
    def test_disabled_when_no_config(self, tmp_path: Path):
        import ai_plan_insight.web as web_mod
        web_mod._config_path = str(tmp_path / "nonexistent.json")
        web_mod._usage_db_path = tmp_path / "usage.db"
        web_mod._v2_manager = None
        web_mod._config_service = None
        from ai_plan_insight.web import app
        with TestClient(app) as c:
            resp = c.get("/api/status/v2")
            assert resp.status_code == 200
            data = resp.json()
            assert data["enabled"] is False
            assert data["config_error"] is not None
            assert c.get("/api/usage/v2").json() == []
            assert c.post("/api/push/v2/any", json={}).status_code == 503
        web_mod._v2_manager = None
        web_mod._config_service = None


class TestV1EndpointsRemoved:
    def test_old_usage_returns_404(self, client: TestClient):
        assert client.get("/api/usage").status_code == 404

    def test_old_status_returns_404(self, client: TestClient):
        assert client.get("/api/status").status_code == 404

    def test_old_push_claude_returns_404(self, client: TestClient):
        assert client.post("/api/push/claude", json={}).status_code == 404

    def test_old_push_grok_returns_404(self, client: TestClient):
        assert client.post("/api/push/grok", json={}).status_code == 404

    def test_old_push_cursor_returns_404(self, client: TestClient):
        assert client.post("/api/push/cursor", json={}).status_code == 404

    def test_old_push_mimo_returns_404(self, client: TestClient):
        assert client.post("/api/push/mimo", json={}).status_code == 404

    def test_old_push_antigravity_returns_404(self, client: TestClient):
        assert client.post("/api/push/antigravity", json={}).status_code == 404
