# ai_plan_insight/config_service.py
"""Hot-reloadable config service: mtime-polled, fault-tolerant, single source
of truth for both the subscription balance manager and the model-usage push auth.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from .instance_config import load_v2_config, LoadResult, V2Config, V2InstanceConfig

logger = logging.getLogger(__name__)


class ConfigService:
    """Caches a LoadResult keyed on the config file's mtime.

    Each `get()` stats the file once. If the mtime is unchanged, the cached
    LoadResult is returned (essentially free). On mtime change the file is
    re-parsed fault-tolerantly; if the effective config signature changed
    (config_error flipped, the set of provider instances changed, their
    contents changed, or instance_errors changed) the registered manager is
    told to reload (or disable).
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._cached: LoadResult | None = None
        self._cached_mtime: float | None = None
        self._cached_sig: tuple | None = None
        self._manager: Any = None

    def set_manager(self, manager: Any) -> None:
        self._manager = manager

    def _stat_mtime(self) -> float | None:
        try:
            return self._path.stat().st_mtime
        except FileNotFoundError:
            return None

    @staticmethod
    def _signature(result: LoadResult) -> tuple:
        # Normalize provider instances to JSON for content comparison.
        prov_items = sorted(
            (iid, inst.model_dump()) for iid, inst in result.config.providers.items()
        )
        return (
            result.config_error is not None,
            prov_items,
            sorted(result.instance_errors.items()),
        )

    def get(self) -> LoadResult:
        mtime = self._stat_mtime()
        if self._cached is not None and mtime == self._cached_mtime:
            return self._cached

        result = load_v2_config(str(self._path))
        new_sig = self._signature(result)
        changed = self._cached is None or new_sig != self._cached_sig

        self._cached = result
        self._cached_mtime = mtime
        self._cached_sig = new_sig

        if changed and self._manager is not None:
            if result.config_error is not None:
                self._manager.disable(result.config_error)
            else:
                self._manager.reload(result.config, result.instance_errors)
        return result
