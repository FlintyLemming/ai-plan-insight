import sqlite3
from datetime import date

from ai_plan_insight import usage_store

TODAY = date.today().isoformat()


def _connect(tmp_path):
    """Fresh DB + schema, returned as an open connection."""
    conn = sqlite3.connect(tmp_path / "test.db")
    usage_store.init_schema(conn)
    return conn


def test_upsert_is_idempotent_for_same_key(tmp_path):
    """Re-posting the same (date, source_id, model_id) overwrites, not adds."""
    conn = _connect(tmp_path)
    points = [{"date": TODAY, "model_id": "glm-5.2", "input_tokens": 100, "output_tokens": 50}]

    assert usage_store.upsert_points(conn, "m1", None, points) == 1
    assert usage_store.upsert_points(conn, "m1", None, points) == 1  # overwrite

    rows = usage_store.query_timeseries(conn, 7, {})
    assert len(rows) == 1
    assert rows[0].total == 150  # not 300
    conn.close()


def test_upsert_updates_tokens_on_overwrite(tmp_path):
    conn = _connect(tmp_path)
    usage_store.upsert_points(conn, "m1", None, [
        {"date": TODAY, "model_id": "glm-5.2", "input_tokens": 100, "output_tokens": 50},
    ])
    usage_store.upsert_points(conn, "m1", None, [
        {"date": TODAY, "model_id": "glm-5.2", "input_tokens": 999, "output_tokens": 1},
    ])
    rows = usage_store.query_timeseries(conn, 7, {})
    assert rows[0].input_tokens == 999
    assert rows[0].output_tokens == 1
    conn.close()


def test_upsert_records_source_label_and_last_seen(tmp_path):
    conn = _connect(tmp_path)
    usage_store.upsert_points(conn, "m1", "MacBook Pro", [
        {"date": TODAY, "model_id": "glm-5.2", "input_tokens": 1, "output_tokens": 1},
    ])
    row = conn.execute("SELECT label FROM source WHERE source_id = 'm1'").fetchone()
    assert row is not None and row[0] == "MacBook Pro"
    conn.close()
