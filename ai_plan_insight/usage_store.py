"""SQLite-backed store for per-model token usage points.

All functions take an already-open `sqlite3.Connection`; the web layer owns
the DB path and opens a fresh connection per request. Storage always keeps
the raw `model_id`; aliasing to canonical labels happens at read time.

All dates are UTC+8 calendar days, matching the reporter agents.
"""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Literal

from ai_plan_insight.api_schemas import UsageResponse

UTC8 = timezone(timedelta(hours=8))


_CREATE_USAGE_POINT = """
CREATE TABLE IF NOT EXISTS usage_point (
    date                TEXT NOT NULL,
    source_id           TEXT NOT NULL,
    model_id            TEXT NOT NULL,
    input_tokens        INTEGER NOT NULL,
    output_tokens       INTEGER NOT NULL,
    cache_read_tokens   INTEGER NOT NULL DEFAULT 0,
    cache_write_tokens  INTEGER NOT NULL DEFAULT 0,
    reasoning_tokens    INTEGER NOT NULL DEFAULT 0,
    updated_at          TEXT NOT NULL,
    PRIMARY KEY (date, source_id, model_id)
)
"""

_CREATE_SOURCE = """
CREATE TABLE IF NOT EXISTS source (
    source_id     TEXT PRIMARY KEY,
    label         TEXT,
    last_seen     TEXT,
    frozen_before TEXT,
    auth_valid    INTEGER NOT NULL DEFAULT 0,
    last_auth_at  TEXT
)
"""

_CREATE_PROVIDER_SNAPSHOT = """
CREATE TABLE IF NOT EXISTS provider_snapshot (
    snapshot_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    provider          TEXT NOT NULL,
    source_kind       TEXT NOT NULL,
    recorded_at       TEXT NOT NULL,
    user_id           TEXT,
    membership_level  TEXT,
    raw_json          TEXT NOT NULL
)
"""

_CREATE_PROVIDER_ITEM = """
CREATE TABLE IF NOT EXISTS provider_item (
    item_id           INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id       INTEGER NOT NULL REFERENCES provider_snapshot(snapshot_id),
    item_kind         TEXT NOT NULL,
    name              TEXT NOT NULL,
    value_text        TEXT,
    value_number      REAL,
    unit              TEXT,
    reset_time        TEXT,
    extra_json        TEXT
)
"""

_NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?")


def _parse_flexible_number(value: str) -> float | None:
    """Extract a number from strings like '45.50', '¥ 1,234', or '$99.9'.

    Returns None if no numeric token can be parsed. Never raises.
    """
    if not value:
        return None
    cleaned = value.strip()
    # Remove common currency symbols, commas, spaces, and unit suffixes we see in balances
    cleaned = re.sub(r"[\$¥£€,\s]", "", cleaned)
    match = _NUMBER_RE.search(cleaned)
    if not match:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None


def _parse_limit_used(value: str) -> float | None:
    """Try to parse limit `used` as a float; return None on failure."""
    if not value:
        return None
    try:
        return float(value.strip())
    except ValueError:
        return None


def record_snapshot(
    conn: sqlite3.Connection,
    usage: UsageResponse,
    source_kind: Literal["fetch", "push"],
    now: str | None = None,
) -> int:
    """Persist one UsageResponse as a snapshot plus its item rows.

    Returns the new snapshot_id. The caller is responsible for commit.
    Value-number parsing failures are silently ignored; the snapshot still
    writes with value_text preserved.
    """
    recorded_at = now or datetime.now().astimezone().isoformat()
    cur = conn.execute(
        "INSERT INTO provider_snapshot "
        "(provider, source_kind, recorded_at, user_id, membership_level, raw_json) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            usage.provider,
            source_kind,
            recorded_at,
            usage.user_id,
            usage.membership_level,
            usage.model_dump_json(),
        ),
    )
    snapshot_id = cur.lastrowid

    items: list[tuple] = []

    for lim in usage.limits:
        items.append(
            (
                snapshot_id,
                "limit",
                lim.time_unit,
                lim.used,
                _parse_limit_used(lim.used),
                lim.time_unit,
                lim.reset_time,
                lim.model_dump_json() if lim else None,
            )
        )

    for key, value in usage.balances.items():
        items.append(
            (
                snapshot_id,
                "balance",
                key,
                value,
                _parse_flexible_number(value),
                None,
                None,
                None,
            )
        )

    for tu in usage.token_usage:
        items.append(
            (
                snapshot_id,
                "token_usage",
                tu.period,
                str(tu.total_tokens),
                float(tu.total_tokens),
                "tokens",
                None,
                tu.model_dump_json(),
            )
        )

    for ms in usage.model_stats:
        items.append(
            (
                snapshot_id,
                "model_stat",
                ms.model,
                str(ms.total_tokens),
                float(ms.total_tokens),
                "tokens",
                None,
                ms.model_dump_json(),
            )
        )

    if usage.history_usage is not None:
        hu = usage.history_usage
        items.append(
            (
                snapshot_id,
                "history_usage",
                "history_usage",
                str(hu.total_tokens),
                float(hu.total_tokens),
                f"{hu.period}/{hu.granularity}",
                None,
                hu.model_dump_json(),
            )
        )

    if items:
        conn.executemany(
            "INSERT INTO provider_item "
            "(snapshot_id, item_kind, name, value_text, value_number, unit, reset_time, extra_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            items,
        )

    return snapshot_id


