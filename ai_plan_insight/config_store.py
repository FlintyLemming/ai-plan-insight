# ai_plan_insight/config_store.py
"""SQLite-backed configuration store.

Replaces config.json as the source of truth for provider instances, model
aliases and push auth. Every write happens in one transaction that also
bumps `config_version`; ConfigService polls that integer in place of the
old file mtime. Write functions commit themselves — a config change is
always a complete unit of work, unlike `usage_store` where the caller
batches several calls into one request transaction.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from . import provider_types, provider_v2_store
from .instance_config import V2InstanceConfig, _validate_instance

_CREATE_APP_SETTING = """
CREATE TABLE IF NOT EXISTS app_setting (
    key    TEXT PRIMARY KEY,
    value  TEXT NOT NULL
)
"""

_CREATE_PROVIDER_INSTANCE = """
CREATE TABLE IF NOT EXISTS provider_instance (
    instance_id       TEXT PRIMARY KEY,
    type              TEXT NOT NULL,
    mode              TEXT NOT NULL,
    label             TEXT NOT NULL,
    sort_order        INTEGER NOT NULL DEFAULT 999,
    enabled           INTEGER NOT NULL DEFAULT 1,
    credentials_json  TEXT NOT NULL DEFAULT '{}',
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL
)
"""

_CREATE_MODEL_ALIAS = """
CREATE TABLE IF NOT EXISTS model_alias (
    raw_id  TEXT PRIMARY KEY,
    label   TEXT NOT NULL
)
"""

_CREATE_ADMIN_SESSION = """
CREATE TABLE IF NOT EXISTS admin_session (
    token_hash  TEXT PRIMARY KEY,
    created_at  TEXT NOT NULL,
    expires_at  TEXT NOT NULL
)
"""


def init_config_schema(conn: sqlite3.Connection) -> None:
    """Create the config tables idempotently and seed config_version.

    Also ensures the v2 snapshot tables exist, because `delete_instance`
    cleans them up and must not depend on the runtime manager having
    started first.
    """
    conn.execute(_CREATE_APP_SETTING)
    conn.execute(_CREATE_PROVIDER_INSTANCE)
    conn.execute(_CREATE_MODEL_ALIAS)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_model_alias_label ON model_alias(label)")
    conn.execute(_CREATE_ADMIN_SESSION)
    provider_v2_store.init_v2_schema(conn)
    conn.execute(
        "INSERT INTO app_setting (key, value) VALUES ('config_version', '0') "
        "ON CONFLICT(key) DO NOTHING"
    )
    conn.commit()


def init_config_db(path: Path) -> None:
    """Open `path`, create the config schema, close. Called once at startup."""
    conn = sqlite3.connect(path)
    try:
        init_config_schema(conn)
    finally:
        conn.close()


# ── app_setting ───────────────────────────────────────────────

def get_setting(conn: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    row = conn.execute("SELECT value FROM app_setting WHERE key = ?", (key,)).fetchone()
    return row[0] if row is not None else default


def _set_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    """Write one setting inside the caller's transaction."""
    conn.execute(
        "INSERT INTO app_setting (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


def set_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    """Write one setting and commit. Does NOT bump config_version — these
    keys (admin password, migration notice) are not part of the runtime
    config that ConfigService watches."""
    _set_setting(conn, key, value)
    conn.commit()


def get_config_version(conn: sqlite3.Connection) -> int:
    raw = get_setting(conn, "config_version", "0") or "0"
    try:
        return int(raw)
    except ValueError:
        return 0


def _bump_version(conn: sqlite3.Connection) -> None:
    """Increment config_version inside the caller's transaction."""
    _set_setting(conn, "config_version", str(get_config_version(conn) + 1))


# ── provider instances ────────────────────────────────────────

@dataclass
class InstanceRow:
    """One row of `provider_instance`, with credentials already unpacked."""
    instance_id: str
    type: str
    mode: str
    label: str
    order: int = 999
    enabled: bool = True
    credentials: dict[str, str] = field(default_factory=dict)

    def to_instance_config(self) -> V2InstanceConfig:
        return V2InstanceConfig(
            type=self.type, mode=self.mode, label=self.label,
            order=self.order, **self.credentials,
        )


def validate_row(row: InstanceRow) -> None:
    """Raise ValueError if this row would not survive a config load.

    Covers both the Pydantic layer (unknown credential field, bad mode)
    and `_validate_instance` (illegal id, unknown type, type/mode mismatch,
    field not accepted by the type, missing required credential).
    Pydantic's ValidationError is a subclass of ValueError, so one except
    clause catches both.
    """
    cfg = row.to_instance_config()
    _validate_instance(row.instance_id, cfg)


def _row_from_db(r: tuple) -> InstanceRow:
    instance_id, type_name, mode, label, sort_order, enabled, credentials_json = r
    try:
        credentials = json.loads(credentials_json)
        if not isinstance(credentials, dict):
            credentials = {}
    except ValueError:
        credentials = {}
    return InstanceRow(
        instance_id=instance_id, type=type_name, mode=mode, label=label,
        order=sort_order, enabled=bool(enabled),
        credentials={k: str(v) for k, v in credentials.items()},
    )


_SELECT_INSTANCE = (
    "SELECT instance_id, type, mode, label, sort_order, enabled, credentials_json "
    "FROM provider_instance"
)


def list_instances(conn: sqlite3.Connection, include_disabled: bool = True) -> list[InstanceRow]:
    sql = _SELECT_INSTANCE
    if not include_disabled:
        sql += " WHERE enabled = 1"
    sql += " ORDER BY sort_order, instance_id"
    return [_row_from_db(r) for r in conn.execute(sql)]


def get_instance(conn: sqlite3.Connection, instance_id: str) -> InstanceRow | None:
    r = conn.execute(
        _SELECT_INSTANCE + " WHERE instance_id = ?", (instance_id,)
    ).fetchone()
    return _row_from_db(r) if r is not None else None


def create_instance(conn: sqlite3.Connection, row: InstanceRow) -> None:
    """Validate, insert, bump config_version, commit.

    Raises ValueError when the row is invalid or the id is already taken;
    nothing is written in that case.
    """
    validate_row(row)
    now = datetime.now().astimezone().isoformat()
    try:
        conn.execute(
            "INSERT INTO provider_instance "
            "(instance_id, type, mode, label, sort_order, enabled, credentials_json, "
            " created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                row.instance_id, row.type, row.mode, row.label, row.order,
                1 if row.enabled else 0, json.dumps(row.credentials, ensure_ascii=False),
                now, now,
            ),
        )
    except sqlite3.IntegrityError:
        conn.rollback()
        raise ValueError(f"instance {row.instance_id!r} already exists")
    _bump_version(conn)
    conn.commit()


