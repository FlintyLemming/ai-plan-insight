from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from config_fixtures import seed_config

from ai_plan_insight import web

ADMIN_HTML = Path(__file__).resolve().parents[1] / "ai_plan_insight" / "admin.html"


def read_admin() -> str:
    return ADMIN_HTML.read_text(encoding="utf-8")


@pytest.fixture
def client(tmp_path):
    db = seed_config(tmp_path / "usage.db")
    web._config_path = str(tmp_path / "config.json")
    web._usage_db_path = db
    web._v2_manager = None
    web._config_service = None
    with TestClient(web.app) as c:
        yield c
    web._v2_manager = None
    web._config_service = None


class TestRoute:
    def test_admin_page_is_served_without_login(self, client):
        resp = client.get("/admin")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]

    def test_admin_page_ships_no_config_data(self, client):
        # The shell must be data-free: everything comes from protected APIs.
        body = client.get("/admin").text
        assert "push_auth_secret\":" not in body
        assert "api_key\":" not in body


class TestShell:
    def test_login_and_admin_views_exist(self):
        h = read_admin()
        assert 'id="login-view"' in h
        assert 'id="admin-view"' in h
        assert 'id="password-input"' in h

    def test_password_not_configured_guidance(self):
        h = read_admin()
        assert "ADMIN_PASSWORD" in h
        assert "未设置管理密码" in h

    def test_banner_container_and_dismiss(self):
        h = read_admin()
        assert 'id="banners"' in h
        assert "/api/notices/migration/dismiss" in h
        assert "配置以数据库为准" in h

    def test_uses_the_dashboard_palette(self):
        h = read_admin()
        assert "#0f172a" in h
        assert "#1e293b" in h
        assert "#334155" in h


class TestInstancesSection:
    def test_table_headers(self):
        h = read_admin()
        for header in ["标签", "类型", "模式", "排序", "状态", "操作"]:
            assert header in h

    def test_calls_the_instance_endpoints(self):
        h = read_admin()
        assert "/api/admin/instances" in h
        assert "/api/admin/provider-types" in h
        assert "/api/admin/instances/${" in h

    def test_instance_id_is_locked_when_editing(self):
        h = read_admin()
        assert "改名请删除后重建" in h

    def test_secret_reveal_uses_the_secrets_endpoint(self):
        h = read_admin()
        assert "/secrets" in h

    def test_test_connection_button_present(self):
        h = read_admin()
        assert "测试连接" in h
        assert "/api/admin/instances/test" in h

    def test_disabled_rows_are_dimmed(self):
        h = read_admin()
        assert "row-disabled" in h

    def test_invalid_rows_are_highlighted(self):
        h = read_admin()
        assert "row-error" in h


INDEX_HTML = Path(__file__).resolve().parents[1] / "ai_plan_insight" / "index.html"


def read_index() -> str:
    return INDEX_HTML.read_text(encoding="utf-8")


class TestAliasesSection:
    def test_editor_present(self):
        h = read_admin()
        assert 'id="aliases-panel"' in h
        assert "模型别名" in h
        assert "/api/admin/aliases" in h
        assert "展示名" in h


class TestPushAuthSection:
    def test_secret_reveal_and_generate(self):
        h = read_admin()
        assert 'id="push-auth-panel"' in h
        assert "/api/admin/push-auth/secret" in h
        assert "生成随机值" in h
        assert 'id="enforce-push-auth"' in h


class TestPasswordSection:
    def test_three_fields_and_logout_all(self):
        h = read_admin()
        assert 'id="password-panel"' in h
        assert 'id="pwd-old"' in h
        assert 'id="pwd-new"' in h
        assert 'id="pwd-confirm"' in h
        assert "/api/admin/logout-all" in h


class TestExportImportSection:
    def test_export_download_and_import_confirm(self):
        h = read_admin()
        assert 'id="io-panel"' in h
        assert "/api/admin/export" in h
        assert "/api/admin/import" in h
        assert "将覆盖现有全部配置" in h


class TestDashboardIntegration:
    def test_index_links_to_the_admin_page(self):
        h = read_index()
        assert 'href="/admin"' in h

    def test_index_shows_the_migration_banner(self):
        h = read_index()
        assert "/api/notices" in h
        assert 'id="notice-banners"' in h

    def test_index_can_dismiss_the_migration_banner(self):
        h = read_index()
        assert "/api/notices/migration/dismiss" in h
