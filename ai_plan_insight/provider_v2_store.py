# ai_plan_insight/provider_v2_store.py
"""SQLite store for v2 provider instance snapshots.

Separate tables from the old provider_snapshot / provider_item / push_card_snapshot.
Uses UPSERT (one row per instance_id) instead of append-only history.
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime
from pathlib import Path

from .api_schemas import UsageResponse

logger = logging.getLogger(__name__)

_CREATE_V2_SNAPSHOT = """
CREATE TABLE IF NOT EXISTS provider_v2_snapshot (
    instance_id   TEXT PRIMARY KEY,
    type          TEXT NOT NULL,
    mode          TEXT NOT NULL,
    label         TEXT NOT NULL,
    recorded_at   TEXT NOT NULL,
    raw_json      TEXT NOT NULL
)
"""

_CREATE_V2_ITEM = """
CREATE TABLE IF NOT EXISTS provider_v2_item (
    item_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    instance_id   TEXT NOT NULL,
    item_kind     TEXT NOT NULL,
    name          TEXT NOT NULL,
    value_text    TEXT,
    value_number  REAL,
    unit          TEXT,
    reset_time    TEXT,
    extra_json    TEXT,
    FOREIGN KEY (instance_id)
        REFERENCES provider_v2_snapshot(instance_id)
)
"""


def init_v2_schema(conn: sqlite3.Connection) -> None:
    """Create v2 tables idempotently. Caller must commit."""
    conn.execute(_CREATE_V2_SNAPSHOT)
    conn.execute(_CREATE_V2_ITEM)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_provider_v2_item_instance "
        "ON provider_v2_item(instance_id)"
    )


def init_v2_db(path: Path) -> None:
    """Open path, create v2 schema, close. Called once at startup."""
    conn = sqlite3.connect(path)
    try:
        init_v2_schema(conn)
        conn.commit()
    finally:
        conn.close()


def upsert_v2_snapshot(
    conn: sqlite3.Connection,
    instance_id: str,
    type_name: str,
    mode: str,
    label: str,
    usage: UsageResponse,
    now: str | None = None,
) -> None:
    """UPSERT one v2 snapshot + replace its items. Caller must commit."""
    recorded_at = now or datetime.now().astimezone().isoformat()

    conn.execute(
        "INSERT INTO provider_v2_snapshot "
        "(instance_id, type, mode, label, recorded_at, raw_json) "
        "VALUES (?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(instance_id) DO UPDATE SET "
        "type=excluded.type, mode=excluded.mode, label=excluded.label, "
        "recorded_at=excluded.recorded_at, raw_json=excluded.raw_json",
        (instance_id, type_name, mode, label, recorded_at, usage.model_dump_json()),
    )

    # Delete old items for this instance, then insert new ones
    conn.execute("DELETE FROM provider_v2_item WHERE instance_id = ?", (instance_id,))

    items = _extract_items(usage)
    if items:
        conn.executemany(
            "INSERT INTO provider_v2_item "
            "(instance_id, item_kind, name, value_text, value_number, unit, reset_time, extra_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [(instance_id, *item) for item in items],
        )


def _extract_items(usage: UsageResponse) -> list[tuple]:
    """Extract item rows from a UsageResponse, mirroring the old store's logic."""
    items: list[tuple] = []

    for lim in usage.limits:
        items.append((
            "limit", lim.time_unit, lim.used, _safe_float(lim.used),
            lim.time_unit, lim.reset_time,
            lim.model_dump_json() if lim else None,
        ))

    for key, value in usage.balances.items():
        items.append((
            "balance", key, value, _safe_float(value),
            None, None, None,
        ))

    for tu in usage.token_usage:
        items.append((
            "token_usage", tu.period, str(tu.total_tokens), float(tu.total_tokens),
            "tokens", None, tu.model_dump_json(),
        ))

    for ms in usage.model_stats:
        items.append((
            "model_stat", ms.model, str(ms.total_tokens), float(ms.total_tokens),
            "tokens", None, ms.model_dump_json(),
        ))

    if usage.history_usage is not None:
        hu = usage.history_usage
        items.append((
            "history_usage", "history_usage", str(hu.total_tokens),
            float(hu.total_tokens), f"{hu.period}/{hu.granularity}", None,
            hu.model_dump_json(),
        ))

    return items


def _safe_float(value: str) -> float | None:
    """Try to parse a number from a string; return None on failure."""
    if not value:
        return None
    try:
        return float(value.strip())
    except ValueError:
        return None


def load_v2_snapshots(
    conn: sqlite3.Connection,
    active_instance_ids: set[str],
    type_mode_map: dict[str, tuple[str, str]] | None = None,
    label_map: dict[str, str] | None = None,
) -> list[tuple[str, str, str, str, str, UsageResponse]]:
    """Load v2 snapshots for instances still present in the current config.

    Returns (instance_id, type, mode, label, recorded_at, usage_response).
    Skips instances not in active_instance_ids, type/mode mismatches, and
    corrupt rows.
    """
    if not active_instance_ids:
        return []

    placeholders = ",".join("?" * len(active_instance_ids))
    rows = conn.execute(
        f"SELECT instance_id, type, mode, label, recorded_at, raw_json "
        f"FROM provider_v2_snapshot WHERE instance_id IN ({placeholders})",
        list(active_instance_ids),
    ).fetchall()

    out: list[tuple[str, str, str, str, str, UsageResponse]] = []
    for instance_id, type_name, mode, label, recorded_at, raw_json in rows:
        # Check type+mode match current config
        if type_mode_map and instance_id in type_mode_map:
            expected_type, expected_mode = type_mode_map[instance_id]
            if type_name != expected_type or mode != expected_mode:
                logger.warning(
                    "v2 snapshot %s: type/mode mismatch (stored=%s/%s, config=%s/%s), skipping",
                    instance_id, type_name, mode, expected_type, expected_mode,
                )
                continue

        try:
            usage = UsageResponse.model_validate_json(raw_json)
        except Exception:
            logger.warning("v2 snapshot %s: corrupt raw_json, skipping", instance_id)
            continue

        # Use config label if available
        effective_label = (label_map or {}).get(instance_id, label)
        out.append((instance_id, type_name, mode, effective_label, recorded_at, usage))

    return out
