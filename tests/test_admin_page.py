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
        dashboard = INDEX_HTML.read_text(encoding="utf-8")
        for token in (
            "--canvas: #f7f7f4;",
            "--surface-card: #ffffff;",
            "--hairline: #e6e5e0;",
            "--ink: #26251e;",
            "--primary: #f54e00;",
        ):
            assert token in h
            assert token in dashboard


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


class TestUsageSourcesSection:
    def test_panel_present_between_instances_and_aliases(self):
        h = read_admin()
        assert 'id="usage-sources-panel"' in h
        assert "用量上报机器" in h
        assert h.index('id="instances-panel"') < h.index('id="usage-sources-panel"') < h.index('id="aliases-panel"')

    def test_table_headers_and_detail_drawer(self):
        h = read_admin()
        for header in ("机器", "状态", "推送认证", "最后上报", "近 30 天 / 累计 Token", "数据范围", "操作"):
            assert header in h
        assert "source-detail-row" in h
        assert "最近上报 IP" in h
        assert "历史冻结边界" in h
        assert "常用模型" in h

    def test_calls_usage_sources_and_stale_endpoints(self):
        h = read_admin()
        assert "/api/admin/usage-sources" in h
        assert "/dismiss-stale" in h
        assert "/restore-stale" in h
        assert "loadUsageSources()" in h
        assert 'id="usage-sources-refresh-btn"' in h


class TestAliasesSection:
    def test_editor_present(self):
        h = read_admin()
        assert 'id="aliases-panel"' in h
        assert "模型别名" in h
        assert "/api/admin/aliases" in h
        assert "展示名" in h


class TestPricesSection:
    def test_panel_present_after_the_aliases_panel(self):
        h = read_admin()
        assert 'id="prices-panel"' in h
        assert "模型价格" in h
        assert h.index('id="aliases-panel"') < h.index('id="prices-panel"')

    def test_table_headers(self):
        h = read_admin()
        for header in ("模型 ID", "近 90 天 Token", "输入", "输出", "缓存读取", "缓存写入"):
            assert header in h

    def test_explains_the_source_and_the_unit(self):
        h = read_admin()
        assert "USD / 百万 Token" in h
        assert "models.dev" in h

    def test_calls_both_price_endpoints(self):
        h = read_admin()
        assert "/api/admin/model-prices" in h
        assert "function savePrices" in h
        assert "loadPrices()" in h

    def test_price_inputs_are_nonnegative_numbers(self):
        assert 'type="number" min="0" step="any"' in read_admin()

    def test_clear_button_present(self):
        assert "data-price-clear" in read_admin()

    def test_empty_and_not_ready_states(self):
        h = read_admin()
        assert "近 90 天内没有缺少价格的模型" in h
        assert "价格数据尚未就绪，请稍后刷新" in h

    def test_filter_pills_search_hide_and_truncation(self):
        h = read_admin()
        for filt in ("pending", "configured", "all", "hidden"):
            assert f'data-price-filter="{filt}"' in h
        assert 'id="price-search"' in h
        assert "const PRICE_PAGE_SIZE = 8;" in h
        assert 'id="prices-more-btn"' in h
        assert "/api/admin/model-prices/hidden" in h
        assert "data-price-hide" in h
        assert "data-price-unhide" in h
        # Saving collects from the full in-memory draft, not visible DOM rows.
        assert "PRICE_DRAFT" in h


class TestCollapsibleSections:
    def test_all_sections_are_collapsible_with_summary_and_instances_open_by_default(self):
        h = read_admin()
        assert 'id="toggle-all-sections-btn"' in h
        assert "ai-plan-insight:admin-open-sections" in h
        assert "DEFAULT_OPEN_SECTIONS = ['instances-panel']" in h
        for section_id in (
            "instances-panel", "usage-sources-panel", "aliases-panel", "prices-panel",
            "push-auth-panel", "display-settings-panel", "password-panel", "io-panel",
        ):
            assert f'data-section="{section_id}"' in h
        for badge_id in (
            "nav-badge-instances", "nav-badge-usage-sources",
            "nav-badge-aliases", "nav-badge-prices",
        ):
            assert f'id="{badge_id}"' in h


class TestPushAuthSection:
    def test_secret_reveal_and_generate(self):
        h = read_admin()
        assert 'id="push-auth-panel"' in h
        assert "/api/admin/push-auth/secret" in h
        assert "生成随机值" in h
        assert 'id="enforce-push-auth"' in h


class TestDisplaySettingsSection:
    def test_panel_present_after_the_push_auth_panel(self):
        h = read_admin()
        assert 'id="display-settings-panel"' in h
        assert "显示设置" in h
        assert h.index('id="push-auth-panel"') < h.index('id="display-settings-panel"')

    def test_input_is_a_number_with_min_half(self):
        h = read_admin()
        assert 'type="number" min="0.5" step="any"' in h

    def test_explains_push_only_and_fetch_unaffected(self):
        h = read_admin()
        assert "push 模式" in h
        assert "fetch" in h

    def test_calls_both_display_settings_endpoints(self):
        h = read_admin()
        assert "/api/admin/display-settings" in h
        assert "function saveDisplaySettings" in h
        assert "loadDisplaySettings()" in h


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
