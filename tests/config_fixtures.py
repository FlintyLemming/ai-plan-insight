"""Seed the DB-backed config in tests.

Replaces the old pattern of writing a temporary config.json. Import it as
`from config_fixtures import seed_config` — pytest puts `tests/` on sys.path.
"""
from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

from ai_plan_insight import config_store, usage_store


def seed_config(
    db_path,
    providers: dict | None = None,
    model_aliases: dict | None = None,
    push_auth_secret: str = "",
    enforce_push_auth: bool = False,
) -> Path:
    """Create the schema in `db_path` and write one full config into it.

    `providers` uses the same shape as the old config.json: instance_id ->
    {"type", "mode", "label", "order", "enabled", ...credentials}.
    """
    db_path = Path(db_path)
    usage_store.init_db(db_path)
    config_store.init_config_db(db_path)
    with closing(sqlite3.connect(db_path)) as conn:
        for instance_id, spec in (providers or {}).items():
            spec = dict(spec)
            row = config_store.InstanceRow(
                instance_id=instance_id,
                type=spec.pop("type"),
                mode=spec.pop("mode"),
                label=spec.pop("label"),
                order=spec.pop("order", 999),
                enabled=spec.pop("enabled", True),
                credentials={k: str(v) for k, v in spec.items()},
            )
            config_store.create_instance(conn, row)
        if model_aliases:
            config_store.replace_aliases(conn, model_aliases)
        if push_auth_secret or enforce_push_auth:
            config_store.set_push_auth(conn, push_auth_secret, enforce_push_auth)
    return db_path
