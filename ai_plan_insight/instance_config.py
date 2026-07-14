# ai_plan_insight/instance_config.py
"""v2 provider instance configuration.

Separate from the old config.py / config_loader.py. The old Config / ProviderConfig
models and the old load_config() function are not touched.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, field_validator, model_validator

from .config_loader import DEFAULT_CONFIG_PATH

_INSTANCE_ID_RE = re.compile(r"[A-Za-z0-9._-]+")

# Supported types and their allowed modes. Updated as new providers are added.
_SUPPORTED_TYPES: dict[str, set[str]] = {
    "kimi": {"fetch"},
    "bigmodel": {"fetch"},
    "bigmodel_international": {"fetch"},
    "aiping": {"fetch"},
    "huawei_cloud": {"fetch"},
    "zenmux": {"fetch"},
    "codex": {"fetch"},
    "codex_security": {"fetch"},
    "antigravity": {"fetch", "push"},
    "volcengine_ark": {"fetch"},
    "claude": {"push"},
    "grok": {"push"},
    "cursor": {"push"},
    "mimo_token_plan": {"push"},
}

# Credential fields each type is allowed to carry (beyond type/mode/label/order).
# Types not listed here default to the empty set (no credentials).
_ALLOWED_CREDENTIAL_FIELDS: dict[str, set[str]] = {
    "kimi": {"api_key"},
    "bigmodel": {"api_key"},
    "bigmodel_international": {"api_key"},
    "aiping": {"api_key"},
    "huawei_cloud": {"user_name", "access_key_id", "access_key_secret"},
    "zenmux": {"api_key"},
    "codex": {"api_key", "base_url"},
    "codex_security": {"api_key", "base_url"},
    "antigravity": {"api_key", "base_url", "admin_password"},
    "volcengine_ark": {"access_key_id", "access_key_secret"},
    "mimo_token_plan": {"cookie"},
}

# Required credential fields per (type, mode). Empty = no required creds.
_REQUIRED_CREDENTIAL_FIELDS: dict[tuple[str, str], set[str]] = {
    ("kimi", "fetch"): {"api_key"},
    ("bigmodel", "fetch"): {"api_key"},
    ("bigmodel_international", "fetch"): {"api_key"},
    ("aiping", "fetch"): {"api_key"},
    ("huawei_cloud", "fetch"): {"user_name", "access_key_id", "access_key_secret"},
    ("zenmux", "fetch"): {"api_key"},
    ("codex", "fetch"): {"api_key"},
    ("codex_security", "fetch"): {"api_key"},
    ("antigravity", "fetch"): {"api_key"},
    ("volcengine_ark", "fetch"): {"access_key_id", "access_key_secret"},
}

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


class V2Config(BaseModel):
    """Top-level v2 configuration."""
    model_config = {"extra": "forbid"}

    providers: dict[str, V2InstanceConfig]
    push_auth_secret: str = ""
    enforce_push_auth: bool = False


def resolve_v2_config_path(
    v2_config_path: str | None,
    config_path: str | None = None,
) -> Path:
    """Resolve the v2 config file path.

    Precedence:
    1. Explicit --v2-config (must not be the same file as the old config)
    2. Same directory as --config
    3. Same directory as the default config path
    """
    if v2_config_path is not None:
        p = Path(v2_config_path).resolve()
        # If the same path was passed for both, treat as "no explicit v2 path"
        if config_path is not None and p == Path(config_path).resolve():
            return p.parent / "config.v2.json"
        return Path(v2_config_path)
    if config_path is not None:
        return Path(config_path).resolve().parent / "config.v2.json"
    return DEFAULT_CONFIG_PATH.parent / "config.v2.json"


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


def load_v2_config(path: str) -> V2Config:
    """Load and validate a v2 config file. Raises on any error."""
    config_path = Path(path)
    if not config_path.exists():
        raise FileNotFoundError(f"v2 config not found: {config_path}")

    with open(config_path) as f:
        raw = json.load(f)

    config = V2Config.model_validate(raw)

    # Validate each instance
    for instance_id, inst_cfg in config.providers.items():
        _validate_instance(instance_id, inst_cfg)

    return config
