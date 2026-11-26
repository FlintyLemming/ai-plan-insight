"""SQLite-backed store for per-model token usage points.

All functions take an already-open `sqlite3.Connection`; the web layer owns
the DB path and opens a fresh connection per request. Storage always keeps
the raw `model_id`; aliasing to canonical labels happens at read time.

All dates are UTC+8 calendar days, matching the reporter agents.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

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
    source_id          TEXT PRIMARY KEY,
    label              TEXT,
    last_seen          TEXT,
    frozen_before      TEXT,
    auth_valid         INTEGER NOT NULL DEFAULT 0,
    last_auth_at       TEXT,
    stale_dismissed_at TEXT,
    last_ip            TEXT
)
"""

def init_schema(conn: sqlite3.Connection) -> None:
    """Create tables (idempotent) and enable WAL.

    Also back-fills columns added after the initial schema: `source.frozen_before`,
    `source.auth_valid`, `source.last_auth_at`, `source.stale_dismissed_at`,
    `source.last_ip`, and the three token-breakdown columns on `usage_point`
    (cache_read_tokens, cache_write_tokens, reasoning_tokens). Each is added
    via ALTER TABLE only if absent, so existing databases upgrade in place
    without a manual migration.
    """
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(_CREATE_USAGE_POINT)
    conn.execute(_CREATE_SOURCE)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(source)")}
    if "frozen_before" not in cols:
        conn.execute("ALTER TABLE source ADD COLUMN frozen_before TEXT")
    if "auth_valid" not in cols:
        conn.execute("ALTER TABLE source ADD COLUMN auth_valid INTEGER NOT NULL DEFAULT 0")
    if "last_auth_at" not in cols:
        conn.execute("ALTER TABLE source ADD COLUMN last_auth_at TEXT")
    if "stale_dismissed_at" not in cols:
        conn.execute("ALTER TABLE source ADD COLUMN stale_dismissed_at TEXT")
    if "last_ip" not in cols:
        conn.execute("ALTER TABLE source ADD COLUMN last_ip TEXT")
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
    last_ip: str | None = None,
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
    clean_ip = (last_ip or "").strip() or None
    # A fresh report also re-arms the stale alert: any dismissal only holds
    # until the device comes back, so the next outage alerts again.
    conn.execute(
        "INSERT INTO source (source_id, label, last_seen, frozen_before, last_ip) "
        "VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT(source_id) DO UPDATE SET "
        "label=COALESCE(excluded.label, source.label), "
        "last_seen=excluded.last_seen, "
        "frozen_before=excluded.frozen_before, "
        "last_ip=COALESCE(excluded.last_ip, source.last_ip), "
        "stale_dismissed_at=NULL",
        (source_id, source_label, ts, new_frozen, clean_ip),
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
    # Per-raw-id token tuples (input, output, cache_read, cache_write,
    # reasoning) so callers can price each raw model at its own rate and sum
    # per canonical label — the alias display name itself has no price.
    raw_breakdown: dict[str, tuple[int, int, int, int, int]] = field(
        default_factory=dict
    )


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
                "raw_breakdown": {},
            },
        )
        slot["input_tokens"] += in_tok
        slot["output_tokens"] += out_tok
        slot["cache_read_tokens"] += cr_tok
        slot["cache_write_tokens"] += cw_tok
        slot["reasoning_tokens"] += rs_tok
        slot["total"] += in_tok + out_tok + cr_tok + cw_tok + rs_tok
        slot["raw_ids"].add(model_id)
        prev = slot["raw_breakdown"].get(model_id, (0, 0, 0, 0, 0))
        slot["raw_breakdown"][model_id] = (
            prev[0] + in_tok,
            prev[1] + out_tok,
            prev[2] + cr_tok,
            prev[3] + cw_tok,
            prev[4] + rs_tok,
        )

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
                raw_breakdown=dict(v["raw_breakdown"]),
            )
        )
    return rows


