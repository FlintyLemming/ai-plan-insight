import sqlite3
from datetime import datetime, timedelta

from ai_plan_insight import usage_store
from ai_plan_insight.usage_store import UTC8


TODAY = datetime.now(UTC8).date().isoformat()
YESTERDAY = (datetime.now(UTC8).date() - timedelta(days=1)).isoformat()
TWO_DAYS_AGO = (datetime.now(UTC8).date() - timedelta(days=2)).isoformat()
TOMORROW = (datetime.now(UTC8).date() + timedelta(days=1)).isoformat()


def _connect(tmp_path):
    """Fresh DB + schema, returned as an open connection."""
    conn = sqlite3.connect(tmp_path / "test.db")
    usage_store.init_schema(conn)
    return conn


def test_upsert_is_idempotent_for_same_key(tmp_path):
    """Re-posting the same (date, source_id, model_id) overwrites, not adds."""
    conn = _connect(tmp_path)
    points = [{"date": TODAY, "model_id": "glm-5.2", "input_tokens": 100, "output_tokens": 50}]

    assert usage_store.upsert_points(conn, "m1", None, points) == (1, 0)
    assert usage_store.upsert_points(conn, "m1", None, points) == (1, 0)  # overwrite

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


def test_cross_source_sum_aggregates_by_model_and_date(tmp_path):
    conn = _connect(tmp_path)
    usage_store.upsert_points(conn, "m1", None, [
        {"date": TODAY, "model_id": "glm-5.2", "input_tokens": 100, "output_tokens": 0},
    ])
    usage_store.upsert_points(conn, "m2", None, [
        {"date": TODAY, "model_id": "glm-5.2", "input_tokens": 200, "output_tokens": 0},
    ])
    rows = usage_store.query_timeseries(conn, 7, {})
    assert len(rows) == 1
    assert rows[0].label == "glm-5.2"
    assert rows[0].total == 300  # summed across two sources
    assert rows[0].input_tokens == 300
    conn.close()


def test_alias_collapses_multiple_raw_ids_into_one_label(tmp_path):
    conn = _connect(tmp_path)
    usage_store.upsert_points(conn, "m1", None, [
        {"date": TODAY, "model_id": "glm-5.2", "input_tokens": 100, "output_tokens": 0},
        {"date": TODAY, "model_id": "glm5.2", "input_tokens": 50, "output_tokens": 0},
    ])
    alias = {"glm-5.2": "GLM 5.2", "glm5.2": "GLM 5.2"}
    rows = usage_store.query_timeseries(conn, 7, alias)
    assert len(rows) == 1
    assert rows[0].label == "GLM 5.2"
    assert rows[0].total == 150
    assert rows[0].raw_ids == ["glm-5.2", "glm5.2"]
    conn.close()


def test_alias_unknown_raw_id_maps_to_itself(tmp_path):
    conn = _connect(tmp_path)
    usage_store.upsert_points(conn, "m1", None, [
        {"date": TODAY, "model_id": "glm-5.2", "input_tokens": 10, "output_tokens": 0},
        {"date": TODAY, "model_id": "claude-brand-new", "input_tokens": 5, "output_tokens": 0},
    ])
    rows = usage_store.query_timeseries(conn, 7, {"glm-5.2": "GLM 5.2"})
    labels = {r.label for r in rows}
    assert labels == {"GLM 5.2", "claude-brand-new"}  # unknown stays raw
    conn.close()


def test_alias_merged_row_retains_per_raw_id_breakdown(tmp_path):
    """Merged rows keep each raw id's token tuple so callers can price per raw
    model (each raw id has its own price) instead of the alias display name."""
    conn = _connect(tmp_path)
    usage_store.upsert_points(conn, "m1", None, [
        {"date": TODAY, "model_id": "glm-5.2", "input_tokens": 10, "output_tokens": 5,
         "cache_read_tokens": 2, "cache_write_tokens": 1, "reasoning_tokens": 3},
    ])
    usage_store.upsert_points(conn, "m2", None, [
        {"date": TODAY, "model_id": "z-ai/glm-5.2", "input_tokens": 7, "output_tokens": 1},
        # a second point for the same raw id on the same date must accumulate
        {"date": YESTERDAY, "model_id": "z-ai/glm-5.2", "input_tokens": 4, "output_tokens": 0},
    ])
    alias = {"glm-5.2": "GLM 5.2", "z-ai/glm-5.2": "GLM 5.2"}
    rows = usage_store.query_timeseries(conn, 7, alias)
    by_date = {r.date: r for r in rows}
    assert by_date[TODAY].raw_breakdown == {
        "glm-5.2": (10, 5, 2, 1, 3),
        "z-ai/glm-5.2": (7, 1, 0, 0, 0),
    }
    assert by_date[YESTERDAY].raw_breakdown == {"z-ai/glm-5.2": (4, 0, 0, 0, 0)}
    # un-merged rows carry their single raw id too
    rows2 = usage_store.query_timeseries(conn, 7, {})
    own = next(r for r in rows2 if r.label == "glm-5.2")
    assert own.raw_breakdown == {"glm-5.2": (10, 5, 2, 1, 3)}
    conn.close()


