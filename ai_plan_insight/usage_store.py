"""SQLite-backed store for per-model token usage points.

All functions take an already-open `sqlite3.Connection`; the web layer owns
the DB path and opens a fresh connection per request. Storage always keeps
the raw `model_id`; aliasing to canonical labels happens at read time.

All dates are UTC+8 calendar days, matching the reporter agents.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

UTC8 = timezone(timedelta(hours=8))


_CREATE_USAGE_POINT = """
CREATE TABLE IF NOT EXISTS usage_point (
    date          TEXT NOT NULL,
    source_id     TEXT NOT NULL,
    model_id      TEXT NOT NULL,
    input_tokens  INTEGER NOT NULL,
    output_tokens INTEGER NOT NULL,
    updated_at    TEXT NOT NULL,
    PRIMARY KEY (date, source_id, model_id)
)
"""

_CREATE_SOURCE = """
CREATE TABLE IF NOT EXISTS source (
    source_id     TEXT PRIMARY KEY,
    label         TEXT,
    last_seen     TEXT,
    frozen_before TEXT
)
"""


def init_schema(conn: sqlite3.Connection) -> None:
    """Create tables (idempotent) and enable WAL."""
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(_CREATE_USAGE_POINT)
    conn.execute(_CREATE_SOURCE)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(source)")}
    if "frozen_before" not in cols:
        conn.execute("ALTER TABLE source ADD COLUMN frozen_before TEXT")
    conn.commit()


def init_db(path: Path) -> None:
    """Open `path`, create the schema, close. Called once at startup."""
    conn = sqlite3.connect(path)
    try:
        init_schema(conn)
    finally:
        conn.close()


def upsert_points(
    conn: sqlite3.Connection,
    source_id: str,
    source_label: str | None,
    points: Iterable[Mapping[str, Any]],
    reported_at: str | None = None,
    now: str | None = None,
) -> tuple[int, int]:
    """Apply one snapshot payload for one source in the caller's transaction.

    `points` items are mappings with keys: date, model_id, input_tokens,
    output_tokens. Per-source freeze rule: points dated before the source's
    `frozen_before` watermark are dropped (those days are immutable). For each
    still-mutable date the payload covers, existing rows are replaced
    wholesale (delete-then-insert), so a model that vanished from a mutable
    day locally also vanishes here. After the payload lands the watermark
    advances to `reported_at` — clamped to today (UTC+8) so a skewed clock
    cannot freeze days that have not happened yet, and never moving backwards
    so a replayed older payload cannot unfreeze anything.

    Returns (written, dropped). The caller is responsible for `conn.commit()`.
    """
    ts = now or datetime.now(UTC8).isoformat()
    row = conn.execute(
        "SELECT frozen_before FROM source WHERE source_id = ?", (source_id,)
    ).fetchone()
    frozen_before = row[0] if row else None

    by_date: dict[str, list[Mapping[str, Any]]] = {}
    dropped = 0
    for p in points:
        # ISO dates compare correctly as strings
        if frozen_before is not None and p["date"] < frozen_before:
            dropped += 1
        else:
            by_date.setdefault(p["date"], []).append(p)

    written = 0
    for d, day_points in by_date.items():
        conn.execute(
            "DELETE FROM usage_point WHERE date = ? AND source_id = ?",
            (d, source_id),
        )
        for p in day_points:
            conn.execute(
                "INSERT INTO usage_point "
                "(date, source_id, model_id, input_tokens, output_tokens, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(date, source_id, model_id) DO UPDATE SET "
                "input_tokens=excluded.input_tokens, "
                "output_tokens=excluded.output_tokens, "
                "updated_at=excluded.updated_at",
                (d, source_id, p["model_id"], p["input_tokens"], p["output_tokens"], ts),
            )
            written += 1

    new_frozen = frozen_before
    if reported_at is not None:
        effective = min(reported_at, datetime.now(UTC8).strftime("%Y-%m-%d"))
        if new_frozen is None or effective > new_frozen:
            new_frozen = effective
    conn.execute(
        "INSERT INTO source (source_id, label, last_seen, frozen_before) "
        "VALUES (?, ?, ?, ?) "
        "ON CONFLICT(source_id) DO UPDATE SET "
        "label=COALESCE(excluded.label, source.label), "
        "last_seen=excluded.last_seen, "
        "frozen_before=excluded.frozen_before",
        (source_id, source_label, ts, new_frozen),
    )
    return written, dropped


@dataclass
class TimeseriesRow:
    """One aggregated (date, canonical label) row, post-aliasing."""
    date: str
    label: str
    raw_ids: list[str]
    input_tokens: int
    output_tokens: int
    total: int


def query_timeseries(
    conn: sqlite3.Connection,
    days: int,
    alias_lookup: dict[str, str],
) -> list[TimeseriesRow]:
    """Return alias-resolved, cross-source SUMmed rows for the last `days` days.

    Storage keeps raw model_ids; here each is mapped through `alias_lookup`
    (defaulting to the raw id itself), then re-aggregated by (date, label)
    so multiple raw ids sharing a canonical label collapse into one row.
    """
    today = datetime.now(UTC8).date()
    start = today - timedelta(days=days - 1)
    cur = conn.execute(
        "SELECT date, model_id, SUM(input_tokens), SUM(output_tokens) "
        "FROM usage_point WHERE date >= ? "
        "GROUP BY date, model_id ORDER BY date, model_id",
        (start.isoformat(),),
    )
    agg: dict[tuple[str, str], dict[str, Any]] = {}
    for d, model_id, in_tok, out_tok in cur.fetchall():
        label = alias_lookup.get(model_id, model_id)
        in_tok = in_tok or 0
        out_tok = out_tok or 0
        slot = agg.setdefault(
            (d, label),
            {"input_tokens": 0, "output_tokens": 0, "total": 0, "raw_ids": set()},
        )
        slot["input_tokens"] += in_tok
        slot["output_tokens"] += out_tok
        slot["total"] += in_tok + out_tok
        slot["raw_ids"].add(model_id)

    rows = []
    for (d, label), v in sorted(agg.items()):
        rows.append(
            TimeseriesRow(
                date=d,
                label=label,
                raw_ids=sorted(v["raw_ids"]),
                input_tokens=v["input_tokens"],
                output_tokens=v["output_tokens"],
                total=v["total"],
            )
        )
    return rows
