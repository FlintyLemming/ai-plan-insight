import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from ai_plan_insight import usage_store, web
from ai_plan_insight.config import Config


def test_startup_creates_all_tables(tmp_path, monkeypatch):
    """Deleting the usage DB and starting the app recreates every table."""
    db = tmp_path / "usage.db"
    monkeypatch.setattr(web, "_usage_db_path", db)
    monkeypatch.setattr(web, "load_config", lambda _=None: Config(providers={}))
    web._pushed_results.clear()
    web._pushed_at.clear()

    # Trigger the lifespan (which calls init_db)
    with TestClient(web.app):
        pass

    assert db.exists()
    conn = sqlite3.connect(db)
    try:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        assert tables >= {
            "usage_point",
            "source",
            "provider_snapshot",
            "provider_item",
            "push_card_snapshot",
        }

        indexes = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        ).fetchall()}
        assert indexes >= {
            "idx_provider_item_snapshot",
            "idx_provider_item_query",
            "idx_snapshot_provider_time",
        }
    finally:
        conn.close()
