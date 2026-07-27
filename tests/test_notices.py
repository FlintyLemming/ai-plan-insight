import json
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from config_fixtures import seed_config

from ai_plan_insight import config_store, web


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


def _set(db, key, value):
    with closing(sqlite3.connect(db)) as conn:
        config_store.set_setting(conn, key, value)


class TestMigrationNotice:
    def test_absent_by_default(self, client, tmp_path):
        assert client.get("/api/notices").json()["migration"] is None

    def test_pending_notice_is_returned_with_summary(self, client, tmp_path):
        db = tmp_path / "usage.db"
        _set(db, "migration_notice", "pending")
        _set(db, "migration_summary_json", json.dumps({"instances": 2, "aliases": 1}))
        notice = client.get("/api/notices").json()["migration"]
        assert notice["status"] == "pending"
        assert notice["summary"]["instances"] == 2

    def test_dismiss_hides_it(self, client, tmp_path):
        db = tmp_path / "usage.db"
        _set(db, "migration_notice", "pending")
        _set(db, "migration_summary_json", "{}")
        assert client.post("/api/notices/migration/dismiss").json() == {"ok": True}
        assert client.get("/api/notices").json()["migration"] is None

    def test_dismiss_needs_no_login(self, client, tmp_path):
        assert client.post("/api/notices/migration/dismiss").status_code == 200


class TestConfigFileNotice:
    def test_absent_when_no_config_file(self, client):
        assert client.get("/api/notices").json()["config_file"] is None

    def test_ignored_when_a_parsable_file_remains(self, client, tmp_path):
        (tmp_path / "config.json").write_text(json.dumps({"providers": {}}), encoding="utf-8")
        notice = client.get("/api/notices").json()["config_file"]
        assert notice["kind"] == "ignored"
        assert notice["path"].endswith("config.json")

    def test_parse_error_reports_the_message(self, client, tmp_path):
        (tmp_path / "config.json").write_text("{not json", encoding="utf-8")
        notice = client.get("/api/notices").json()["config_file"]
        assert notice["kind"] == "parse_error"
        assert "parse failed" in notice["error"]


class TestStartupMigration:
    def test_lifespan_migrates_a_config_file(self, tmp_path):
        src = tmp_path / "config.json"
        src.write_text(json.dumps({
            "providers": {"claude-x": {"type": "claude", "mode": "push", "label": "x"}},
            "push_auth_secret": "s3cret",
        }), encoding="utf-8")
        db = tmp_path / "usage.db"
        web._config_path = str(src)
        web._usage_db_path = db
        web._v2_manager = None
        web._config_service = None

        with TestClient(web.app) as c:
            assert c.get("/api/notices").json()["migration"]["status"] == "pending"
            status = c.get("/api/status/v2").json()
            assert status["enabled"] is True

        assert not src.exists()
        assert (tmp_path / "config.json.bak").exists()
        with closing(sqlite3.connect(db)) as conn:
            assert [r.instance_id for r in config_store.list_instances(conn)] == ["claude-x"]
            assert config_store.get_push_auth(conn) == ("s3cret", False)
        web._v2_manager = None
        web._config_service = None
