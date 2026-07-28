# ai_plan_insight/instance_config.py
"""Provider instance configuration.

The single source of truth for config.json: provider instances, model
aliases, and push auth. config.py only keeps the ProviderConfig credential
value object used to construct providers.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.json"

_INSTANCE_ID_RE = re.compile(r"[A-Za-z0-9._-]+")

from . import provider_types

# Derived from provider_types.PROVIDER_TYPES — the single source of truth.
# Kept as module-level names because _validate_instance() and existing tests
# reference them directly.
_SUPPORTED_TYPES: dict[str, set[str]] = provider_types.supported_types()
_ALLOWED_CREDENTIAL_FIELDS: dict[str, set[str]] = provider_types.allowed_credential_fields()
_REQUIRED_CREDENTIAL_FIELDS: dict[tuple[str, str], set[str]] = provider_types.required_credential_fields()

# The known field names for V2InstanceConfig. Anything else is rejected.
_KNOWN_FIELDS = {
    "type", "mode", "label", "order",
    "api_key", "base_url", "user_name",
    "access_key_id", "access_key_secret",
    "admin_password", "cookie",
}


class V2InstanceConfig(BaseModel):
    """Configuration for one provider instance in v2."""
    model_config = {"extra": "forbid"}

    type: str
    mode: str
    label: str
    order: int = 999
    api_key: str = ""
    base_url: str = ""
    user_name: str = ""
    access_key_id: str = ""
    access_key_secret: str = ""
    admin_password: str = ""
    cookie: str = ""

    @field_validator("mode")
    @classmethod
    def _check_mode(cls, v: str) -> str:
        if v not in ("fetch", "push"):
            raise ValueError(f"mode must be 'fetch' or 'push', got {v!r}")
        return v

    @field_validator("label")
    @classmethod
    def _check_label(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("label must be non-empty")
        return v


class ModelPriceConfig(BaseModel):
    """One manual price override, in USD per MILLION tokens.

    Only used for model ids that none of the upstream pricing sources knows.
    """
    model_config = {"extra": "forbid"}

    input: float = 0.0
    output: float = 0.0
    cache_read: float = 0.0
    cache_write: float = 0.0


class V2Config(BaseModel):
    """Top-level v2 configuration."""
    model_config = {"extra": "forbid"}

    providers: dict[str, V2InstanceConfig]
    model_aliases: dict[str, list[str]] = Field(default_factory=dict)
    model_prices: dict[str, ModelPriceConfig] = Field(default_factory=dict)
    push_auth_secret: str = ""
    enforce_push_auth: bool = False
    push_card_ttl_hours: float = 1.0

    @property
    def alias_lookup(self) -> dict[str, str]:
        """Reverse map {raw_model_id -> canonical_label}.

        Built from `model_aliases` on each call. Unknown raw ids default to
        themselves at read time. If the same raw id appears in multiple
        arrays, the last definition wins (dict insertion order = source order).
        """
        lookup: dict[str, str] = {}
        for label, raw_ids in self.model_aliases.items():
            for raw_id in raw_ids:
                lookup[raw_id] = label
        return lookup


from dataclasses import dataclass, field


@dataclass
class LoadResult:
    """Outcome of a fault-tolerant config load.

    - `config`: a V2Config containing ONLY the instances that passed validation
      (empty `providers` when the top-level structure is unusable).
    - `instance_errors`: instance_id -> error message for instances that were
      skipped (unknown type, bad mode, empty label, unknown field, missing
      required credentials, illegal instance_id). Does not affect other instances.
    - `config_error`: top-level error (file missing, JSON parse failure,
      top-level schema failure). Non-None means the whole config is unusable;
      `config` is an empty-config V2Config in that case.
    """
    config: V2Config
    instance_errors: dict[str, str] = field(default_factory=dict)
    config_error: str | None = None


def _empty_config() -> V2Config:
    return V2Config(providers={})


def _validate_instance(instance_id: str, cfg: V2InstanceConfig) -> None:
    """Validate one instance beyond what Pydantic already checks."""
    if not _INSTANCE_ID_RE.fullmatch(instance_id):
        raise ValueError(
            f"instance_id {instance_id!r} must match [A-Za-z0-9._-]+"
        )
    if not cfg.label.strip():
        raise ValueError(f"instance {instance_id!r}: label must be non-empty")

    # Check type is registered
    if cfg.type not in _SUPPORTED_TYPES:
        raise ValueError(
            f"instance {instance_id!r}: unknown type {cfg.type!r}"
        )

    # Check mode
    if cfg.mode not in ("fetch", "push"):
        raise ValueError(
            f"instance {instance_id!r}: mode must be 'fetch' or 'push', got {cfg.mode!r}"
        )

    # Check type+mode combo
    if cfg.mode not in _SUPPORTED_TYPES[cfg.type]:
        raise ValueError(
            f"instance {instance_id!r}: type {cfg.type!r} does not support mode {cfg.mode!r}"
        )

    # Check credentials: only allowed fields may be non-empty
    allowed = _ALLOWED_CREDENTIAL_FIELDS.get(cfg.type, set())
    for field_name in _KNOWN_FIELDS - {"type", "mode", "label", "order"}:
        value = getattr(cfg, field_name, "")
        if value and field_name not in allowed:
            raise ValueError(
                f"instance {instance_id!r}: type {cfg.type!r} does not accept field {field_name!r}"
            )

    # Check required credentials for fetch instances
    required = _REQUIRED_CREDENTIAL_FIELDS.get((cfg.type, cfg.mode), set())
    for field_name in required:
        value = getattr(cfg, field_name, "")
        if not value:
            raise ValueError(
                f"instance {instance_id!r}: type {cfg.type!r} mode {cfg.mode!r} "
                f"requires field {field_name!r}"
            )


def load_v2_config(path: str) -> LoadResult:
    """Load and validate a v2 config file, fault-tolerantly.

    Top-level errors (missing file, bad JSON, top-level schema failure) are
    returned as `config_error` with an empty config. Per-instance errors are
    collected in `instance_errors` and the offending instance is skipped;
    other instances still load.
    """
    config_path = Path(path)
    if not config_path.exists():
        return LoadResult(
            config=_empty_config(),
            config_error=f"config not found: {config_path}",
        )

    try:
        with open(config_path) as f:
            raw = json.load(f)
    except Exception as e:
        return LoadResult(
            config=_empty_config(),
            config_error=f"config JSON parse failed: {e}",
        )

    # Parse top-level structure leniently so a bad instance field does not
    # take down the whole config; validate each instance strictly afterwards.
    if not isinstance(raw, dict):
        return LoadResult(
            config=_empty_config(),
            config_error="config top-level must be a JSON object",
        )

    raw_providers = raw.get("providers", {})
    if not isinstance(raw_providers, dict):
        return LoadResult(
            config=_empty_config(),
            config_error="'providers' must be a JSON object",
        )

    # Top-level fields other than the known four are rejected as config_error.
    known_top = {"providers", "model_aliases", "push_auth_secret", "enforce_push_auth"}
    unknown_top = set(raw.keys()) - known_top
    if unknown_top:
        return LoadResult(
            config=_empty_config(),
            config_error=f"unknown top-level field(s): {sorted(unknown_top)}",
        )

    try:
        model_aliases = raw.get("model_aliases", {}) or {}
        if not isinstance(model_aliases, dict):
            return LoadResult(
                config=_empty_config(),
                config_error="'model_aliases' must be a JSON object",
            )
        push_auth_secret = str(raw.get("push_auth_secret", "") or "")
        enforce_push_auth = bool(raw.get("enforce_push_auth", False))
    except Exception as e:
        return LoadResult(config=_empty_config(), config_error=f"config invalid: {e}")

    instance_errors: dict[str, str] = {}
    valid_providers: dict[str, V2InstanceConfig] = {}
    for instance_id, inst_raw in raw_providers.items():
        if not isinstance(inst_raw, dict):
            instance_errors[instance_id] = (
                f"instance {instance_id!r}: must be a JSON object"
            )
            continue
        try:
            inst_cfg = V2InstanceConfig.model_validate(inst_raw)
            _validate_instance(instance_id, inst_cfg)
            valid_providers[instance_id] = inst_cfg
        except Exception as e:
            instance_errors[instance_id] = f"instance {instance_id!r}: {e}"

    try:
        config = V2Config(
            providers=valid_providers,
            model_aliases=model_aliases,
            push_auth_secret=push_auth_secret,
            enforce_push_auth=enforce_push_auth,
        )
    except Exception as e:
        # e.g. model_aliases values are not lists[str] — top-level schema error.
        return LoadResult(config=_empty_config(), config_error=f"config schema invalid: {e}")
    return LoadResult(
        config=config,
        instance_errors=instance_errors,
        config_error=None,
    )