def list_raw_model_usage(conn: sqlite3.Connection, days: int) -> list[tuple[str, int]]:
    """(raw model_id, total tokens) for the last `days` days, busiest first.

    Deliberately un-aliased: pricing is per raw model id, so the admin price
    panel must list raw ids, not canonical display labels. `date` is
    "YYYY-MM-DD" text, which compares correctly as a string.
    """
    start = datetime.now(UTC8).date() - timedelta(days=days - 1)
    return [
        (model_id, int(total))
        for model_id, total in conn.execute(
            "SELECT model_id, "
            "SUM(input_tokens + output_tokens + cache_read_tokens "
            "    + cache_write_tokens + reasoning_tokens) AS total "
            "FROM usage_point WHERE date >= ? "
            "GROUP BY model_id HAVING total > 0 "
            "ORDER BY total DESC, model_id",
            (start.isoformat(),),
        )
    ]


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


STALE_THRESHOLD = timedelta(hours=24)


def _parse_iso_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC8)
    return dt


def get_stale_sources(
    conn: sqlite3.Connection, now: datetime | None = None
) -> list[dict]:
    """Return reporting devices silent for more than 24h, minus dismissed ones.

    Only sources that have actually reported count (`last_seen NOT NULL`);
    push-card stub rows never alert. A zero-point report still bumps
    `last_seen`, so this tracks agent liveness, not usage volume. Rows whose
    `last_seen` cannot be parsed are skipped.
    """
    ref = now or datetime.now(UTC8)
    rows = conn.execute(
        "SELECT source_id, label, last_seen FROM source "
        "WHERE last_seen IS NOT NULL AND stale_dismissed_at IS NULL "
        "ORDER BY source_id"
    ).fetchall()
    out: list[dict] = []
    for source_id, label, last_seen in rows:
        seen_at = _parse_iso_dt(last_seen)
        if seen_at is None:
            continue
        if ref - seen_at > STALE_THRESHOLD:
            out.append(
                {"source_id": source_id, "label": label, "last_seen": last_seen}
            )
    return out


def dismiss_stale_source(
    conn: sqlite3.Connection, source_id: str, now: str
) -> bool:
    """Mark one source's stale alert as dismissed; caller must commit.

    Returns False when the source does not exist (for the endpoint's 404).
    The flag is cleared automatically by the next `upsert_points` report.
    """
    cur = conn.execute(
        "UPDATE source SET stale_dismissed_at = ? WHERE source_id = ?",
        (now, source_id),
    )
    return cur.rowcount > 0


def restore_stale_source(conn: sqlite3.Connection, source_id: str) -> bool:
    """Re-arm a previously dismissed stale alert; caller must commit.

    Returns False when the source does not exist (for the endpoint's 404).
    """
    cur = conn.execute(
        "UPDATE source SET stale_dismissed_at = NULL WHERE source_id = ?",
        (source_id,),
    )
    return cur.rowcount > 0