def test_query_ignores_points_outside_window(tmp_path):
    conn = _connect(tmp_path)
    usage_store.upsert_points(conn, "m1", None, [
        {"date": "2020-01-01", "model_id": "glm-5.2", "input_tokens": 9999, "output_tokens": 0},  # far past
        {"date": TODAY, "model_id": "glm-5.2", "input_tokens": 10, "output_tokens": 0},
    ])
    rows = usage_store.query_timeseries(conn, 7, {})
    assert len(rows) == 1
    assert rows[0].total == 10  # the 2020 row is outside the 7-day window
    conn.close()


def _point(d, model="glm-5.2", inp=10, out=5):
    return {"date": d, "model_id": model, "input_tokens": inp, "output_tokens": out}


def test_freeze_drops_points_before_watermark(tmp_path):
    """Once a payload with reported_at=TODAY lands, earlier days are immutable."""
    conn = _connect(tmp_path)
    usage_store.upsert_points(
        conn, "m1", None, [_point(YESTERDAY, inp=100, out=0)], reported_at=TODAY,
    )
    # local data corrupted: yesterday now claims a different value
    written, dropped = usage_store.upsert_points(
        conn, "m1", None, [_point(YESTERDAY, inp=1, out=0)], reported_at=TODAY,
    )
    assert (written, dropped) == (0, 1)
    rows = usage_store.query_timeseries(conn, 7, {})
    assert rows[0].input_tokens == 100  # first value kept
    conn.close()


def test_freeze_payload_applies_before_watermark_advances(tmp_path):
    """The first payload of a new day still delivers yesterday's final numbers."""
    conn = _connect(tmp_path)
    # yesterday's intraday run: reported_at was yesterday, so yesterday stays open
    usage_store.upsert_points(
        conn, "m1", None, [_point(YESTERDAY, inp=100, out=0)], reported_at=YESTERDAY,
    )
    # today's first run carries yesterday's final total and freezes it
    written, dropped = usage_store.upsert_points(
        conn, "m1", None, [_point(YESTERDAY, inp=150, out=0)], reported_at=TODAY,
    )
    assert (written, dropped) == (1, 0)
    rows = usage_store.query_timeseries(conn, 7, {})
    assert rows[0].input_tokens == 150
    conn.close()


def test_freeze_is_per_source(tmp_path):
    """m1 freezing yesterday must not block m2 from reporting yesterday."""
    conn = _connect(tmp_path)
    usage_store.upsert_points(
        conn, "m1", None, [_point(YESTERDAY, inp=100, out=0)], reported_at=TODAY,
    )
    written, dropped = usage_store.upsert_points(
        conn, "m2", None, [_point(YESTERDAY, inp=200, out=0)], reported_at=YESTERDAY,
    )
    assert (written, dropped) == (1, 0)
    rows = usage_store.query_timeseries(conn, 7, {})
    assert rows[0].input_tokens == 300
    conn.close()


def test_freeze_watermark_never_regresses(tmp_path):
    """A replayed pending payload with an older reported_at cannot unfreeze days."""
    conn = _connect(tmp_path)
    usage_store.upsert_points(conn, "m1", None, [], reported_at=TODAY)
    # replayed pending from two days ago tries to write two days ago
    written, dropped = usage_store.upsert_points(
        conn, "m1", None, [_point(TWO_DAYS_AGO, inp=1, out=0)], reported_at=TWO_DAYS_AGO,
    )
    assert (written, dropped) == (0, 1)
    row = conn.execute(
        "SELECT frozen_before FROM source WHERE source_id = 'm1'"
    ).fetchone()
    assert row[0] == TODAY
    conn.close()


