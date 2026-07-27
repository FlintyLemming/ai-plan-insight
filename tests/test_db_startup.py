import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from ai_plan_insight import web


def test_startup_creates_all_tables(tmp_path, monkeypatch):
    """Deleting the usage DB and starting the app recreates every table."""
    db = tmp_path / "usage.db"
    monkeypatch.setattr(web, "_config_path", str(tmp_path / "config.json"))
    monkeypatch.setattr(web, "_usage_db_path", db)
    web._v2_manager = None
    web._config_service = None

    # Trigger the lifespan (which calls init_db + init_config_db)
    with TestClient(web.app):
        pass

    assert db.exists()
    conn = sqlite3.connect(db)
    try:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        assert tables >= {
            "usage_point", "source",
            "app_setting", "provider_instance", "model_alias", "admin_session",
        }
        assert "provider_snapshot" not in tables
        assert "provider_item" not in tables
        assert "push_card_snapshot" not in tables

        indexes = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        ).fetchall()}
        assert "idx_model_alias_label" in indexes
        assert "idx_provider_item_snapshot" not in indexes
        assert "idx_provider_item_query" not in indexes
        assert "idx_snapshot_provider_time" not in indexes
    finally:
        conn.close()
    web._v2_manager = None
    web._config_service = None
