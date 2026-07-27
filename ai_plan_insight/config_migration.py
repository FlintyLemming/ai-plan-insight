# ai_plan_insight/config_migration.py
"""One-shot config.json -> SQLite migration, run once at startup.

Only fires when a source file exists AND the database config is still
empty. The file is renamed to `.bak` afterwards so the next startup takes
the "db not empty" branch. If the rename fails (read-only mount) the
migrated config is NOT rolled back: the database is the source of truth
from that moment on, and the "db not empty" guard prevents a re-import.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from . import config_store
from .instance_config import load_v2_config

logger = logging.getLogger(__name__)

_STRUCTURAL = ("type", "mode", "label", "order")


@dataclass
class MigrationResult:
    migrated: bool
    reason: str  # no_file | db_not_empty | parse_error | migrated
    summary: dict = field(default_factory=dict)


def backup_path_for(source: Path, now: datetime) -> Path:
    """`config.json` -> `config.json.bak`, or a timestamped variant if taken."""
    candidate = source.with_name(source.name + ".bak")
    if candidate.exists():
        candidate = source.with_name(f"{source.name}.bak.{now.strftime('%Y%m%d-%H%M%S')}")
    return candidate


def _log_duplicate_aliases(model_aliases: dict[str, list[str]]) -> None:
    seen: dict[str, str] = {}
    for label, raw_ids in model_aliases.items():
        for raw_id in raw_ids:
            if raw_id in seen:
                logger.info(
                    "model alias %r redefined during migration: %r -> %r (last wins)",
                    raw_id, seen[raw_id], label,
                )
            seen[raw_id] = label


def run_migration(conn, source: Path, now: datetime | None = None) -> MigrationResult:
    now = now or datetime.now().astimezone()

    if not source.exists():
        return MigrationResult(False, "no_file")

    if not config_store.is_config_empty(conn):
        logger.warning(
            "config file %s ignored: the database already holds configuration", source
        )
        return MigrationResult(False, "db_not_empty", {"source_path": str(source)})

    result = load_v2_config(str(source))
    if result.config_error is not None:
        logger.error("config file %s not migrated: %s", source, result.config_error)
        return MigrationResult(
            False, "parse_error",
            {"source_path": str(source), "error": result.config_error},
        )

    providers: dict[str, dict] = {}
    for instance_id, cfg in result.config.providers.items():
        entry = {
            "type": cfg.type, "mode": cfg.mode, "label": cfg.label,
            "order": cfg.order, "enabled": True,
        }
        entry.update({
            name: value
            for name, value in cfg.model_dump().items()
            if name not in _STRUCTURAL and value
        })
        providers[instance_id] = entry

    _log_duplicate_aliases(result.config.model_aliases)

    # One transaction: instances + aliases + push auth + config_version.
    config_store.import_config(conn, {
        "providers": providers,
        "model_aliases": result.config.model_aliases,
        "push_auth_secret": result.config.push_auth_secret,
        "enforce_push_auth": result.config.enforce_push_auth,
    })

    backup = backup_path_for(source, now)
    backup_ok = True
    backup_error = ""
    try:
        source.rename(backup)
    except OSError as e:
        backup_ok = False
        backup_error = str(e)
        logger.error("config backup rename failed for %s: %s", source, e)

    summary = {
        "source_path": str(source),
        "instances": len(providers),
        "aliases": len(result.config.model_aliases),
        "skipped": dict(result.instance_errors),
        "push_auth_migrated": bool(result.config.push_auth_secret),
        "backup_ok": backup_ok,
        "backup_path": str(backup) if backup_ok else "",
        "backup_error": backup_error,
        "migrated_at": now.isoformat(),
    }
    config_store.set_setting(conn, "config_migrated_at", now.isoformat())
    config_store.set_setting(conn, "migration_notice", "pending")
    config_store.set_setting(
        conn, "migration_summary_json", json.dumps(summary, ensure_ascii=False)
    )
    logger.info(
        "migrated %d instance(s) and %d alias(es) from %s (%d skipped)",
        summary["instances"], summary["aliases"], source, len(summary["skipped"]),
    )
    return MigrationResult(True, "migrated", summary)