def test_freeze_reported_at_clamped_to_today(tmp_path):
    """A skewed clock sending a future reported_at must not freeze today."""
    conn = _connect(tmp_path)
    usage_store.upsert_points(
        conn, "m1", None, [_point(TODAY, inp=1, out=0)], reported_at=TOMORROW,
    )
    written, dropped = usage_store.upsert_points(
        conn, "m1", None, [_point(TODAY, inp=2, out=0)], reported_at=TODAY,
    )
    assert (written, dropped) == (1, 0)  # today still writable
    rows = usage_store.query_timeseries(conn, 7, {})
    assert rows[0].input_tokens == 2
    conn.close()


def test_no_reported_at_respects_existing_freeze_without_advancing(tmp_path):
    """Old agents without reported_at can't write frozen days, nor freeze new ones."""
    conn = _connect(tmp_path)
    usage_store.upsert_points(conn, "m1", None, [], reported_at=TODAY)
    written, dropped = usage_store.upsert_points(
        conn, "m1", None, [_point(YESTERDAY), _point(TODAY)],
    )
    assert (written, dropped) == (1, 1)  # yesterday frozen, today accepted
    row = conn.execute(
        "SELECT frozen_before FROM source WHERE source_id = 'm1'"
    ).fetchone()
    assert row[0] == TODAY  # unchanged
    conn.close()


def test_mutable_day_is_replaced_wholesale(tmp_path):
    """A model that vanished from a mutable day locally also vanishes here."""
    conn = _connect(tmp_path)
    usage_store.upsert_points(conn, "m1", None, [
        _point(TODAY, model="glm-5.2", inp=100),
        _point(TODAY, model="kimi-k2", inp=50),
    ], reported_at=TODAY)
    usage_store.upsert_points(conn, "m1", None, [
        _point(TODAY, model="glm-5.2", inp=120),
    ], reported_at=TODAY)
    rows = usage_store.query_timeseries(conn, 7, {})
    assert [(r.label, r.input_tokens) for r in rows] == [("glm-5.2", 120)]
    conn.close()


def test_day_absent_from_payload_is_left_untouched(tmp_path):
    """Wholesale replace only applies to dates the payload covers."""
    conn = _connect(tmp_path)
    usage_store.upsert_points(
        conn, "m1", None, [_point(YESTERDAY, inp=100)], reported_at=YESTERDAY,
    )
    usage_store.upsert_points(
        conn, "m1", None, [_point(TODAY, inp=5)], reported_at=YESTERDAY,
    )
    rows = usage_store.query_timeseries(conn, 7, {})
    assert {(r.date, r.input_tokens) for r in rows} == {(YESTERDAY, 100), (TODAY, 5)}
    conn.close()


def test_init_schema_migrates_old_source_table(tmp_path):
    """A DB created before frozen_before existed gains the column in place."""
    db = tmp_path / "old.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE source (source_id TEXT PRIMARY KEY, label TEXT, last_seen TEXT)"
    )
    conn.execute("INSERT INTO source VALUES ('m1', 'old', '2026-01-01')")
    conn.commit()
    usage_store.init_schema(conn)
    row = conn.execute(
        "SELECT frozen_before FROM source WHERE source_id = 'm1'"
    ).fetchone()
    assert row[0] is None
    # and the freeze machinery works on the migrated table
    usage_store.upsert_points(conn, "m1", None, [_point(TODAY)], reported_at=TODAY)
    conn.close()


