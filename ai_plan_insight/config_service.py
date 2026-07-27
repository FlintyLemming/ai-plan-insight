# ai_plan_insight/config_service.py
"""Hot-reloadable config service: version-polled, fault-tolerant, single
source of truth for both the subscription balance manager and the
model-usage push auth.
"""
from __future__ import annotations

import logging
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

from . import config_store
from .instance_config import LoadResult, V2Config

logger = logging.getLogger(__name__)


class ConfigService:
    """Caches a LoadResult keyed on the DB's `config_version`.

    Each `get()` runs one `SELECT value FROM app_setting WHERE
    key='config_version'` — as cheap as the file stat it replaces. If the
    version is unchanged the cached LoadResult is returned. On change the
    config is reassembled; if the effective signature changed (config_error
    flipped, the set of provider instances changed, their contents changed,
    or instance_errors changed) the registered manager is told to reload
    (or disable). An unreadable DB reads as version None, which never
    matches the cache, so the service keeps retrying until it recovers.
    """

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._cached: LoadResult | None = None
        self._cached_version: int | None = None
        self._cached_sig: tuple | None = None
        self._manager: Any = None

    def set_manager(self, manager: Any) -> None:
        self._manager = manager

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._db_path)

    def _read_config_version(self) -> int | None:
        try:
            with closing(self._connect()) as conn:
                return config_store.get_config_version(conn)
        except sqlite3.Error:
            return None

    @staticmethod
    def _signature(result: LoadResult) -> tuple:
        prov_items = sorted(
            (iid, inst.model_dump()) for iid, inst in result.config.providers.items()
        )
        return (
            result.config_error is not None,
            prov_items,
            sorted(result.instance_errors.items()),
        )

    def get(self) -> LoadResult:
        version = self._read_config_version()
        if (
            self._cached is not None
            and version is not None
            and version == self._cached_version
        ):
            return self._cached

        try:
            with closing(self._connect()) as conn:
                result = config_store.load_config(conn)
        except sqlite3.Error as e:
            result = LoadResult(
                config=V2Config(providers={}),
                config_error=f"config DB unreadable: {e}",
            )

        new_sig = self._signature(result)
        changed = self._cached is None or new_sig != self._cached_sig

        self._cached = result
        self._cached_version = version
        self._cached_sig = new_sig

        if changed and self._manager is not None:
            if result.config_error is not None:
                self._manager.disable(result.config_error)
            else:
                self._manager.reload(result.config, result.instance_errors)
        return result
