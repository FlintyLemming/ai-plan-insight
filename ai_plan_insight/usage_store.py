"""SQLite-backed store for per-model token usage points.

All functions take an already-open `sqlite3.Connection`; the web layer owns
the DB path and opens a fresh connection per request. Storage always keeps
the raw `model_id`; aliasing to canonical labels happens at read time.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, Mapping


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
    source_id   TEXT PRIMARY KEY,
    label       TEXT,
    last_seen   TEXT
)
"""


def init_schema(conn: sqlite3.Connection) -> None:
    """Create tables (idempotent) and enable WAL."""
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(_CREATE_USAGE_POINT)
    conn.execute(_CREATE_SOURCE)
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
    now: str | None = None,
) -> int:
    """UPSERT points for one source in the caller's transaction.

    `points` items are mappings with keys: date, model_id, input_tokens,
    output_tokens. A repeat post for the same (date, source_id, model_id)
    overwrites rather than accumulates. Returns the number of points written.
    The caller is responsible for `conn.commit()`.
    """
    ts = now or datetime.now().astimezone().isoformat()
    count = 0
    for p in points:
        conn.execute(
            "INSERT INTO usage_point "
            "(date, source_id, model_id, input_tokens, output_tokens, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(date, source_id, model_id) DO UPDATE SET "
            "input_tokens=excluded.input_tokens, "
            "output_tokens=excluded.output_tokens, "
            "updated_at=excluded.updated_at",
            (p["date"], source_id, p["model_id"], p["input_tokens"], p["output_tokens"], ts),
        )
        count += 1
    conn.execute(
        "INSERT INTO source (source_id, label, last_seen) VALUES (?, ?, ?) "
        "ON CONFLICT(source_id) DO UPDATE SET "
        "label=COALESCE(excluded.label, source.label), last_seen=excluded.last_seen",
        (source_id, source_label, ts),
    )
    return count


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
    today = date.today()
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