def test_init_schema_adds_token_breakdown_columns_to_old_usage_point(tmp_path):
    """A usage_point table from before cache/reasoning columns is upgraded in place."""
    db = tmp_path / "old.db"
    conn = sqlite3.connect(db)
    # pre-cache/reasoning schema: only input + output + updated_at
    conn.execute(
        "CREATE TABLE usage_point ("
        " date TEXT NOT NULL, source_id TEXT NOT NULL, model_id TEXT NOT NULL,"
        " input_tokens INTEGER NOT NULL, output_tokens INTEGER NOT NULL,"
        " updated_at TEXT NOT NULL,"
        " PRIMARY KEY (date, source_id, model_id))"
    )
    conn.execute(
        "INSERT INTO usage_point VALUES (?, 'm1', 'glm-5.2', 100, 50, '2026-01-01')",
        (TODAY,),
    )
    conn.commit()
    usage_store.init_schema(conn)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(usage_point)")}
    assert {"cache_read_tokens", "cache_write_tokens", "reasoning_tokens"} <= cols
    # the pre-existing row is intact, and the new columns defaulted to 0
    rows = usage_store.query_timeseries(conn, 7, {})
    assert len(rows) == 1
    assert rows[0].input_tokens == 100
    assert rows[0].output_tokens == 50
    assert rows[0].cache_read_tokens == 0
    assert rows[0].cache_write_tokens == 0
    assert rows[0].reasoning_tokens == 0
    conn.close()


def test_upsert_persists_token_breakdown(tmp_path):
    conn = _connect(tmp_path)
    usage_store.upsert_points(conn, "m1", None, [
        {"date": TODAY, "model_id": "glm-5.2", "input_tokens": 100, "output_tokens": 50,
         "cache_read_tokens": 80, "cache_write_tokens": 20, "reasoning_tokens": 5},
    ])
    rows = usage_store.query_timeseries(conn, 7, {})
    assert len(rows) == 1
    r = rows[0]
    assert r.input_tokens == 100
    assert r.output_tokens == 50
    assert r.cache_read_tokens == 80
    assert r.cache_write_tokens == 20
    assert r.reasoning_tokens == 5
    conn.close()


def test_total_is_sum_of_five_categories(tmp_path):
    """total = input + output + cache_read + cache_write + reasoning (matches tokscale)."""
    conn = _connect(tmp_path)
    usage_store.upsert_points(conn, "m1", None, [
        {"date": TODAY, "model_id": "glm-5.2", "input_tokens": 100, "output_tokens": 50,
         "cache_read_tokens": 80, "cache_write_tokens": 20, "reasoning_tokens": 5},
    ])
    rows = usage_store.query_timeseries(conn, 7, {})
    assert rows[0].total == 255  # 100+50+80+20+5, NOT 150
    conn.close()


def test_cache_reasoning_default_to_zero_for_legacy_payload(tmp_path):
    """Old reporters that omit cache/reasoning still work; those columns read as 0."""
    conn = _connect(tmp_path)
    usage_store.upsert_points(conn, "m1", None, [
        {"date": TODAY, "model_id": "glm-5.2", "input_tokens": 100, "output_tokens": 50},
    ])
    rows = usage_store.query_timeseries(conn, 7, {})
    r = rows[0]
    assert r.cache_read_tokens == 0
    assert r.cache_write_tokens == 0
    assert r.reasoning_tokens == 0
    assert r.total == 150
    conn.close()


def test_cross_source_sum_includes_cache_reasoning(tmp_path):
    conn = _connect(tmp_path)
    usage_store.upsert_points(conn, "m1", None, [
        {"date": TODAY, "model_id": "glm-5.2", "input_tokens": 100, "output_tokens": 0,
         "cache_read_tokens": 1000, "cache_write_tokens": 0, "reasoning_tokens": 0},
    ])
    usage_store.upsert_points(conn, "m2", None, [
        {"date": TODAY, "model_id": "glm-5.2", "input_tokens": 200, "output_tokens": 0,
         "cache_read_tokens": 2000, "cache_write_tokens": 0, "reasoning_tokens": 0},
    ])
    rows = usage_store.query_timeseries(conn, 7, {})
    assert rows[0].cache_read_tokens == 3000  # summed across sources
    assert rows[0].total == 3300  # 300 + 3000
    conn.close()




def test_list_raw_model_usage_sums_all_five_token_kinds(tmp_path):
    conn = _connect(tmp_path)
    usage_store.upsert_points(conn, "m1", None, [{
        "date": TODAY, "model_id": "cursor-grok", "input_tokens": 1,
        "output_tokens": 2, "cache_read_tokens": 4,
        "cache_write_tokens": 8, "reasoning_tokens": 16,
    }])
    assert usage_store.list_raw_model_usage(conn, 90) == [("cursor-grok", 31)]
    conn.close()


