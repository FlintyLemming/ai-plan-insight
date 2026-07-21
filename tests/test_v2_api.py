# tests/test_v2_api.py
import json
import pytest
from pathlib import Path
from fastapi.testclient import TestClient


@pytest.fixture
def v2_config_path(tmp_path: Path) -> Path:
    p = tmp_path / "config.v2.json"
    p.write_text(json.dumps({
        "providers": {
            "claude-personal": {
                "type": "claude", "mode": "push", "label": "个人号", "order": 12,
            },
            "claude-work": {
                "type": "claude", "mode": "push", "label": "工作号", "order": 13,
            },
        },
        "push_auth_secret": "test-secret",
        "enforce_push_auth": True,
    }))
    return p


@pytest.fixture
def client(v2_config_path: Path, tmp_path: Path) -> TestClient:
    """Create a test client with v2 enabled."""
    # Write a minimal old config so the app starts
    old_config = tmp_path / "config.json"
    old_config.write_text(json.dumps({"providers": {}}))

    import ai_plan_insight.web as web_mod
    web_mod._config_path = str(old_config)
    web_mod._usage_db_path = tmp_path / "usage.db"
    web_mod._v2_config_path = str(v2_config_path)

    # Reset v2 manager state
    web_mod._v2_manager = None

    from ai_plan_insight.web import app
    with TestClient(app) as c:
        yield c

    # Cleanup
    web_mod._v2_manager = None
    web_mod._v2_config_path = None


class TestV2Status:
    def test_status_enabled(self, client: TestClient):
        resp = client.get("/api/status/v2")
        assert resp.status_code == 200
        data = resp.json()
        assert data["enabled"] is True
        assert data["config_error"] is None
        assert "v1_has_providers" in data

    def test_v1_has_providers_false_when_empty(self, client: TestClient):
        """The test fixture uses an empty old config, so v1_has_providers should be False."""
        resp = client.get("/api/status/v2")
        data = resp.json()
        assert data["v1_has_providers"] is False

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
        """When v2 config doesn't exist, v2 endpoints return disabled/empty."""
        old_config = tmp_path / "config.json"
        old_config.write_text(json.dumps({"providers": {}}))

        import ai_plan_insight.web as web_mod
        web_mod._config_path = str(old_config)
        web_mod._usage_db_path = tmp_path / "usage.db"
        web_mod._v2_config_path = str(tmp_path / "nonexistent_v2.json")
        web_mod._v2_manager = None

        from ai_plan_insight.web import app
        with TestClient(app) as c:
            resp = c.get("/api/status/v2")
            assert resp.status_code == 200
            data = resp.json()
            assert data["enabled"] is False
            assert data["v1_has_providers"] is False  # empty old config

            resp = c.get("/api/usage/v2")
            assert resp.status_code == 200
            assert resp.json() == []

            resp = c.post(
                "/api/push/v2/any-instance",
                json={"test": True},
            )
            assert resp.status_code == 503

        web_mod._v2_manager = None
        web_mod._v2_config_path = None


class TestOldSystemUntouched:
    def test_old_usage_still_works(self, client: TestClient):
        resp = client.get("/api/usage")
        assert resp.status_code == 200

    def test_old_status_still_works(self, client: TestClient):
        resp = client.get("/api/status")
        assert resp.status_code == 200
