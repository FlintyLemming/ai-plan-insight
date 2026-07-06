import sqlite3
from datetime import datetime, timedelta

from ai_plan_insight import usage_store
from ai_plan_insight.usage_store import UTC8


def test_parse_flexible_number_handles_currency_and_commas():
    assert usage_store._parse_flexible_number("45.50") == 45.50
    assert usage_store._parse_flexible_number("¥1,234.56") == 1234.56
    assert usage_store._parse_flexible_number("$ 99") == 99.0
    assert usage_store._parse_flexible_number("Pro") is None
    assert usage_store._parse_flexible_number("") is None


def test_parse_limit_used_parses_floats_and_survives_garbage():
    assert usage_store._parse_limit_used("12.5") == 12.5
    assert usage_store._parse_limit_used("100%") is None
    assert usage_store._parse_limit_used("") is None


from ai_plan_insight.api_schemas import (
    UsageResponse,
    LimitResponse,
    TokenUsageResponse,
    ModelStatResponse,
    HistoryUsagePeriodResponse,
    HistoryModelUsageResponse,
)


def test_record_snapshot_persists_items(tmp_path):
    conn = _connect(tmp_path)
    usage = UsageResponse(
        provider="Kimi Coding Plan",
        user_id="u-1",
        membership_level="Pro",
        limits=[
            LimitResponse(
                duration=1,
                time_unit="小时",
                limit="100",
                used="12.5",
                remaining="87.5",
                reset_time="2026-07-07T00:00:00+08:00",
            )
        ],
        balances={"余额": "¥45.50"},
        token_usage=[TokenUsageResponse(period="2026-07", total_tokens=1000, total_calls=5)],
        model_stats=[ModelStatResponse(model="kimi-k2", total_tokens=500, requests=3)],
        history_usage=HistoryUsagePeriodResponse(
            period="30d",
            granularity="1d",
            x_time=["2026-07-01"],
            tokens_usage=[100],
            model_call_count=[1],
            total_tokens=100,
            total_calls=1,
            models=[
                HistoryModelUsageResponse(
                    model_name="kimi-k2", total_tokens=100, total_calls=1, tokens_usage=[100]
                )
            ],
        ),
    )
    snapshot_id = usage_store.record_snapshot(conn, usage, source_kind="fetch")
    conn.commit()

    row = conn.execute(
        "SELECT provider, source_kind, user_id, membership_level, raw_json "
        "FROM provider_snapshot WHERE snapshot_id = ?",
        (snapshot_id,),
    ).fetchone()
    assert row[0] == "Kimi Coding Plan"
    assert row[1] == "fetch"
    assert row[2] == "u-1"
    assert row[3] == "Pro"
    assert '"provider":"Kimi Coding Plan"' in row[4]

    items = conn.execute(
        "SELECT item_kind, name, value_text, value_number, unit, reset_time "
        "FROM provider_item WHERE snapshot_id = ? ORDER BY item_id",
        (snapshot_id,),
    ).fetchall()
    assert len(items) == 5
    assert items[0] == ("limit", "小时", "12.5", 12.5, "小时", "2026-07-07T00:00:00+08:00")
    assert items[1] == ("balance", "余额", "¥45.50", 45.5, None, None)
    assert items[2] == ("token_usage", "2026-07", "1000", 1000.0, "tokens", None)
    assert items[3] == ("model_stat", "kimi-k2", "500", 500.0, "tokens", None)
    assert items[4][0] == "history_usage"
    assert items[4][3] == 100.0
    assert items[4][4] == "30d/1d"
    conn.close()


def test_record_snapshot_survives_unparseable_values_and_empty_lists(tmp_path):
    conn = _connect(tmp_path)
    usage = UsageResponse(
        provider="Cursor",
        limits=[
            LimitResponse(
                duration=1,
                time_unit="API 用量",
                limit="100",
                used="100%",  # unparseable
                remaining="0",
            )
        ],
        balances={"membership": "Pro"},  # unparseable
    )
    snapshot_id = usage_store.record_snapshot(conn, usage, source_kind="push")
    conn.commit()

    limit = conn.execute(
        "SELECT value_text, value_number FROM provider_item "
        "WHERE snapshot_id = ? AND item_kind = 'limit'",
        (snapshot_id,),
    ).fetchone()
    assert limit == ("100%", None)

    balance = conn.execute(
        "SELECT value_text, value_number FROM provider_item "
        "WHERE snapshot_id = ? AND item_kind = 'balance'",
        (snapshot_id,),
    ).fetchone()
    assert balance == ("Pro", None)

    # Snapshot is still written even though no items were produced from empty collections
    empty_usage = UsageResponse(provider="Antigravity")
    empty_id = usage_store.record_snapshot(conn, empty_usage, source_kind="push")
    conn.commit()
    row = conn.execute(
        "SELECT provider FROM provider_snapshot WHERE snapshot_id = ?", (empty_id,)
    ).fetchone()
    assert row[0] == "Antigravity"
    item_count = conn.execute(
        "SELECT COUNT(*) FROM provider_item WHERE snapshot_id = ?", (empty_id,)
    ).fetchone()[0]
    assert item_count == 0
    conn.close()


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
