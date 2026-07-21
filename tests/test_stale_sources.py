"""Stale-source alert: store queries + web endpoints.

A source is "stale" when its last_seen is more than 24h old, it has not
been dismissed, and it actually reported usage at least once (last_seen
NOT NULL — push-card stub rows are excluded).
"""
import sqlite3
from datetime import datetime, timedelta

from fastapi.testclient import TestClient

from ai_plan_insight import usage_store, web
from ai_plan_insight.usage_store import UTC8

NOW = datetime(2026, 7, 11, 12, 0, 0, tzinfo=UTC8)


def _connect(tmp_path):
    conn = sqlite3.connect(tmp_path / "test.db")
    usage_store.init_schema(conn)
    return conn


def _report(conn, source_id, at, label=None):
    """Simulate one agent report at time `at` (0 points is fine)."""
    usage_store.upsert_points(
        conn, source_id, label, points=[], now=at.isoformat()
    )
    conn.commit()


def test_stale_dismissed_at_column_added_on_init(tmp_path):
    conn = _connect(tmp_path)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(source)")}
    assert "stale_dismissed_at" in cols


def test_source_silent_over_24h_is_stale(tmp_path):
    conn = _connect(tmp_path)
    _report(conn, "dev-a", NOW - timedelta(hours=25), label="Dev A")
    stale = usage_store.get_stale_sources(conn, now=NOW)
    assert [(s["source_id"], s["label"]) for s in stale] == [("dev-a", "Dev A")]
    assert stale[0]["last_seen"] == (NOW - timedelta(hours=25)).isoformat()


def test_source_seen_within_24h_is_not_stale(tmp_path):
    conn = _connect(tmp_path)
    _report(conn, "dev-a", NOW - timedelta(hours=23))
    assert usage_store.get_stale_sources(conn, now=NOW) == []


def test_stub_row_without_last_seen_is_ignored(tmp_path):
    conn = _connect(tmp_path)
    # Push-card sources only get a stub row (last_seen stays NULL).
    conn.execute("INSERT INTO source (source_id) VALUES ('cursor')")
    conn.commit()
    assert usage_store.get_stale_sources(conn, now=NOW) == []


def test_dismiss_hides_source_and_reports_hit(tmp_path):
    conn = _connect(tmp_path)
    _report(conn, "dev-a", NOW - timedelta(days=3))
    assert usage_store.dismiss_stale_source(conn, "dev-a", NOW.isoformat()) is True
    conn.commit()
    assert usage_store.get_stale_sources(conn, now=NOW) == []


def test_dismiss_unknown_source_reports_miss(tmp_path):
    conn = _connect(tmp_path)
    assert usage_store.dismiss_stale_source(conn, "ghost", NOW.isoformat()) is False


def test_new_report_rearms_dismissed_source(tmp_path):
    conn = _connect(tmp_path)
    _report(conn, "dev-a", NOW - timedelta(days=5))
    usage_store.dismiss_stale_source(conn, "dev-a", NOW.isoformat())
    conn.commit()
    # Device comes back, then goes silent again for >24h.
    _report(conn, "dev-a", NOW - timedelta(days=2))
    stale = usage_store.get_stale_sources(conn, now=NOW)
    assert [s["source_id"] for s in stale] == ["dev-a"]


def test_malformed_last_seen_row_is_skipped(tmp_path):
    conn = _connect(tmp_path)
    conn.execute(
        "INSERT INTO source (source_id, last_seen) VALUES ('bad', 'not-a-date')"
    )
    _report(conn, "dev-a", NOW - timedelta(hours=30))
    stale = usage_store.get_stale_sources(conn, now=NOW)
    assert [s["source_id"] for s in stale] == ["dev-a"]


# --- web endpoints ---


def _setup_web(tmp_path, monkeypatch):
    db = tmp_path / "usage.db"
    monkeypatch.setattr(web, "_usage_db_path", db)
    usage_store.init_db(db)
    return sqlite3.connect(db)


def test_get_stale_sources_endpoint(tmp_path, monkeypatch):
    conn = _setup_web(tmp_path, monkeypatch)
    _report(conn, "dev-a", datetime.now(UTC8) - timedelta(hours=30), label="Dev A")
    _report(conn, "dev-b", datetime.now(UTC8))
    conn.close()
    client = TestClient(web.app)
    resp = client.get("/api/sources/stale")
    assert resp.status_code == 200
    body = resp.json()
    assert [s["source_id"] for s in body] == ["dev-a"]
    assert body[0]["label"] == "Dev A"
    assert body[0]["last_seen"]


def test_dismiss_stale_endpoint_then_hidden(tmp_path, monkeypatch):
    conn = _setup_web(tmp_path, monkeypatch)
    _report(conn, "dev-a", datetime.now(UTC8) - timedelta(hours=30))
    conn.close()
    client = TestClient(web.app)
    resp = client.post("/api/sources/dev-a/dismiss-stale")
    assert resp.status_code == 200
    assert client.get("/api/sources/stale").json() == []


def test_dismiss_unknown_source_returns_404(tmp_path, monkeypatch):
    conn = _setup_web(tmp_path, monkeypatch)
    conn.close()
    client = TestClient(web.app)
    resp = client.post("/api/sources/ghost/dismiss-stale")
    assert resp.status_code == 404


# --- frontend structure ---

from pathlib import Path

INDEX_HTML = Path(__file__).resolve().parents[1] / "ai_plan_insight" / "index.html"


def test_index_has_stale_alert_container_below_usage_table():
    h = INDEX_HTML.read_text(encoding="utf-8")
    assert 'id="stale-sources-container"' in h
    # Must sit below the usage table inside the usage view.
    assert h.index('id="usage-table-container"') < h.index('id="stale-sources-container"')


def test_index_wires_stale_alert_fetch_and_dismiss():
    h = INDEX_HTML.read_text(encoding="utf-8")
    assert "/api/sources/stale" in h
    assert "dismiss-stale" in h
    assert "不再提示" in h
    assert "未上传数据" in h
