# tests/test_provider_v2_store.py
import json
import sqlite3
import pytest
from datetime import datetime
from pathlib import Path
from ai_plan_insight.api_schemas import UsageResponse, LimitResponse
from ai_plan_insight import provider_v2_store


@pytest.fixture
def db(tmp_path: Path) -> sqlite3.Connection:
    p = tmp_path / "test_v2.db"
    conn = sqlite3.connect(p)
    provider_v2_store.init_v2_schema(conn)
    conn.commit()
    yield conn
    conn.close()


def _make_usage(**overrides) -> UsageResponse:
    defaults = {
        "provider": "Claude 订阅 · 工作号",
        "limits": [
            LimitResponse(
                duration=5, time_unit="小时", limit="100",
                used="30", remaining="70", reset_time="2026-07-01T15:00:00Z",
            ),
        ],
    }
    defaults.update(overrides)
    return UsageResponse(**defaults)


class TestInitSchema:
    def test_tables_created(self, db: sqlite3.Connection):
        tables = {r[0] for r in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        assert "provider_v2_snapshot" in tables
        assert "provider_v2_item" in tables

    def test_idempotent(self, db: sqlite3.Connection):
        # Calling init again should not fail
        provider_v2_store.init_v2_schema(db)
        db.commit()


class TestUpsertV2Snapshot:
    def test_insert_and_update(self, db: sqlite3.Connection):
        usage = _make_usage()
        provider_v2_store.upsert_v2_snapshot(
            db, "claude-work", "claude", "push", "工作号", usage,
        )
        db.commit()
        rows = db.execute("SELECT * FROM provider_v2_snapshot").fetchall()
        assert len(rows) == 1
        assert rows[0][0] == "claude-work"  # instance_id

        # Update same instance
        usage2 = _make_usage(provider="Claude 订阅 · 工作号 (updated)")
        provider_v2_store.upsert_v2_snapshot(
            db, "claude-work", "claude", "push", "工作号", usage2,
        )
        db.commit()
        rows = db.execute("SELECT * FROM provider_v2_snapshot").fetchall()
        assert len(rows) == 1  # still one row (UPSERT)

    def test_different_instances_independent(self, db: sqlite3.Connection):
        usage1 = _make_usage()
        usage2 = _make_usage(provider="Claude 订阅 · 个人号")
        provider_v2_store.upsert_v2_snapshot(
            db, "claude-work", "claude", "push", "工作号", usage1,
        )
        provider_v2_store.upsert_v2_snapshot(
            db, "claude-personal", "claude", "push", "个人号", usage2,
        )
        db.commit()
        rows = db.execute("SELECT instance_id FROM provider_v2_snapshot ORDER BY instance_id").fetchall()
        assert [r[0] for r in rows] == ["claude-personal", "claude-work"]

    def test_items_replaced_on_upsert(self, db: sqlite3.Connection):
        usage = _make_usage()
        provider_v2_store.upsert_v2_snapshot(
            db, "claude-work", "claude", "push", "工作号", usage,
        )
        db.commit()
        count1 = db.execute("SELECT COUNT(*) FROM provider_v2_item").fetchone()[0]
        assert count1 > 0

        # Upsert again — old items should be gone, new ones inserted
        provider_v2_store.upsert_v2_snapshot(
            db, "claude-work", "claude", "push", "工作号", usage,
        )
        db.commit()
        count2 = db.execute("SELECT COUNT(*) FROM provider_v2_item").fetchone()[0]
        assert count2 == count1


class TestLoadV2Snapshots:
    def test_load_empty(self, db: sqlite3.Connection):
        rows = provider_v2_store.load_v2_snapshots(db, {"claude-work"})
        assert rows == []

    def test_load_matching_instances(self, db: sqlite3.Connection):
        usage = _make_usage()
        provider_v2_store.upsert_v2_snapshot(
            db, "claude-work", "claude", "push", "工作号", usage,
        )
        db.commit()
        rows = provider_v2_store.load_v2_snapshots(
            db, {"claude-work", "claude-personal"},
        )
        assert len(rows) == 1
        instance_id, type_name, mode, label, recorded_at, usage_resp = rows[0]
        assert instance_id == "claude-work"
        assert type_name == "claude"
        assert mode == "push"
        assert isinstance(usage_resp, UsageResponse)

    def test_skip_instance_not_in_config(self, db: sqlite3.Connection):
        usage = _make_usage()
        provider_v2_store.upsert_v2_snapshot(
            db, "old-removed", "claude", "push", "旧号", usage,
        )
        db.commit()
        rows = provider_v2_store.load_v2_snapshots(db, {"claude-work"})
        assert len(rows) == 0

    def test_skip_type_mismatch(self, db: sqlite3.Connection):
        usage = _make_usage()
        provider_v2_store.upsert_v2_snapshot(
            db, "test-inst", "claude", "push", "test", usage,
        )
        db.commit()
        # Request with different type
        rows = provider_v2_store.load_v2_snapshots(
            db, {"test-inst"},
            type_mode_map={"test-inst": ("grok", "push")},
        )
        assert len(rows) == 0

    def test_skip_mode_mismatch(self, db: sqlite3.Connection):
        usage = _make_usage()
        provider_v2_store.upsert_v2_snapshot(
            db, "test-inst", "antigravity", "push", "test", usage,
        )
        db.commit()
        rows = provider_v2_store.load_v2_snapshots(
            db, {"test-inst"},
            type_mode_map={"test-inst": ("antigravity", "fetch")},
        )
        assert len(rows) == 0

    def test_label_from_config_overrides_stored(self, db: sqlite3.Connection):
        usage = _make_usage()
        provider_v2_store.upsert_v2_snapshot(
            db, "claude-work", "claude", "push", "旧标签", usage,
        )
        db.commit()
        rows = provider_v2_store.load_v2_snapshots(
            db, {"claude-work"},
            label_map={"claude-work": "新标签"},
        )
        assert len(rows) == 1
        _, _, _, label, _, _ = rows[0]
        assert label == "新标签"

    def test_corrupt_row_skipped(self, db: sqlite3.Connection):
        # Insert a row with invalid JSON
        db.execute(
            "INSERT INTO provider_v2_snapshot "
            "(instance_id, type, mode, label, recorded_at, raw_json) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("bad-row", "claude", "push", "test", "now", "{bad json"),
        )
        db.commit()
        # Also insert a good row
        usage = _make_usage()
        provider_v2_store.upsert_v2_snapshot(
            db, "good-row", "claude", "push", "test", usage,
        )
        db.commit()
        rows = provider_v2_store.load_v2_snapshots(
            db, {"bad-row", "good-row"},
        )
        assert len(rows) == 1
        assert rows[0][0] == "good-row"