def update_instance(conn: sqlite3.Connection, instance_id: str, row: InstanceRow) -> None:
    """Validate and replace one instance. The id itself is immutable.

    Raises KeyError when the instance does not exist, ValueError when the
    row is invalid or `row.instance_id` differs from `instance_id`.
    """
    if row.instance_id != instance_id:
        raise ValueError("instance_id is immutable; delete and recreate to rename")
    validate_row(row)
    now = datetime.now().astimezone().isoformat()
    cur = conn.execute(
        "UPDATE provider_instance SET type = ?, mode = ?, label = ?, sort_order = ?, "
        "enabled = ?, credentials_json = ?, updated_at = ? WHERE instance_id = ?",
        (
            row.type, row.mode, row.label, row.order, 1 if row.enabled else 0,
            json.dumps(row.credentials, ensure_ascii=False), now, instance_id,
        ),
    )
    if cur.rowcount == 0:
        conn.rollback()
        raise KeyError(instance_id)
    _bump_version(conn)
    conn.commit()


def delete_instance(conn: sqlite3.Connection, instance_id: str) -> None:
    """Delete one instance and its v2 snapshot rows. Raises KeyError if absent."""
    cur = conn.execute(
        "DELETE FROM provider_instance WHERE instance_id = ?", (instance_id,)
    )
    if cur.rowcount == 0:
        conn.rollback()
        raise KeyError(instance_id)
    conn.execute("DELETE FROM provider_v2_item WHERE instance_id = ?", (instance_id,))
    conn.execute("DELETE FROM provider_v2_snapshot WHERE instance_id = ?", (instance_id,))
    _bump_version(conn)
    conn.commit()