def init_schema(conn: sqlite3.Connection) -> None:
    """Create tables (idempotent) and enable WAL.

    Also back-fills columns added after the initial schema: `source.frozen_before`
    and the three token-breakdown columns on `usage_point` (cache_read_tokens,
    cache_write_tokens, reasoning_tokens). Each is added via ALTER TABLE only if
    absent, so existing databases upgrade in place without a manual migration.
    """
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(_CREATE_USAGE_POINT)
    conn.execute(_CREATE_SOURCE)
    conn.execute(_CREATE_PROVIDER_SNAPSHOT)
    conn.execute(_CREATE_PROVIDER_ITEM)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_provider_item_snapshot "
        "ON provider_item(snapshot_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_provider_item_query "
        "ON provider_item(item_kind, name)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_snapshot_provider_time "
        "ON provider_snapshot(provider, recorded_at)"
    )
    cols = {row[1] for row in conn.execute("PRAGMA table_info(source)")}
    if "frozen_before" not in cols:
        conn.execute("ALTER TABLE source ADD COLUMN frozen_before TEXT")
    if "auth_valid" not in cols:
        conn.execute("ALTER TABLE source ADD COLUMN auth_valid INTEGER NOT NULL DEFAULT 0")
    if "last_auth_at" not in cols:
        conn.execute("ALTER TABLE source ADD COLUMN last_auth_at TEXT")
    usage_cols = {row[1] for row in conn.execute("PRAGMA table_info(usage_point)")}
    for col in ("cache_read_tokens", "cache_write_tokens", "reasoning_tokens"):
        if col not in usage_cols:
            conn.execute(
                f"ALTER TABLE usage_point ADD COLUMN {col} INTEGER NOT NULL DEFAULT 0"
            )
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
    output_tokens, and optionally cache_read_tokens, cache_write_tokens,
    reasoning_tokens (default 0, so older agents omitting them still work).
    Per-source freeze rule: points dated before the source's
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
                "(date, source_id, model_id, input_tokens, output_tokens, "
                " cache_read_tokens, cache_write_tokens, reasoning_tokens, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(date, source_id, model_id) DO UPDATE SET "
                "input_tokens=excluded.input_tokens, "
                "output_tokens=excluded.output_tokens, "
                "cache_read_tokens=excluded.cache_read_tokens, "
                "cache_write_tokens=excluded.cache_write_tokens, "
                "reasoning_tokens=excluded.reasoning_tokens, "
                "updated_at=excluded.updated_at",
                (
                    d,
                    source_id,
                    p["model_id"],
                    p["input_tokens"],
                    p["output_tokens"],
                    p.get("cache_read_tokens", 0),
                    p.get("cache_write_tokens", 0),
                    p.get("reasoning_tokens", 0),
                    ts,
                ),
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
    cache_read_tokens: int
    cache_write_tokens: int
    reasoning_tokens: int
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

    `total` is the sum of all five token categories (input + output +
    cache_read + cache_write + reasoning), matching tokscale's totalTokens.
    """
    today = datetime.now(UTC8).date()
    start = today - timedelta(days=days - 1)
    cur = conn.execute(
        "SELECT date, model_id, "
        "SUM(input_tokens), SUM(output_tokens), "
        "SUM(cache_read_tokens), SUM(cache_write_tokens), SUM(reasoning_tokens) "
        "FROM usage_point WHERE date >= ? "
        "GROUP BY date, model_id ORDER BY date, model_id",
        (start.isoformat(),),
    )
    agg: dict[tuple[str, str], dict[str, Any]] = {}
    for d, model_id, in_tok, out_tok, cr_tok, cw_tok, rs_tok in cur.fetchall():
        label = alias_lookup.get(model_id, model_id)
        in_tok = in_tok or 0
        out_tok = out_tok or 0
        cr_tok = cr_tok or 0
        cw_tok = cw_tok or 0
        rs_tok = rs_tok or 0
        slot = agg.setdefault(
            (d, label),
            {
                "input_tokens": 0,
                "output_tokens": 0,
                "cache_read_tokens": 0,
                "cache_write_tokens": 0,
                "reasoning_tokens": 0,
                "total": 0,
                "raw_ids": set(),
            },
        )
        slot["input_tokens"] += in_tok
        slot["output_tokens"] += out_tok
        slot["cache_read_tokens"] += cr_tok
        slot["cache_write_tokens"] += cw_tok
        slot["reasoning_tokens"] += rs_tok
        slot["total"] += in_tok + out_tok + cr_tok + cw_tok + rs_tok
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
                cache_read_tokens=v["cache_read_tokens"],
                cache_write_tokens=v["cache_write_tokens"],
                reasoning_tokens=v["reasoning_tokens"],
                total=v["total"],
            )
        )
    return rows


def update_source_auth(
    conn: sqlite3.Connection, source_id: str, is_valid: bool, now: str
) -> None:
    """Record the latest push-auth outcome for a source.

    Only `auth_valid` and `last_auth_at` are touched; the row must already
    exist from `upsert_points` (which writes the source row on every report).
    """
    conn.execute(
        "UPDATE source SET auth_valid = ?, last_auth_at = ? WHERE source_id = ?",
        (1 if is_valid else 0, now, source_id),
    )


def get_source_auth_status(conn: sqlite3.Connection) -> list[dict]:
    """Return all sources' authentication states for the admin endpoint."""
    rows = conn.execute(
        "SELECT source_id, label, last_seen, auth_valid, last_auth_at "
        "FROM source ORDER BY source_id"
    ).fetchall()
    return [
        {
            "source_id": source_id,
            "label": label,
            "last_seen": last_seen,
            "auth_valid": bool(auth_valid),
            "last_auth_at": last_auth_at,
        }
        for source_id, label, last_seen, auth_valid, last_auth_at in rows
    ]