def list_usage_sources(
    conn: sqlite3.Connection,
    alias_lookup: Mapping[str, str] | None = None,
    now: datetime | None = None,
    top_models_limit: int = 5,
) -> list[dict]:
    """Return reporting machines (`source.last_seen IS NOT NULL`) with usage stats.

    Push-card stub rows (`last_seen IS NULL`) are excluded because they are
    already managed under provider instances. Ordered by `last_seen` descending
    (most recently active first), tie-broken by `source_id`.
    """
    ref = now or datetime.now(UTC8)
    if ref.tzinfo is None:
        ref = ref.replace(tzinfo=UTC8)
    today = ref.astimezone(UTC8).date()
    today_str = today.isoformat()
    start_7d = (today - timedelta(days=6)).isoformat()
    start_30d = (today - timedelta(days=29)).isoformat()

    source_rows = conn.execute(
        "SELECT source_id, label, last_seen, frozen_before, auth_valid, "
        "       last_auth_at, stale_dismissed_at, last_ip "
        "FROM source "
        "WHERE last_seen IS NOT NULL "
        "ORDER BY source_id"
    ).fetchall()
    if not source_rows:
        return []

    stats_by_source: dict[str, dict[str, Any]] = {}
    for (
        source_id,
        first_date,
        last_date,
        active_days,
        model_count,
        total_tokens,
        tokens_30d,
        tokens_7d,
        tokens_today,
    ) in conn.execute(
        "SELECT source_id, "
        "       MIN(date), "
        "       MAX(date), "
        "       COUNT(DISTINCT date), "
        "       COUNT(DISTINCT model_id), "
        "       SUM(input_tokens + output_tokens + cache_read_tokens "
        "           + cache_write_tokens + reasoning_tokens), "
        "       SUM(CASE WHEN date >= ? THEN input_tokens + output_tokens "
        "           + cache_read_tokens + cache_write_tokens + reasoning_tokens ELSE 0 END), "
        "       SUM(CASE WHEN date >= ? THEN input_tokens + output_tokens "
        "           + cache_read_tokens + cache_write_tokens + reasoning_tokens ELSE 0 END), "
        "       SUM(CASE WHEN date = ? THEN input_tokens + output_tokens "
        "           + cache_read_tokens + cache_write_tokens + reasoning_tokens ELSE 0 END) "
        "FROM usage_point "
        "GROUP BY source_id",
        (start_30d, start_7d, today_str),
    ):
        stats_by_source[source_id] = {
            "first_date": first_date,
            "last_date": last_date,
            "active_days": int(active_days or 0),
            "model_count": int(model_count or 0),
            "total_tokens": int(total_tokens or 0),
            "tokens_30d": int(tokens_30d or 0),
            "tokens_7d": int(tokens_7d or 0),
            "tokens_today": int(tokens_today or 0),
        }

    aliases = alias_lookup or {}
    models_by_source: dict[str, dict[str, int]] = {}
    for source_id, model_id, total in conn.execute(
        "SELECT source_id, model_id, "
        "       SUM(input_tokens + output_tokens + cache_read_tokens "
        "           + cache_write_tokens + reasoning_tokens) AS total "
        "FROM usage_point "
        "GROUP BY source_id, model_id "
        "HAVING total > 0"
    ):
        canonical = aliases.get(model_id, model_id)
        bucket = models_by_source.setdefault(source_id, {})
        bucket[canonical] = bucket.get(canonical, 0) + int(total or 0)

    enriched: list[tuple[tuple[int, float, str], dict[str, Any]]] = []
    for (
        source_id,
        label,
        last_seen,
        frozen_before,
        auth_valid,
        last_auth_at,
        stale_dismissed_at,
        last_ip,
    ) in source_rows:
        seen_at = _parse_iso_dt(last_seen)
        is_stale = (seen_at is not None) and ((ref - seen_at) > STALE_THRESHOLD)
        stats = stats_by_source.get(source_id, {})
        model_totals = models_by_source.get(source_id, {})
        top_models = [
            {"label": name, "tokens": tok}
            for name, tok in sorted(
                model_totals.items(), key=lambda kv: (-kv[1], kv[0])
            )[:top_models_limit]
        ]
        item = {
            "source_id": source_id,
            "label": label,
            "last_seen": last_seen,
            "is_stale": is_stale,
            "stale_dismissed": stale_dismissed_at is not None,
            "stale_dismissed_at": stale_dismissed_at,
            "auth_valid": bool(auth_valid),
            "last_auth_at": last_auth_at,
            "frozen_before": frozen_before,
            "last_ip": last_ip,
            "first_date": stats.get("first_date"),
            "last_date": stats.get("last_date"),
            "active_days": stats.get("active_days", 0),
            "model_count": stats.get("model_count", 0),
            "tokens_today": stats.get("tokens_today", 0),
            "tokens_7d": stats.get("tokens_7d", 0),
            "tokens_30d": stats.get("tokens_30d", 0),
            "total_tokens": stats.get("total_tokens", 0),
            "top_models": top_models,
        }
        sort_key = (0, -seen_at.timestamp(), source_id) if seen_at is not None else (1, 0.0, source_id)
        enriched.append((sort_key, item))

    enriched.sort(key=lambda pair: pair[0])
    return [item for _, item in enriched]


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