def test_list_raw_model_usage_sums_across_sources_and_days(tmp_path):
    conn = _connect(tmp_path)
    usage_store.upsert_points(conn, "m1", None, [
        {"date": TODAY, "model_id": "a", "input_tokens": 10, "output_tokens": 0}])
    usage_store.upsert_points(conn, "m2", None, [
        {"date": YESTERDAY, "model_id": "a", "input_tokens": 5, "output_tokens": 0}])
    assert usage_store.list_raw_model_usage(conn, 90) == [("a", 15)]
    conn.close()


def test_list_raw_model_usage_is_ordered_by_total_desc(tmp_path):
    conn = _connect(tmp_path)
    usage_store.upsert_points(conn, "m1", None, [
        {"date": TODAY, "model_id": "small", "input_tokens": 1, "output_tokens": 0},
        {"date": TODAY, "model_id": "big", "input_tokens": 100, "output_tokens": 0},
    ])
    assert [m for m, _ in usage_store.list_raw_model_usage(conn, 90)] == ["big", "small"]
    conn.close()


def test_list_raw_model_usage_keeps_raw_ids_unaliased(tmp_path):
    """Pricing is per raw id, so this query must never merge aliases."""
    conn = _connect(tmp_path)
    usage_store.upsert_points(conn, "m1", None, [
        {"date": TODAY, "model_id": "glm-5.2", "input_tokens": 1, "output_tokens": 0},
        {"date": TODAY, "model_id": "z-ai/glm-5.2", "input_tokens": 1, "output_tokens": 0},
    ])
    assert len(usage_store.list_raw_model_usage(conn, 90)) == 2
    conn.close()


def test_list_raw_model_usage_excludes_zero_token_models(tmp_path):
    conn = _connect(tmp_path)
    usage_store.upsert_points(conn, "m1", None, [
        {"date": TODAY, "model_id": "idle", "input_tokens": 0, "output_tokens": 0}])
    assert usage_store.list_raw_model_usage(conn, 90) == []
    conn.close()


def test_list_raw_model_usage_respects_the_window(tmp_path):
    conn = _connect(tmp_path)
    old = (datetime.now(UTC8).date() - timedelta(days=100)).isoformat()
    usage_store.upsert_points(conn, "m1", None, [
        {"date": old, "model_id": "ancient", "input_tokens": 5, "output_tokens": 0}])
    assert usage_store.list_raw_model_usage(conn, 90) == []
    conn.close()


def test_list_raw_model_usage_empty_db(tmp_path):
    conn = _connect(tmp_path)
    assert usage_store.list_raw_model_usage(conn, 90) == []
    conn.close()


def test_init_schema_adds_last_ip_column_to_old_source_table(tmp_path):
    db = tmp_path / "old_ip.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE source (source_id TEXT PRIMARY KEY, label TEXT, last_seen TEXT)"
    )
    conn.execute("INSERT INTO source VALUES ('m1', 'old', '2026-01-01T00:00:00+08:00')")
    conn.commit()
    usage_store.init_schema(conn)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(source)")}
    assert "last_ip" in cols
    assert conn.execute(
        "SELECT last_ip FROM source WHERE source_id = 'm1'"
    ).fetchone()[0] is None
    conn.close()


def test_upsert_records_and_preserves_last_ip(tmp_path):
    conn = _connect(tmp_path)
    usage_store.upsert_points(conn, "m1", "Laptop", [], last_ip="192.168.1.10")
    assert conn.execute("SELECT last_ip FROM source WHERE source_id='m1'").fetchone()[0] == "192.168.1.10"
    # None keeps existing IP
    usage_store.upsert_points(conn, "m1", None, [], last_ip=None)
    assert conn.execute("SELECT last_ip FROM source WHERE source_id='m1'").fetchone()[0] == "192.168.1.10"
    # New IP updates it
    usage_store.upsert_points(conn, "m1", None, [], last_ip="10.0.0.5")
    assert conn.execute("SELECT last_ip FROM source WHERE source_id='m1'").fetchone()[0] == "10.0.0.5"
    conn.close()


