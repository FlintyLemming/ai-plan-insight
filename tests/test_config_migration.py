import json
import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

from ai_plan_insight import config_migration, config_store

UTC8 = timezone(timedelta(hours=8))
NOW = datetime(2026, 7, 27, 10, 30, 0, tzinfo=UTC8)

FULL_CONFIG = {
    "providers": {
        "claude-personal": {"type": "claude", "mode": "push", "label": "个人号", "order": 12},
        "kimi-main": {"type": "kimi", "mode": "fetch", "label": "主账号",
                      "api_key": "sk-abcdef1234", "order": 30},
    },
    "model_aliases": {"GLM 5.2": ["glm-5.2", "glm5.2"]},
    "push_auth_secret": "s3cret",
    "enforce_push_auth": True,
}


@pytest.fixture
def conn(tmp_path):
    c = sqlite3.connect(tmp_path / "usage.db")
    config_store.init_config_schema(c)
    yield c
    c.close()


def _write(tmp_path, data, name="config.json") -> Path:
    p = tmp_path / name
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return p


class TestHappyPath:
    def test_migrates_instances_aliases_and_push_auth(self, conn, tmp_path):
        src = _write(tmp_path, FULL_CONFIG)
        result = config_migration.run_migration(conn, src, now=NOW)

        assert result.migrated is True
        assert result.reason == "migrated"
        assert {r.instance_id for r in config_store.list_instances(conn)} == {
            "claude-personal", "kimi-main",
        }
        assert config_store.get_instance(conn, "kimi-main").credentials == {
            "api_key": "sk-abcdef1234",
        }
        assert config_store.get_aliases(conn) == {"GLM 5.2": ["glm-5.2", "glm5.2"]}
        assert config_store.get_push_auth(conn) == ("s3cret", True)

    def test_migrated_instances_are_enabled(self, conn, tmp_path):
        config_migration.run_migration(conn, _write(tmp_path, FULL_CONFIG), now=NOW)
        assert all(r.enabled for r in config_store.list_instances(conn))

    def test_bumps_config_version(self, conn, tmp_path):
        before = config_store.get_config_version(conn)
        config_migration.run_migration(conn, _write(tmp_path, FULL_CONFIG), now=NOW)
        assert config_store.get_config_version(conn) == before + 1

    def test_renames_source_to_bak(self, conn, tmp_path):
        src = _write(tmp_path, FULL_CONFIG)
        config_migration.run_migration(conn, src, now=NOW)
        assert not src.exists()
        assert (tmp_path / "config.json.bak").exists()

    def test_writes_notice_and_summary(self, conn, tmp_path):
        config_migration.run_migration(conn, _write(tmp_path, FULL_CONFIG), now=NOW)
        assert config_store.get_setting(conn, "migration_notice") == "pending"
        assert config_store.get_setting(conn, "config_migrated_at") == NOW.isoformat()
        summary = json.loads(config_store.get_setting(conn, "migration_summary_json"))
        assert summary["instances"] == 2
        assert summary["aliases"] == 1
        assert summary["skipped"] == {}
        assert summary["backup_ok"] is True
        assert summary["backup_path"].endswith("config.json.bak")


class TestBackupNaming:
    def test_existing_bak_gets_a_timestamp_suffix(self, conn, tmp_path):
        src = _write(tmp_path, FULL_CONFIG)
        (tmp_path / "config.json.bak").write_text("older backup", encoding="utf-8")
        config_migration.run_migration(conn, src, now=NOW)
        assert (tmp_path / "config.json.bak").read_text(encoding="utf-8") == "older backup"
        assert (tmp_path / "config.json.bak.20260727-103000").exists()

    def test_backup_path_for_is_pure(self, tmp_path):
        src = tmp_path / "config.json"
        assert config_migration.backup_path_for(src, NOW).name == "config.json.bak"


class TestSkipConditions:
    def test_no_file_is_a_noop(self, conn, tmp_path):
        result = config_migration.run_migration(conn, tmp_path / "absent.json", now=NOW)
        assert result.migrated is False
        assert result.reason == "no_file"
        assert config_store.get_setting(conn, "migration_notice") is None

    def test_non_empty_db_is_not_migrated_and_file_is_kept(self, conn, tmp_path):
        config_store.create_instance(conn, config_store.InstanceRow(
            instance_id="existing", type="claude", mode="push", label="已有",
        ))
        src = _write(tmp_path, FULL_CONFIG)
        result = config_migration.run_migration(conn, src, now=NOW)

        assert result.reason == "db_not_empty"
        assert src.exists()
        assert {r.instance_id for r in config_store.list_instances(conn)} == {"existing"}
        assert config_store.get_setting(conn, "migration_notice") is None

    def test_bad_json_is_not_migrated_and_file_is_kept(self, conn, tmp_path):
        src = tmp_path / "config.json"
        src.write_text("{not json", encoding="utf-8")
        result = config_migration.run_migration(conn, src, now=NOW)

        assert result.reason == "parse_error"
        assert result.summary["error"]
        assert src.exists()
        assert config_store.list_instances(conn) == []
        assert config_store.get_setting(conn, "migration_notice") is None


class TestPartialConfigs:
    def test_instance_errors_are_skipped_but_summarized(self, conn, tmp_path):
        src = _write(tmp_path, {
            "providers": {
                "good-one": {"type": "claude", "mode": "push", "label": "好的"},
                "bad-one": {"type": "nonexistent", "mode": "fetch", "label": "坏的"},
            },
        })
        result = config_migration.run_migration(conn, src, now=NOW)

        assert {r.instance_id for r in config_store.list_instances(conn)} == {"good-one"}
        assert "bad-one" in result.summary["skipped"]

    def test_rename_failure_keeps_migrated_config(self, conn, tmp_path, monkeypatch):
        src = _write(tmp_path, FULL_CONFIG)

        def boom(self, target):
            raise OSError("read-only file system")

        monkeypatch.setattr(Path, "rename", boom)
        result = config_migration.run_migration(conn, src, now=NOW)

        assert result.migrated is True
        assert result.summary["backup_ok"] is False
        assert result.summary["backup_error"]
        assert len(config_store.list_instances(conn)) == 2
        assert src.exists()

    def test_second_run_after_rename_failure_does_not_reimport(self, conn, tmp_path, monkeypatch):
        src = _write(tmp_path, FULL_CONFIG)
        monkeypatch.setattr(Path, "rename", lambda self, target: (_ for _ in ()).throw(OSError("ro")))
        config_migration.run_migration(conn, src, now=NOW)
        monkeypatch.undo()

        second = config_migration.run_migration(conn, src, now=NOW)
        assert second.reason == "db_not_empty"
        assert len(config_store.list_instances(conn)) == 2
