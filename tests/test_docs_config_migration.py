"""Guards that the docs describe the DB-backed config, not the old file."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


class TestReadme:
    def test_documents_the_admin_page(self):
        r = read("README.md")
        assert "/admin" in r
        assert "管理页" in r

    def test_documents_both_env_vars(self):
        r = read("README.md")
        assert "ADMIN_PASSWORD" in r
        assert "ADMIN_PASSWORD_RESET" in r

    def test_documents_the_automatic_migration(self):
        r = read("README.md")
        assert "config.json.bak" in r

    def test_config_flag_is_described_as_a_migration_source(self):
        r = read("README.md")
        assert "迁移源" in r

    def test_no_longer_tells_users_to_hand_edit_config_json(self):
        r = read("README.md")
        assert "### 配置（config.json）" not in r


class TestDeploymentSamples:
    def test_compose_sets_the_admin_password(self):
        assert "ADMIN_PASSWORD" in read("sample-compose.yaml")

    def test_compose_no_longer_mentions_config_v2_json(self):
        assert "config.v2.json" not in read("sample-compose.yaml")

    def test_example_config_is_labelled_migration_only(self):
        # The label lives in the README: config.json.example must stay pure
        # JSON that load_v2_config() accepts, so it cannot carry a comment key.
        assert "仅用于从旧版迁移" in read("README.md")

    def test_example_config_stays_loadable(self):
        from ai_plan_insight.instance_config import load_v2_config
        result = load_v2_config(str(ROOT / "config.json.example"))
        assert result.config_error is None


class TestCliHelp:
    def test_config_flag_help_mentions_migration(self):
        source = read("ai_plan_insight/__main__.py")
        assert "迁移" in source or "migration" in source.lower()
