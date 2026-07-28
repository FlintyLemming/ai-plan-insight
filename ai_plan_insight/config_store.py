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
import math
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from . import provider_types, provider_v2_store
from .instance_config import LoadResult, V2Config, V2InstanceConfig, _validate_instance

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

_CREATE_MODEL_PRICE = """
CREATE TABLE IF NOT EXISTS model_price (
    model_id             TEXT PRIMARY KEY,
    input_per_mtok       REAL NOT NULL,
    output_per_mtok      REAL NOT NULL,
    cache_read_per_mtok  REAL NOT NULL DEFAULT 0,
    cache_write_per_mtok REAL NOT NULL DEFAULT 0,
    updated_at           TEXT NOT NULL
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
    conn.execute(_CREATE_MODEL_PRICE)
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


# ── model aliases ─────────────────────────────────────────────

def validate_aliases(mapping: dict) -> None:
    """Raise ValueError if the alias mapping is not {label: [raw_id, ...]}."""
    if not isinstance(mapping, dict):
        raise ValueError("model_aliases must be an object")
    for label, raw_ids in mapping.items():
        if not isinstance(label, str) or not label.strip():
            raise ValueError("alias label must be a non-empty string")
        if not isinstance(raw_ids, list):
            raise ValueError(f"alias {label!r}: raw ids must be a list")
        for raw_id in raw_ids:
            if not isinstance(raw_id, str) or not raw_id.strip():
                raise ValueError(f"alias {label!r}: raw id must be a non-empty string")


def get_aliases(conn: sqlite3.Connection) -> dict[str, list[str]]:
    """label -> raw ids, in insertion order (rowid)."""
    out: dict[str, list[str]] = {}
    for raw_id, label in conn.execute(
        "SELECT raw_id, label FROM model_alias ORDER BY rowid"
    ):
        out.setdefault(label, []).append(raw_id)
    return out


def _write_aliases(conn: sqlite3.Connection, mapping: dict[str, list[str]]) -> None:
    """Replace the alias table inside the caller's transaction.

    `raw_id` is the primary key, so a raw id listed under two labels ends up
    on the last one — matching V2Config.alias_lookup's "last definition wins".
    """
    conn.execute("DELETE FROM model_alias")
    for label, raw_ids in mapping.items():
        for raw_id in raw_ids:
            conn.execute(
                "INSERT INTO model_alias (raw_id, label) VALUES (?, ?) "
                "ON CONFLICT(raw_id) DO UPDATE SET label = excluded.label",
                (raw_id, label),
            )


def replace_aliases(conn: sqlite3.Connection, mapping: dict[str, list[str]]) -> None:
    validate_aliases(mapping)
    _write_aliases(conn, mapping)
    _bump_version(conn)
    conn.commit()


# ── model prices ──────────────────────────────────────────────

_PRICE_KEYS = ("input", "output", "cache_read", "cache_write")


def validate_prices(mapping: dict) -> None:
    """Raise ValueError unless the mapping is {model_id: {price key: number}}.

    Prices are USD per MILLION tokens. Unknown keys are rejected rather than
    ignored, so a typo like "cache-read" fails loudly instead of silently
    pricing cache reads at 0. An entry with both input and output at 0 is
    meaningless — clearing a row means dropping it from the mapping.
    """
    if not isinstance(mapping, dict):
        raise ValueError("model_prices must be an object")
    for model_id, spec in mapping.items():
        if not isinstance(model_id, str) or not model_id.strip():
            raise ValueError("price model id must be a non-empty string")
        if not isinstance(spec, dict):
            raise ValueError(f"price {model_id!r}: must be an object")
        unknown = sorted(set(spec) - set(_PRICE_KEYS))
        if unknown:
            raise ValueError(f"price {model_id!r}: unknown field(s) {unknown}")
        values: dict[str, float] = {}
        for key in _PRICE_KEYS:
            raw = spec.get(key, 0)
            # bool is an int subclass; True must not become 1.0
            if isinstance(raw, bool) or not isinstance(raw, (int, float)):
                raise ValueError(f"price {model_id!r}: {key} must be a number")
            value = float(raw)
            if not math.isfinite(value):
                raise ValueError(f"price {model_id!r}: {key} must be finite")
            if value < 0:
                raise ValueError(f"price {model_id!r}: {key} must not be negative")
            values[key] = value
        if not values["input"] and not values["output"]:
            raise ValueError(
                f"price {model_id!r}: input and output must not both be 0; "
                "remove the entry to clear it"
            )


def get_prices(conn: sqlite3.Connection) -> dict[str, dict[str, float]]:
    """model_id -> USD per MILLION tokens, ordered by model_id."""
    return {
        model_id: {
            "input": inp, "output": outp,
            "cache_read": cache_read, "cache_write": cache_write,
        }
        for model_id, inp, outp, cache_read, cache_write in conn.execute(
            "SELECT model_id, input_per_mtok, output_per_mtok, "
            "cache_read_per_mtok, cache_write_per_mtok "
            "FROM model_price ORDER BY model_id"
        )
    }


def _write_prices(conn: sqlite3.Connection, mapping: dict) -> None:
    """Replace the price table inside the caller's transaction.

    Ids are stored lowercased; two spellings differing only in case collapse
    onto one row (last one wins), matching the exact-match lookup.
    """
    now = datetime.now().astimezone().isoformat()
    conn.execute("DELETE FROM model_price")
    for model_id, spec in mapping.items():
        conn.execute(
            "INSERT INTO model_price (model_id, input_per_mtok, output_per_mtok, "
            "cache_read_per_mtok, cache_write_per_mtok, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(model_id) DO UPDATE SET "
            "input_per_mtok=excluded.input_per_mtok, "
            "output_per_mtok=excluded.output_per_mtok, "
            "cache_read_per_mtok=excluded.cache_read_per_mtok, "
            "cache_write_per_mtok=excluded.cache_write_per_mtok, "
            "updated_at=excluded.updated_at",
            (
                model_id.strip().lower(),
                float(spec.get("input", 0)),
                float(spec.get("output", 0)),
                float(spec.get("cache_read", 0)),
                float(spec.get("cache_write", 0)),
                now,
            ),
        )


def replace_prices(conn: sqlite3.Connection, mapping: dict) -> None:
    validate_prices(mapping)
    _write_prices(conn, mapping)
    _bump_version(conn)
    conn.commit()


# ── push auth ─────────────────────────────────────────────────

def get_push_auth(conn: sqlite3.Connection) -> tuple[str, bool]:
    secret = get_setting(conn, "push_auth_secret", "") or ""
    enforce = (get_setting(conn, "enforce_push_auth", "0") or "0") == "1"
    return secret, enforce


def set_push_auth(conn: sqlite3.Connection, secret: str, enforce: bool) -> None:
    _set_setting(conn, "push_auth_secret", secret)
    _set_setting(conn, "enforce_push_auth", "1" if enforce else "0")
    _bump_version(conn)
    conn.commit()


# ── load ──────────────────────────────────────────────────────

def is_config_empty(conn: sqlite3.Connection) -> bool:
    """True when no instance, no alias and no push secret has been stored."""
    n_instances = conn.execute("SELECT COUNT(*) FROM provider_instance").fetchone()[0]
    n_aliases = conn.execute("SELECT COUNT(*) FROM model_alias").fetchone()[0]
    secret = get_setting(conn, "push_auth_secret", "") or ""
    return n_instances == 0 and n_aliases == 0 and not secret


def load_config(conn: sqlite3.Connection) -> LoadResult:
    """Assemble a V2Config from the DB, mirroring load_v2_config's semantics.

    Only enabled instances are loaded. Each row is re-validated: writes are
    strictly checked, but a code upgrade can retire a type or change a
    required field, and such rows must degrade into `instance_errors`
    instead of taking down the whole config. `config_error` is reserved for
    "the database itself is unreadable".
    """
    try:
        rows = list_instances(conn, include_disabled=False)
        aliases = get_aliases(conn)
        prices = get_prices(conn)
        secret, enforce = get_push_auth(conn)
    except sqlite3.Error as e:
        return LoadResult(
            config=V2Config(providers={}),
            config_error=f"config DB unreadable: {e}",
        )

    providers: dict[str, V2InstanceConfig] = {}
    instance_errors: dict[str, str] = {}
    for row in rows:
        try:
            cfg = row.to_instance_config()
            _validate_instance(row.instance_id, cfg)
            providers[row.instance_id] = cfg
        except Exception as e:
            instance_errors[row.instance_id] = f"instance {row.instance_id!r}: {e}"

    try:
        config = V2Config(
            providers=providers, model_aliases=aliases, model_prices=prices,
            push_auth_secret=secret, enforce_push_auth=enforce,
        )
    except Exception as e:
        return LoadResult(
            config=V2Config(providers={}),
            config_error=f"config schema invalid: {e}",
        )
    return LoadResult(config=config, instance_errors=instance_errors)


# ── export / import ───────────────────────────────────────────

class ImportValidationError(ValueError):
    """Raised when an import payload fails validation; `errors` maps the
    offending instance_id (or "_" for top-level problems) to a message."""

    def __init__(self, errors: dict[str, str]) -> None:
        super().__init__("; ".join(f"{k}: {v}" for k, v in errors.items()))
        self.errors = errors


_STRUCTURAL_KEYS = ("type", "mode", "label", "order", "enabled")


def export_config(conn: sqlite3.Connection) -> dict:
    """Config as a config.json-shaped dict, plus `enabled` per instance.

    Includes plaintext credentials; never includes the admin password.
    """
    providers: dict[str, dict] = {}
    for row in list_instances(conn, include_disabled=True):
        entry: dict = {
            "type": row.type, "mode": row.mode, "label": row.label,
            "order": row.order, "enabled": row.enabled,
        }
        entry.update(row.credentials)
        providers[row.instance_id] = entry
    secret, enforce = get_push_auth(conn)
    return {
        "providers": providers,
        "model_aliases": get_aliases(conn),
        "push_auth_secret": secret,
        "enforce_push_auth": enforce,
    }


def parse_import_payload(payload: dict) -> tuple[list[InstanceRow], dict[str, list[str]], str, bool]:
    """Validate an export-shaped payload wholesale.

    Every problem is collected before raising, so the caller can show all of
    them at once. Raises ImportValidationError; on success returns
    (rows, aliases, push_auth_secret, enforce_push_auth).
    """
    errors: dict[str, str] = {}
    if not isinstance(payload, dict):
        raise ImportValidationError({"_": "payload must be a JSON object"})

    raw_providers = payload.get("providers", {})
    rows: list[InstanceRow] = []
    if not isinstance(raw_providers, dict):
        errors["_"] = "'providers' must be a JSON object"
        raw_providers = {}

    for instance_id, spec in raw_providers.items():
        if not isinstance(spec, dict):
            errors[instance_id] = "must be a JSON object"
            continue
        credentials = {
            k: v for k, v in spec.items() if k not in _STRUCTURAL_KEYS
        }
        row = InstanceRow(
            instance_id=instance_id,
            type=str(spec.get("type", "")),
            mode=str(spec.get("mode", "")),
            label=str(spec.get("label", "")),
            order=int(spec.get("order", 999) or 999),
            enabled=bool(spec.get("enabled", True)),
            credentials={k: str(v) for k, v in credentials.items()},
        )
        try:
            validate_row(row)
        except Exception as e:
            errors[instance_id] = str(e)
            continue
        rows.append(row)

    aliases = payload.get("model_aliases", {}) or {}
    try:
        validate_aliases(aliases)
    except ValueError as e:
        errors["_model_aliases"] = str(e)

    secret = str(payload.get("push_auth_secret", "") or "")
    enforce = bool(payload.get("enforce_push_auth", False))

    if errors:
        raise ImportValidationError(errors)
    return rows, aliases, secret, enforce


def import_config(conn: sqlite3.Connection, payload: dict) -> None:
    """Validate everything, then replace all config in one transaction.

    The admin password and active sessions are untouched.
    """
    rows, aliases, secret, enforce = parse_import_payload(payload)
    now = datetime.now().astimezone().isoformat()
    conn.execute("DELETE FROM provider_instance")
    for row in rows:
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
    _write_aliases(conn, aliases)
    _set_setting(conn, "push_auth_secret", secret)
    _set_setting(conn, "enforce_push_auth", "1" if enforce else "0")
    _bump_version(conn)
    conn.commit()