def test_restore_stale_source_clears_dismissed_flag(tmp_path):
    conn = _connect(tmp_path)
    now = datetime(2026, 7, 15, 12, 0, 0, tzinfo=UTC8)
    usage_store.upsert_points(
        conn, "dev-a", "Dev A", [], now=(now - timedelta(days=3)).isoformat()
    )
    assert usage_store.dismiss_stale_source(conn, "dev-a", now.isoformat()) is True
    assert usage_store.get_stale_sources(conn, now=now) == []
    assert usage_store.restore_stale_source(conn, "dev-a") is True
    assert [s["source_id"] for s in usage_store.get_stale_sources(conn, now=now)] == ["dev-a"]
    assert usage_store.restore_stale_source(conn, "ghost") is False
    conn.close()


def test_list_usage_sources_excludes_stubs_and_computes_stats(tmp_path):
    conn = _connect(tmp_path)
    ref = datetime(2026, 7, 30, 12, 0, 0, tzinfo=UTC8)
    today = ref.date().isoformat()
    d5 = (ref.date() - timedelta(days=5)).isoformat()
    d20 = (ref.date() - timedelta(days=20)).isoformat()
    d40 = (ref.date() - timedelta(days=40)).isoformat()

    # Push-card stub row (last_seen IS NULL) must be excluded.
    conn.execute("INSERT INTO source (source_id, auth_valid) VALUES ('claude-card', 1)")

    # Active machine with usage across today, 7d, 30d, and >30d
    usage_store.upsert_points(
        conn, "macbook", "MacBook Pro",
        [
            {"date": d40, "model_id": "glm-5.2", "input_tokens": 1000, "output_tokens": 500},
            {"date": d20, "model_id": "z-ai/glm-5.2", "input_tokens": 200, "output_tokens": 100,
             "cache_read_tokens": 50, "cache_write_tokens": 25, "reasoning_tokens": 25},
            {"date": d5, "model_id": "claude-sonnet", "input_tokens": 300, "output_tokens": 200},
            {"date": today, "model_id": "glm-5.2", "input_tokens": 60, "output_tokens": 40},
        ],
        reported_at=today,
        now=(ref - timedelta(minutes=10)).isoformat(),
        last_ip="192.168.1.20",
    )
    usage_store.update_source_auth(conn, "macbook", True, (ref - timedelta(minutes=10)).isoformat())

    # Stale & dismissed machine with zero points
    usage_store.upsert_points(
        conn, "old-box", "Old Box", [],
        now=(ref - timedelta(hours=36)).isoformat(),
        last_ip="10.0.0.2",
    )
    usage_store.update_source_auth(conn, "old-box", False, (ref - timedelta(hours=36)).isoformat())
    usage_store.dismiss_stale_source(conn, "old-box", (ref - timedelta(hours=1)).isoformat())

    # Malformed last_seen row should not crash and sorts last
    conn.execute("INSERT INTO source (source_id, last_seen) VALUES ('bad-ts', 'not-a-date')")
    conn.commit()

    aliases = {"glm-5.2": "GLM 5.2", "z-ai/glm-5.2": "GLM 5.2"}
    rows = usage_store.list_usage_sources(conn, alias_lookup=aliases, now=ref)
    assert [r["source_id"] for r in rows] == ["macbook", "old-box", "bad-ts"]

    mac = rows[0]
    assert mac["label"] == "MacBook Pro"
    assert mac["is_stale"] is False
    assert mac["stale_dismissed"] is False
    assert mac["auth_valid"] is True
    assert mac["last_ip"] == "192.168.1.20"
    assert mac["first_date"] == d40
    assert mac["last_date"] == today
    assert mac["active_days"] == 4
    assert mac["model_count"] == 3  # raw distinct model_ids
    assert mac["tokens_today"] == 100
    assert mac["tokens_7d"] == 600   # today (100) + d5 (500)
    assert mac["tokens_30d"] == 1000  # today (100) + d5 (500) + d20 (400)
    assert mac["total_tokens"] == 2500  # + d40 (1500)
    # Alias collapses glm-5.2 (1500+100) + z-ai/glm-5.2 (400) = 2000
    assert mac["top_models"] == [
        {"label": "GLM 5.2", "tokens": 2000},
        {"label": "claude-sonnet", "tokens": 500},
    ]

    old = rows[1]
    assert old["is_stale"] is True
    assert old["stale_dismissed"] is True
    assert old["stale_dismissed_at"] is not None
    assert old["auth_valid"] is False
    assert old["active_days"] == 0
    assert old["first_date"] is None
    assert old["top_models"] == []

    bad = rows[2]
    assert bad["is_stale"] is False
    conn.close()
