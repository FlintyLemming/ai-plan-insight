# Provider 多实例 v2 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a parallel Provider multi-instance system (v2) so that the same service type (e.g. Claude) can have multiple independent subscription cards, each with its own config, cache, DB rows, and push endpoint.

**Architecture:** Four new modules (`instance_config`, `provider_registry`, `provider_instances`, `provider_v2_store`) run alongside the existing system in the same process. A new `config.v2.json` declares instances with `instance_id` / `type` / `mode` / `label`. Three thin v2 API endpoints (`GET /api/usage/v2`, `GET /api/status/v2`, `POST /api/push/v2/{instance_id}`) serve a new "订阅余额（新）" frontend tab. Old system is untouched.

**Tech Stack:** Python 3.12, FastAPI, Pydantic v2, SQLite, vanilla HTML/JS frontend.

## Global Constraints

- `instance_id` must match `[A-Za-z0-9._-]+`
- `mode` is exactly `fetch` or `push`
- v2 config missing → v2 disabled, old system works
- v2 config invalid → v2 disabled with error in status, old system works
- Unknown push `instance_id` → `404`; push to fetch instance → `422`
- Push body schema reuses existing per-type request models (no new fields)
- v2 response adds `instance_id`, `type`, `instance_label` on top of existing card fields
- v2 DB tables: `provider_v2_snapshot` (UPSERT by `instance_id`) and `provider_v2_item`
- Push TTL: 30 minutes (same as old system)
- Fetch consecutive-failure fallback: show last success for first 2 failures, error card from 3rd
- Sorting: `(order, provider_title, instance_id)`
- v2 push auth shares one global token (`push_auth_secret` in v2 config)
- Do not modify any old tables, old endpoints, old runtime state, or old UI behavior
- `usage_point`, `source`, `/api/usage/report`, model usage chart/table are untouched

---

### Task 1: v2 Config Schema and Loader

**Files:**
- Create: `ai_plan_insight/instance_config.py`
- Create: `tests/test_instance_config.py`
- Create: `config.v2.json.example`

**Interfaces:**
- Consumes: nothing (standalone module)
- Produces: `V2InstanceConfig`, `V2Config`, `load_v2_config()`, `DEFAULT_V2_CONFIG_PATH`, `resolve_v2_config_path()`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_instance_config.py
import json
import pytest
from pathlib import Path
from ai_plan_insight.instance_config import (
    V2InstanceConfig,
    V2Config,
    load_v2_config,
    resolve_v2_config_path,
    _INSTANCE_ID_RE,
)


class TestInstanceIdRegex:
    def test_valid_ids(self):
        for vid in ["claude-personal", "bigmodel.work", "my_instance", "abc123", "a-b.c_d"]:
            assert _INSTANCE_ID_RE.fullmatch(vid)

    def test_invalid_ids(self):
        for vid in ["", "has space", "slash/bad", "colon:bad", "中文", "a@b"]:
            assert not _INSTANCE_ID_RE.fullmatch(vid)


class TestV2InstanceConfig:
    def test_minimal_push(self):
        cfg = V2InstanceConfig(type="claude", mode="push", label="个人号")
        assert cfg.type == "claude"
        assert cfg.mode == "push"
        assert cfg.label == "个人号"
        assert cfg.order == 999

    def test_fetch_with_credentials(self):
        cfg = V2InstanceConfig(
            type="bigmodel", mode="fetch", label="工作号",
            api_key="sk-xxx", order=20,
        )
        assert cfg.api_key == "sk-xxx"
        assert cfg.order == 20

    def test_empty_label_rejected(self):
        with pytest.raises(Exception):
            V2InstanceConfig(type="claude", mode="push", label="   ")

    def test_invalid_mode_rejected(self):
        with pytest.raises(Exception):
            V2InstanceConfig(type="claude", mode="invalid", label="test")


class TestV2Config:
    def test_empty_providers(self):
        cfg = V2Config(providers={})
        assert cfg.providers == {}
        assert cfg.push_auth_secret == ""
        assert cfg.enforce_push_auth is False

    def test_full_config(self):
        raw = {
            "providers": {
                "claude-personal": {
                    "type": "claude",
                    "mode": "push",
                    "label": "个人号",
                    "order": 12,
                },
                "bigmodel-work": {
                    "type": "bigmodel",
                    "mode": "fetch",
                    "label": "工作账号",
                    "api_key": "sk-xxx",
                    "order": 20,
                },
            },
            "push_auth_secret": "secret123",
            "enforce_push_auth": True,
        }
        cfg = V2Config.model_validate(raw)
        assert len(cfg.providers) == 2
        assert cfg.providers["claude-personal"].type == "claude"
        assert cfg.providers["bigmodel-work"].api_key == "sk-xxx"
        assert cfg.push_auth_secret == "secret123"
        assert cfg.enforce_push_auth is True


class TestLoadV2Config:
    def test_load_valid(self, tmp_path: Path):
        data = {
            "providers": {
                "claude-personal": {
                    "type": "claude",
                    "mode": "push",
                    "label": "个人号",
                }
            }
        }
        p = tmp_path / "config.v2.json"
        p.write_text(json.dumps(data))
        cfg = load_v2_config(str(p))
        assert "claude-personal" in cfg.providers

    def test_file_not_found(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError):
            load_v2_config(str(tmp_path / "nope.json"))

    def test_invalid_json(self, tmp_path: Path):
        p = tmp_path / "bad.json"
        p.write_text("{bad json}")
        with pytest.raises(Exception):
            load_v2_config(str(p))

    def test_invalid_instance_id(self, tmp_path: Path):
        data = {
            "providers": {
                "has space": {
                    "type": "claude",
                    "mode": "push",
                    "label": "test",
                }
            }
        }
        p = tmp_path / "config.v2.json"
        p.write_text(json.dumps(data))
        with pytest.raises(ValueError, match="instance_id"):
            load_v2_config(str(p))

    def test_unknown_type(self, tmp_path: Path):
        data = {
            "providers": {
                "test-inst": {
                    "type": "nonexistent_provider",
                    "mode": "push",
                    "label": "test",
                }
            }
        }
        p = tmp_path / "config.v2.json"
        p.write_text(json.dumps(data))
        with pytest.raises(ValueError, match="type"):
            load_v2_config(str(p))

    def test_unknown_mode(self, tmp_path: Path):
        data = {
            "providers": {
                "test-inst": {
                    "type": "claude",
                    "mode": "invalid",
                    "label": "test",
                }
            }
        }
        p = tmp_path / "config.v2.json"
        p.write_text(json.dumps(data))
        with pytest.raises(ValueError, match="mode"):
            load_v2_config(str(p))

    def test_unsupported_type_mode_combo(self, tmp_path: Path):
        # claude only supports push, not fetch
        data = {
            "providers": {
                "claude-fetch": {
                    "type": "claude",
                    "mode": "fetch",
                    "label": "test",
                }
            }
        }
        p = tmp_path / "config.v2.json"
        p.write_text(json.dumps(data))
        with pytest.raises(ValueError, match="type.*mode"):
            load_v2_config(str(p))

    def test_empty_label_rejected(self, tmp_path: Path):
        data = {
            "providers": {
                "test-inst": {
                    "type": "claude",
                    "mode": "push",
                    "label": "   ",
                }
            }
        }
        p = tmp_path / "config.v2.json"
        p.write_text(json.dumps(data))
        with pytest.raises(ValueError, match="label"):
            load_v2_config(str(p))

    def test_unknown_field_rejected(self, tmp_path: Path):
        data = {
            "providers": {
                "test-inst": {
                    "type": "claude",
                    "mode": "push",
                    "label": "test",
                    "totally_bogus_field": True,
                }
            }
        }
        p = tmp_path / "config.v2.json"
        p.write_text(json.dumps(data))
        with pytest.raises(Exception):
            load_v2_config(str(p))


class TestResolveV2ConfigPath:
    def test_explicit_path(self, tmp_path: Path):
        p = tmp_path / "custom.json"
        assert resolve_v2_config_path(str(p)) == p

    def test_from_config_dir(self, tmp_path: Path):
        # When config_path points to a dir's config.json, v2 should be in the same dir
        config_p = tmp_path / "config.json"
        config_p.write_text("{}")
        result = resolve_v2_config_path(str(config_p), config_path=str(config_p))
        assert result == tmp_path / "config.v2.json"

    def test_default_path(self):
        from ai_plan_insight.config_loader import DEFAULT_CONFIG_PATH
        result = resolve_v2_config_path(None, config_path=None)
        assert result == DEFAULT_CONFIG_PATH.parent / "config.v2.json"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/flintylemming/Projects/ai-plan-insight && python -m pytest tests/test_instance_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ai_plan_insight.instance_config'`

- [ ] **Step 3: Implement `instance_config.py`**

```python
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
    1. Explicit --v2-config
    2. Same directory as --config
    3. Same directory as the default config path
    """
    if v2_config_path is not None:
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/flintylemming/Projects/ai-plan-insight && python -m pytest tests/test_instance_config.py -v`
Expected: all PASS

- [ ] **Step 5: Create `config.v2.json.example`**

```json
{
  "providers": {
    "claude-personal": {
      "type": "claude",
      "mode": "push",
      "label": "个人号",
      "order": 12
    },
    "claude-work": {
      "type": "claude",
      "mode": "push",
      "label": "工作号",
      "order": 13
    },
    "bigmodel-personal": {
      "type": "bigmodel",
      "mode": "fetch",
      "label": "个人账号",
      "api_key": "YOUR_BIGMODEL_API_KEY",
      "order": 20
    }
  },
  "push_auth_secret": "your-secret-token",
  "enforce_push_auth": true
}
```

- [ ] **Step 6: Commit**

```bash
git add ai_plan_insight/instance_config.py tests/test_instance_config.py config.v2.json.example
git commit -m "feat(v2): add instance config schema and loader"
```

---

### Task 2: Provider Type Registry

**Files:**
- Create: `ai_plan_insight/provider_registry.py`
- Create: `tests/test_provider_registry.py`

**Interfaces:**
- Consumes: `V2InstanceConfig` from Task 1; existing provider classes; existing push request models from `api_schemas.py`
- Produces: `ProviderTypeEntry`, `get_type_entry()`, `build_fetch_provider()`, `get_push_request_model()`, `convert_push_payload()`, `get_type_display_name()`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_provider_registry.py
import pytest
from ai_plan_insight.instance_config import V2InstanceConfig
from ai_plan_insight.provider_registry import (
    get_type_entry,
    build_fetch_provider,
    get_push_request_model,
    convert_push_payload,
    get_type_display_name,
    make_card_title,
)
from ai_plan_insight.config import ProviderConfig
from ai_plan_insight.api_schemas import (
    ClaudePushRequest,
    GrokPushRequest,
    CursorPushRequest,
    MimoPushRequest,
    AntigravityPushRequest,
    UsageResponse,
)


class TestGetTypeName:
    def test_known_types(self):
        assert get_type_display_name("claude") == "Claude 订阅"
        assert get_type_display_name("bigmodel") == "GLM Coding Plan"
        assert get_type_display_name("kimi") == "Kimi Coding Plan"
        assert get_type_display_name("grok") == "Grok 订阅"
        assert get_type_display_name("cursor") == "Cursor"
        assert get_type_display_name("mimo_token_plan") == "小米 MiMo Token Plan"

    def test_unknown_type(self):
        assert get_type_display_name("nonexistent") == "nonexistent"


class TestMakeCardTitle:
    def test_with_label(self):
        assert make_card_title("claude", "工作号") == "Claude 订阅 · 工作号"

    def test_with_label_bigmodel(self):
        assert make_card_title("bigmodel", "个人账号") == "GLM Coding Plan · 个人账号"


class TestGetTypeEntry:
    def test_known_type(self):
        entry = get_type_entry("claude")
        assert entry is not None
        assert "push" in entry.modes

    def test_fetch_type(self):
        entry = get_type_entry("bigmodel")
        assert entry is not None
        assert "fetch" in entry.modes

    def test_dual_mode_type(self):
        entry = get_type_entry("antigravity")
        assert entry is not None
        assert "fetch" in entry.modes
        assert "push" in entry.modes

    def test_unknown_type(self):
        assert get_type_entry("nonexistent") is None


class TestBuildFetchProvider:
    def test_build_kimi(self):
        cfg = V2InstanceConfig(
            type="kimi", mode="fetch", label="test", api_key="sk-test",
        )
        provider = build_fetch_provider("kimi-test", cfg)
        from ai_plan_insight.providers.kimi import KimiProvider
        assert isinstance(provider, KimiProvider)

    def test_build_bigmodel(self):
        cfg = V2InstanceConfig(
            type="bigmodel", mode="fetch", label="test", api_key="sk-test",
        )
        provider = build_fetch_provider("bm-test", cfg)
        from ai_plan_insight.providers.bigmodel import BigModelProvider
        assert isinstance(provider, BigModelProvider)

    def test_push_type_raises(self):
        cfg = V2InstanceConfig(type="claude", mode="push", label="test")
        with pytest.raises(ValueError, match="fetch"):
            build_fetch_provider("claude-test", cfg)


class TestGetPushRequestModel:
    def test_claude(self):
        model = get_push_request_model("claude")
        assert model is ClaudePushRequest

    def test_grok(self):
        model = get_push_request_model("grok")
        assert model is GrokPushRequest

    def test_cursor(self):
        model = get_push_request_model("cursor")
        assert model is CursorPushRequest

    def test_mimo(self):
        model = get_push_request_model("mimo_token_plan")
        assert model is MimoPushRequest

    def test_antigravity(self):
        model = get_push_request_model("antigravity")
        assert model is AntigravityPushRequest

    def test_fetch_only_returns_none(self):
        assert get_push_request_model("kimi") is None

    def test_unknown_returns_none(self):
        assert get_push_request_model("nonexistent") is None


class TestConvertPushPayload:
    def test_claude(self):
        payload = ClaudePushRequest(
            seven_day={"utilization": 45.2, "resets_at": "2026-07-08T12:00:00Z"},
            five_hour={"utilization": 12.8, "resets_at": "2026-07-01T15:00:00Z"},
        )
        resp = convert_push_payload("claude", "claude-work", "Claude 订阅 · 工作号", payload)
        assert isinstance(resp, UsageResponse)
        assert resp.provider == "Claude 订阅 · 工作号"
        assert len(resp.limits) == 2

    def test_grok(self):
        payload = GrokPushRequest(
            weekly={"utilization": 50.0, "resets_at": "2026-07-08T00:00:00Z"},
            monthly={"used": 100.0, "limit": 200.0, "resets_at": "2026-08-01T00:00:00Z"},
            plan="SuperGrok",
        )
        resp = convert_push_payload("grok", "grok-work", "Grok 订阅 · 工作号", payload)
        assert resp.provider == "Grok 订阅 · 工作号"
        assert resp.membership_level == "SuperGrok"
        assert len(resp.limits) == 2

    def test_cursor(self):
        payload = CursorPushRequest(
            membership="Pro",
            billing_start="2026-07-01",
            billing_end="2026-08-01",
            autoPercentUsed=30.5,
            apiPercentUsed=10.2,
        )
        resp = convert_push_payload("cursor", "cursor-work", "Cursor · 工作号", payload)
        assert resp.provider == "Cursor · 工作号"
        assert resp.membership_level == "Pro"

    def test_mimo_title_not_overridden(self):
        """v2 MiMo title comes from config, not from the request body."""
        payload = MimoPushRequest(
            provider="小米 MiMo Token Plan",
            limits=[],
            balances={"total": "100"},
        )
        resp = convert_push_payload(
            "mimo_token_plan", "mimo-work",
            "小米 MiMo Token Plan · 工作号", payload,
        )
        assert resp.provider == "小米 MiMo Token Plan · 工作号"

    def test_antigravity(self):
        payload = AntigravityPushRequest(
            gemini_3_1_pro_percentage=50.0,
            gemini_3_1_pro_reset_time="2026-07-01T15:00:00Z",
            gemini_3_flash_percentage=30.0,
            gemini_3_flash_reset_time="2026-07-01T15:00:00Z",
            claude_series_percentage=20.0,
            claude_series_reset_time="2026-07-01T15:00:00Z",
        )
        resp = convert_push_payload(
            "antigravity", "ag-test", "Antigravity · 测试", payload,
        )
        assert resp.provider == "Antigravity · 测试"
        assert len(resp.limits) == 3
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/flintylemming/Projects/ai-plan-insight && python -m pytest tests/test_provider_registry.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ai_plan_insight.provider_registry'`

- [ ] **Step 3: Implement `provider_registry.py`**

```python
# ai_plan_insight/provider_registry.py
"""Centralized provider type registry for v2.

Maps type strings to their display names, supported modes, fetch factories,
push request models, and push-to-UsageResponse converters. Existing provider
classes and push request models are reused without modification.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Type

from .config import ProviderConfig
from .instance_config import V2InstanceConfig
from .api_schemas import (
    UsageResponse,
    LimitResponse,
    AntigravityPushRequest,
    CursorPushRequest,
    MimoPushRequest,
    ClaudePushRequest,
    GrokPushRequest,
)
from .providers.kimi import KimiProvider
from .providers.bigmodel import BigModelProvider
from .providers.bigmodel_international import BigModelInternationalProvider
from .providers.aiping import AipingProvider
from .providers.huawei_cloud import HuaweiCloudBssProvider
from .providers.zenmux import ZenMuxProvider
from .providers.codex import CodexProvider, CodexSecurityProvider
from .providers.antigravity import AntigravityProvider
from .providers.volcengine_ark import VolcEngineArkProvider
from .providers.base import BaseProvider


_TYPE_DISPLAY_NAMES: dict[str, str] = {
    "kimi": "Kimi Coding Plan",
    "bigmodel": "GLM Coding Plan",
    "bigmodel_international": "白嫖 GLM Coding Plan 国际版",
    "aiping": "AIPing",
    "huawei_cloud": "华为云余额",
    "zenmux": "ZenMux",
    "codex": "自购 Codex 中转站",
    "codex_security": "白嫖 Codex Security 中转",
    "antigravity": "Antigravity",
    "volcengine_ark": "火山方舟 Coding Plan",
    "cursor": "Cursor",
    "claude": "Claude 订阅",
    "grok": "Grok 订阅",
    "mimo_token_plan": "小米 MiMo Token Plan",
}


_FETCH_FACTORIES: dict[str, Type[BaseProvider]] = {
    "kimi": KimiProvider,
    "bigmodel": BigModelProvider,
    "bigmodel_international": BigModelInternationalProvider,
    "aiping": AipingProvider,
    "huawei_cloud": HuaweiCloudBssProvider,
    "zenmux": ZenMuxProvider,
    "codex": CodexProvider,
    "codex_security": CodexSecurityProvider,
    "antigravity": AntigravityProvider,
    "volcengine_ark": VolcEngineArkProvider,
}


def _to_provider_config(inst: V2InstanceConfig) -> ProviderConfig:
    """Convert a v2 instance config to the old ProviderConfig for reuse."""
    return ProviderConfig(
        api_key=inst.api_key,
        base_url=inst.base_url,
        user_name=inst.user_name,
        access_key_id=inst.access_key_id,
        access_key_secret=inst.access_key_secret,
        admin_password=inst.admin_password,
        cookie=inst.cookie,
        order=inst.order,
    )


def _convert_claude(instance_id: str, title: str, payload: ClaudePushRequest) -> UsageResponse:
    return UsageResponse(
        provider=title,
        limits=[
            LimitResponse(
                duration=5, time_unit="小时", limit="100",
                used=str(int(payload.five_hour.utilization)),
                remaining=str(int(100 - payload.five_hour.utilization)),
                reset_time=payload.five_hour.resets_at,
            ),
            LimitResponse(
                duration=7, time_unit="天", limit="100",
                used=str(int(payload.seven_day.utilization)),
                remaining=str(int(100 - payload.seven_day.utilization)),
                reset_time=payload.seven_day.resets_at,
            ),
        ],
    )


def _convert_grok(instance_id: str, title: str, payload: GrokPushRequest) -> UsageResponse:
    plan = (payload.plan or "").strip() or None
    limits: list[LimitResponse] = []
    if payload.monthly is not None:
        used = int(payload.monthly.used)
        limit_val = int(payload.monthly.limit)
        remaining = max(limit_val - used, 0)
        limits.append(LimitResponse(
            duration=1, time_unit="月",
            limit=str(limit_val), used=str(used), remaining=str(remaining),
            reset_time=payload.monthly.resets_at,
        ))
    if payload.weekly is not None:
        limits.append(LimitResponse(
            duration=7, time_unit="天", limit="100",
            used=str(int(payload.weekly.utilization)),
            remaining=str(int(100 - payload.weekly.utilization)),
            reset_time=payload.weekly.resets_at,
        ))
    return UsageResponse(
        provider=title,
        membership_level=plan,
        limits=limits,
    )


def _convert_cursor(instance_id: str, title: str, payload: CursorPushRequest) -> UsageResponse:
    from datetime import datetime as dt
    end_dt = dt.fromisoformat(payload.billing_end.replace("Z", "+00:00"))
    end_display = end_dt.strftime("%Y-%m-%d")

    def pct_to_limit(label: str, pct: float) -> LimitResponse:
        return LimitResponse(
            duration=1, time_unit=label, limit="100",
            used=f"{pct:.2f}", remaining=f"{100 - pct:.2f}",
            reset_time=payload.billing_end, limit_type="PERCENT",
        )

    return UsageResponse(
        provider=title,
        membership_level=payload.membership,
        limits=[
            pct_to_limit("Auto + Composer 用量", payload.autoPercentUsed or 0),
            pct_to_limit("API 用量", payload.apiPercentUsed or 0),
        ],
        balances={"到期时间": end_display},
    )


def _convert_mimo(instance_id: str, title: str, payload: MimoPushRequest) -> UsageResponse:
    return UsageResponse(
        provider=title,  # v2 title from config, NOT payload.provider
        user_id=payload.user_id,
        membership_level=payload.membership_level,
        limits=payload.limits,
        balances=payload.balances,
    )


def _convert_antigravity(instance_id: str, title: str, payload: AntigravityPushRequest) -> UsageResponse:
    return UsageResponse(
        provider=title,
        membership_level="Gemini Ultra",
        limits=[
            LimitResponse(
                duration=5, time_unit="小时 (Gemini 3.1 Pro)",
                limit="100",
                used=str(int(payload.gemini_3_1_pro_percentage)),
                remaining=str(int(100 - payload.gemini_3_1_pro_percentage)),
                reset_time=payload.gemini_3_1_pro_reset_time,
            ),
            LimitResponse(
                duration=5, time_unit="小时 (Gemini 3 Flash)",
                limit="100",
                used=str(int(payload.gemini_3_flash_percentage)),
                remaining=str(int(100 - payload.gemini_3_flash_percentage)),
                reset_time=payload.gemini_3_flash_reset_time,
            ),
            LimitResponse(
                duration=5, time_unit="小时 (Claude 系列)",
                limit="100",
                used=str(int(payload.claude_series_percentage)),
                remaining=str(int(100 - payload.claude_series_percentage)),
                reset_time=payload.claude_series_reset_time,
            ),
        ],
    )


_PUSH_REQUEST_MODELS: dict[str, Type] = {
    "claude": ClaudePushRequest,
    "grok": GrokPushRequest,
    "cursor": CursorPushRequest,
    "mimo_token_plan": MimoPushRequest,
    "antigravity": AntigravityPushRequest,
}

_PUSH_CONVERTERS = {
    "claude": _convert_claude,
    "grok": _convert_grok,
    "cursor": _convert_cursor,
    "mimo_token_plan": _convert_mimo,
    "antigravity": _convert_antigravity,
}


@dataclass
class ProviderTypeEntry:
    """Metadata for one registered provider type."""
    type_name: str
    display_name: str
    modes: set[str]


def get_type_display_name(type_name: str) -> str:
    return _TYPE_DISPLAY_NAMES.get(type_name, type_name)


def make_card_title(type_name: str, label: str) -> str:
    return f"{get_type_display_name(type_name)} · {label}"


def get_type_entry(type_name: str) -> ProviderTypeEntry | None:
    from .instance_config import _SUPPORTED_TYPES
    modes = _SUPPORTED_TYPES.get(type_name)
    if modes is None:
        return None
    return ProviderTypeEntry(
        type_name=type_name,
        display_name=get_type_display_name(type_name),
        modes=set(modes),
    )


def build_fetch_provider(instance_id: str, cfg: V2InstanceConfig) -> BaseProvider:
    """Create a fetch provider instance from a v2 instance config."""
    factory = _FETCH_FACTORIES.get(cfg.type)
    if factory is None:
        raise ValueError(f"type {cfg.type!r} does not support fetch mode")
    prov_config = _to_provider_config(cfg)
    return factory(prov_config)


def get_push_request_model(type_name: str) -> Type | None:
    """Return the Pydantic request model for a push type, or None."""
    return _PUSH_REQUEST_MODELS.get(type_name)


def convert_push_payload(
    type_name: str,
    instance_id: str,
    title: str,
    payload: Any,
) -> UsageResponse:
    """Convert a typed push payload into a UsageResponse with the v2 title."""
    converter = _PUSH_CONVERTERS.get(type_name)
    if converter is None:
        raise ValueError(f"no push converter for type {type_name!r}")
    return converter(instance_id, title, payload)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/flintylemming/Projects/ai-plan-insight && python -m pytest tests/test_provider_registry.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add ai_plan_insight/provider_registry.py tests/test_provider_registry.py
git commit -m "feat(v2): add provider type registry with fetch factory and push converters"
```

---

### Task 3: v2 Database Store

**Files:**
- Create: `ai_plan_insight/provider_v2_store.py`
- Create: `tests/test_provider_v2_store.py`

**Interfaces:**
- Consumes: `UsageResponse` from `api_schemas.py`
- Produces: `init_v2_schema()`, `upsert_v2_snapshot()`, `load_v2_snapshots()`, `init_v2_db()`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_provider_v2_store.py
import json
import sqlite3
import pytest
from datetime import datetime
from pathlib import Path
from ai_plan_insight.api_schemas import UsageResponse, LimitResponse
from ai_plan_insight import provider_v2_store


@pytest.fixture
def db(tmp_path: Path) -> sqlite3.Connection:
    p = tmp_path / "test_v2.db"
    conn = sqlite3.connect(p)
    provider_v2_store.init_v2_schema(conn)
    conn.commit()
    yield conn
    conn.close()


def _make_usage(**overrides) -> UsageResponse:
    defaults = {
        "provider": "Claude 订阅 · 工作号",
        "limits": [
            LimitResponse(
                duration=5, time_unit="小时", limit="100",
                used="30", remaining="70", reset_time="2026-07-01T15:00:00Z",
            ),
        ],
    }
    defaults.update(overrides)
    return UsageResponse(**defaults)


class TestInitSchema:
    def test_tables_created(self, db: sqlite3.Connection):
        tables = {r[0] for r in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        assert "provider_v2_snapshot" in tables
        assert "provider_v2_item" in tables

    def test_idempotent(self, db: sqlite3.Connection):
        # Calling init again should not fail
        provider_v2_store.init_v2_schema(db)
        db.commit()


class TestUpsertV2Snapshot:
    def test_insert_and_update(self, db: sqlite3.Connection):
        usage = _make_usage()
        provider_v2_store.upsert_v2_snapshot(
            db, "claude-work", "claude", "push", "工作号", usage,
        )
        db.commit()
        rows = db.execute("SELECT * FROM provider_v2_snapshot").fetchall()
        assert len(rows) == 1
        assert rows[0][0] == "claude-work"  # instance_id

        # Update same instance
        usage2 = _make_usage(provider="Claude 订阅 · 工作号 (updated)")
        provider_v2_store.upsert_v2_snapshot(
            db, "claude-work", "claude", "push", "工作号", usage2,
        )
        db.commit()
        rows = db.execute("SELECT * FROM provider_v2_snapshot").fetchall()
        assert len(rows) == 1  # still one row (UPSERT)

    def test_different_instances_independent(self, db: sqlite3.Connection):
        usage1 = _make_usage()
        usage2 = _make_usage(provider="Claude 订阅 · 个人号")
        provider_v2_store.upsert_v2_snapshot(
            db, "claude-work", "claude", "push", "工作号", usage1,
        )
        provider_v2_store.upsert_v2_snapshot(
            db, "claude-personal", "claude", "push", "个人号", usage2,
        )
        db.commit()
        rows = db.execute("SELECT instance_id FROM provider_v2_snapshot ORDER BY instance_id").fetchall()
        assert [r[0] for r in rows] == ["claude-personal", "claude-work"]

    def test_items_replaced_on_upsert(self, db: sqlite3.Connection):
        usage = _make_usage()
        provider_v2_store.upsert_v2_snapshot(
            db, "claude-work", "claude", "push", "工作号", usage,
        )
        db.commit()
        count1 = db.execute("SELECT COUNT(*) FROM provider_v2_item").fetchone()[0]
        assert count1 > 0

        # Upsert again — old items should be gone, new ones inserted
        provider_v2_store.upsert_v2_snapshot(
            db, "claude-work", "claude", "push", "工作号", usage,
        )
        db.commit()
        count2 = db.execute("SELECT COUNT(*) FROM provider_v2_item").fetchone()[0]
        assert count2 == count1


class TestLoadV2Snapshots:
    def test_load_empty(self, db: sqlite3.Connection):
        rows = provider_v2_store.load_v2_snapshots(db, {"claude-work"})
        assert rows == []

    def test_load_matching_instances(self, db: sqlite3.Connection):
        usage = _make_usage()
        provider_v2_store.upsert_v2_snapshot(
            db, "claude-work", "claude", "push", "工作号", usage,
        )
        db.commit()
        rows = provider_v2_store.load_v2_snapshots(
            db, {"claude-work", "claude-personal"},
        )
        assert len(rows) == 1
        instance_id, type_name, mode, label, recorded_at, usage_resp = rows[0]
        assert instance_id == "claude-work"
        assert type_name == "claude"
        assert mode == "push"
        assert isinstance(usage_resp, UsageResponse)

    def test_skip_instance_not_in_config(self, db: sqlite3.Connection):
        usage = _make_usage()
        provider_v2_store.upsert_v2_snapshot(
            db, "old-removed", "claude", "push", "旧号", usage,
        )
        db.commit()
        rows = provider_v2_store.load_v2_snapshots(db, {"claude-work"})
        assert len(rows) == 0

    def test_skip_type_mismatch(self, db: sqlite3.Connection):
        usage = _make_usage()
        provider_v2_store.upsert_v2_snapshot(
            db, "test-inst", "claude", "push", "test", usage,
        )
        db.commit()
        # Request with different type
        rows = provider_v2_store.load_v2_snapshots(
            db, {"test-inst"},
            type_mode_map={"test-inst": ("grok", "push")},
        )
        assert len(rows) == 0

    def test_skip_mode_mismatch(self, db: sqlite3.Connection):
        usage = _make_usage()
        provider_v2_store.upsert_v2_snapshot(
            db, "test-inst", "antigravity", "push", "test", usage,
        )
        db.commit()
        rows = provider_v2_store.load_v2_snapshots(
            db, {"test-inst"},
            type_mode_map={"test-inst": ("antigravity", "fetch")},
        )
        assert len(rows) == 0

    def test_label_from_config_overrides_stored(self, db: sqlite3.Connection):
        usage = _make_usage()
        provider_v2_store.upsert_v2_snapshot(
            db, "claude-work", "claude", "push", "旧标签", usage,
        )
        db.commit()
        rows = provider_v2_store.load_v2_snapshots(
            db, {"claude-work"},
            label_map={"claude-work": "新标签"},
        )
        assert len(rows) == 1
        _, _, _, label, _, _ = rows[0]
        assert label == "新标签"

    def test_corrupt_row_skipped(self, db: sqlite3.Connection):
        # Insert a row with invalid JSON
        db.execute(
            "INSERT INTO provider_v2_snapshot "
            "(instance_id, type, mode, label, recorded_at, raw_json) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("bad-row", "claude", "push", "test", "now", "{bad json"),
        )
        db.commit()
        # Also insert a good row
        usage = _make_usage()
        provider_v2_store.upsert_v2_snapshot(
            db, "good-row", "claude", "push", "test", usage,
        )
        db.commit()
        rows = provider_v2_store.load_v2_snapshots(
            db, {"bad-row", "good-row"},
        )
        assert len(rows) == 1
        assert rows[0][0] == "good-row"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/flintylemming/Projects/ai-plan-insight && python -m pytest tests/test_provider_v2_store.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ai_plan_insight.provider_v2_store'`

- [ ] **Step 3: Implement `provider_v2_store.py`**

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/flintylemming/Projects/ai-plan-insight && python -m pytest tests/test_provider_v2_store.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add ai_plan_insight/provider_v2_store.py tests/test_provider_v2_store.py
git commit -m "feat(v2): add v2 database store with UPSERT snapshots and restore"
```

---

### Task 4: v2 Runtime Manager (Fetch Scheduling + Push Dispatch)

**Files:**
- Create: `ai_plan_insight/provider_instances.py`
- Create: `tests/test_provider_instances.py`

**Interfaces:**
- Consumes: `V2Config` from Task 1; `provider_registry` from Task 2; `provider_v2_store` from Task 3; `usage_store` (for DB path); `_fetch_one` logic from `web.py`
- Produces: `V2RuntimeManager` class with methods: `start()`, `stop()`, `handle_push()`, `get_usage()`, `get_status()`, `restore_snapshots()`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_provider_instances.py
import asyncio
import json
import sqlite3
import pytest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock

from ai_plan_insight.instance_config import V2Config, V2InstanceConfig
from ai_plan_insight.api_schemas import (
    UsageResponse, LimitResponse, ClaudePushRequest, GrokPushRequest,
)
from ai_plan_insight.provider_instances import V2RuntimeManager


@pytest.fixture
def v2_config() -> V2Config:
    return V2Config.model_validate({
        "providers": {
            "claude-personal": {
                "type": "claude", "mode": "push", "label": "个人号", "order": 12,
            },
            "claude-work": {
                "type": "claude", "mode": "push", "label": "工作号", "order": 13,
            },
        },
        "push_auth_secret": "test-secret",
        "enforce_push_auth": True,
    })


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "v2_test.db"


@pytest.fixture
def manager(v2_config: V2Config, db_path: Path) -> V2RuntimeManager:
    return V2RuntimeManager(v2_config, db_path)


class TestV2RuntimeManagerInit:
    def test_config_loaded(self, manager: V2RuntimeManager, v2_config: V2Config):
        assert manager.config is v2_config
        assert manager.enabled is True

    def test_no_fetch_instances(self, manager: V2RuntimeManager):
        # All instances are push, so no fetch loop should be needed
        assert manager.has_fetch_instances is False

    def test_with_fetch_instances(self, db_path: Path):
        config = V2Config.model_validate({
            "providers": {
                "bigmodel-test": {
                    "type": "bigmodel", "mode": "fetch", "label": "test",
                    "api_key": "sk-test", "order": 20,
                },
            },
        })
        mgr = V2RuntimeManager(config, db_path)
        assert mgr.has_fetch_instances is True


class TestHandlePush:
    def test_push_success(self, manager: V2RuntimeManager):
        payload = ClaudePushRequest(
            seven_day={"utilization": 45.2, "resets_at": "2026-07-08T12:00:00Z"},
            five_hour={"utilization": 12.8, "resets_at": "2026-07-01T15:00:00Z"},
        )
        result = asyncio.get_event_loop().run_until_complete(
            manager.handle_push("claude-work", payload)
        )
        assert result["status"] == "ok"
        assert result["instance_id"] == "claude-work"

    def test_push_unknown_instance(self, manager: V2RuntimeManager):
        payload = ClaudePushRequest(
            seven_day={"utilization": 10.0, "resets_at": "2026-07-08T12:00:00Z"},
            five_hour={"utilization": 5.0, "resets_at": "2026-07-01T15:00:00Z"},
        )
        with pytest.raises(ValueError, match="not registered"):
            asyncio.get_event_loop().run_until_complete(
                manager.handle_push("unknown-instance", payload)
            )

    def test_push_to_fetch_instance(self, db_path: Path):
        config = V2Config.model_validate({
            "providers": {
                "bigmodel-test": {
                    "type": "bigmodel", "mode": "fetch", "label": "test",
                    "api_key": "sk-test",
                },
            },
        })
        mgr = V2RuntimeManager(config, db_path)
        payload = ClaudePushRequest(
            seven_day={"utilization": 10.0, "resets_at": "2026-07-08T12:00:00Z"},
            five_hour={"utilization": 5.0, "resets_at": "2026-07-01T15:00:00Z"},
        )
        with pytest.raises(ValueError, match="fetch"):
            asyncio.get_event_loop().run_until_complete(
                mgr.handle_push("bigmodel-test", payload)
            )

    def test_two_instances_independent(self, manager: V2RuntimeManager):
        loop = asyncio.get_event_loop()
        for inst_id in ["claude-personal", "claude-work"]:
            payload = ClaudePushRequest(
                seven_day={"utilization": 30.0, "resets_at": "2026-07-08T12:00:00Z"},
                five_hour={"utilization": 10.0, "resets_at": "2026-07-01T15:00:00Z"},
            )
            loop.run_until_complete(manager.handle_push(inst_id, payload))

        usage = manager.get_usage()
        assert len(usage) == 2
        providers = {u.provider for u in usage}
        assert "Claude 订阅 · 个人号" in providers
        assert "Claude 订阅 · 工作号" in providers

    def test_push_ttl_expires(self, manager: V2RuntimeManager):
        loop = asyncio.get_event_loop()
        payload = ClaudePushRequest(
            seven_day={"utilization": 30.0, "resets_at": "2026-07-08T12:00:00Z"},
            five_hour={"utilization": 10.0, "resets_at": "2026-07-01T15:00:00Z"},
        )
        loop.run_until_complete(manager.handle_push("claude-work", payload))

        # Manually expire the push
        manager._pushed_at["claude-work"] = datetime.now().astimezone() - timedelta(minutes=31)
        usage = manager.get_usage()
        assert len(usage) == 0  # expired, not shown


class TestGetUsage:
    def test_empty_when_no_data(self, manager: V2RuntimeManager):
        assert manager.get_usage() == []

    def test_sorted_by_order(self, manager: V2RuntimeManager):
        loop = asyncio.get_event_loop()
        for inst_id in ["claude-work", "claude-personal"]:
            payload = ClaudePushRequest(
                seven_day={"utilization": 30.0, "resets_at": "2026-07-08T12:00:00Z"},
                five_hour={"utilization": 10.0, "resets_at": "2026-07-01T15:00:00Z"},
            )
            loop.run_until_complete(manager.handle_push(inst_id, payload))

        usage = manager.get_usage()
        # order 12 (personal) before order 13 (work)
        assert usage[0].provider == "Claude 订阅 · 个人号"
        assert usage[1].provider == "Claude 订阅 · 工作号"


class TestGetStatus:
    def test_enabled(self, manager: V2RuntimeManager):
        status = manager.get_status()
        assert status["enabled"] is True
        assert status["config_error"] is None

    def test_disabled_config_error(self, db_path: Path):
        mgr = V2RuntimeManager.__new__(V2RuntimeManager)
        mgr._config = None
        mgr._config_error = "bad config"
        mgr._enabled = False
        mgr._last_updated = None
        mgr._pushed_results = {}
        mgr._pushed_at = {}
        mgr._fetch_results = {}
        mgr._consecutive_failures = {}
        mgr._prev_results = {}
        mgr._db_path = db_path
        status = mgr.get_status()
        assert status["enabled"] is False
        assert status["config_error"] == "bad config"


class TestV2ResponseFields:
    def test_response_has_v2_fields(self, manager: V2RuntimeManager):
        loop = asyncio.get_event_loop()
        payload = ClaudePushRequest(
            seven_day={"utilization": 30.0, "resets_at": "2026-07-08T12:00:00Z"},
            five_hour={"utilization": 10.0, "resets_at": "2026-07-01T15:00:00Z"},
        )
        loop.run_until_complete(manager.handle_push("claude-work", payload))
        usage = manager.get_usage()
        assert len(usage) == 1
        card = usage[0]
        # v2 fields are on the response dict
        d = card.model_dump()
        # The V2UsageResponse (returned by get_usage) has extra fields
        # but they are added as a wrapper, not on UsageResponse itself.
        # We verify via the get_usage_v2 method instead.


class TestGetUsageV2:
    def test_v2_response_fields(self, manager: V2RuntimeManager):
        loop = asyncio.get_event_loop()
        payload = ClaudePushRequest(
            seven_day={"utilization": 30.0, "resets_at": "2026-07-08T12:00:00Z"},
            five_hour={"utilization": 10.0, "resets_at": "2026-07-01T15:00:00Z"},
        )
        loop.run_until_complete(manager.handle_push("claude-work", payload))
        v2_cards = manager.get_usage_v2()
        assert len(v2_cards) == 1
        card = v2_cards[0]
        assert card["instance_id"] == "claude-work"
        assert card["type"] == "claude"
        assert card["instance_label"] == "工作号"
        assert card["provider"] == "Claude 订阅 · 工作号"
        assert len(card["limits"]) == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/flintylemming/Projects/ai-plan-insight && python -m pytest tests/test_provider_instances.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ai_plan_insight.provider_instances'`

- [ ] **Step 3: Implement `provider_instances.py`**

```python
# ai_plan_insight/provider_instances.py
"""v2 runtime manager: fetch scheduling, push dispatch, caching, and sorting.

Encapsulates all v2 runtime state so web.py only needs thin endpoint wrappers.
"""
from __future__ import annotations

import asyncio
import logging
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .instance_config import V2Config, V2InstanceConfig
from .api_schemas import UsageResponse
from .provider_registry import (
    build_fetch_provider,
    convert_push_payload,
    make_card_title,
    get_push_request_model,
    get_type_display_name,
)
from . import provider_v2_store

logger = logging.getLogger(__name__)

REFRESH_INTERVAL = 30
PUSH_TTL_SECONDS = 30 * 60


class V2RuntimeManager:
    """Manages v2 provider instances: fetch loop, push handling, caching, DB."""

    def __init__(self, config: V2Config, db_path: Path) -> None:
        self._config = config
        self._config_error: str | None = None
        self._enabled = True
        self._db_path = db_path

        # Runtime state keyed by instance_id
        self._fetch_results: dict[str, UsageResponse] = {}
        self._pushed_results: dict[str, UsageResponse] = {}
        self._pushed_at: dict[str, datetime] = {}
        self._consecutive_failures: dict[str, int] = {}
        self._prev_results: dict[str, UsageResponse] = {}
        self._last_updated: str | None = None
        self._refresh_task: asyncio.Task | None = None

    @property
    def config(self) -> V2Config:
        return self._config

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def has_fetch_instances(self) -> bool:
        return any(
            inst.mode == "fetch" for inst in self._config.providers.values()
        )

    def _conn(self) -> sqlite3.Connection:
        return sqlite3.connect(self._db_path)

    def _persist_snapshot(
        self,
        instance_id: str,
        type_name: str,
        mode: str,
        label: str,
        usage: UsageResponse,
    ) -> None:
        """Best-effort DB persist; never raises."""
        try:
            with closing(self._conn()) as conn:
                provider_v2_store.upsert_v2_snapshot(
                    conn, instance_id, type_name, mode, label, usage,
                )
                conn.commit()
        except Exception as e:
            logger.warning("v2 persist failed for %s: %s", instance_id, e)

    async def _fetch_one_instance(
        self, instance_id: str, cfg: V2InstanceConfig,
    ) -> UsageResponse:
        """Fetch usage for one fetch-mode instance."""
        title = make_card_title(cfg.type, cfg.label)
        provider = build_fetch_provider(instance_id, cfg)
        provider.authenticate()
        raw = await provider.fetch_usage()
        parsed = provider.parse_usage(raw)

        if hasattr(provider, "fetch_token_usage"):
            parsed.token_usage = await provider.fetch_token_usage()

        if hasattr(provider, "fetch_history_usage"):
            try:
                parsed.history_usage = await provider.fetch_history_usage()
            except Exception as e:
                logger.warning("v2 history fetch failed for %s: %s", instance_id, e)

        # Override the provider name with the v2 title
        parsed.provider = title

        # Convert to UsageResponse (mirrors web.py _fetch_one logic)
        from .api_schemas import (
            LimitResponse, UsageDetailResponse, TokenUsageResponse,
            HistoryUsagePeriodResponse, HistoryModelUsageResponse, ModelStatResponse,
        )

        limits = []
        for lim in parsed.limits:
            details = [
                UsageDetailResponse(model_code=d.model_code, usage=d.usage)
                for d in lim.usage_details
            ]
            limits.append(LimitResponse(
                duration=lim.duration, time_unit=lim.time_unit,
                limit=lim.limit, used=lim.used, remaining=lim.remaining,
                reset_time=lim.reset_time.isoformat() if lim.reset_time else None,
                usage_details=details, limit_type=lim.limit_type,
            ))

        token_usage = [
            TokenUsageResponse(
                period=t.period, total_tokens=t.total_tokens, total_calls=t.total_calls,
            )
            for t in parsed.token_usage
        ]

        history_usage = None
        if parsed.history_usage:
            hu = parsed.history_usage
            history_usage = HistoryUsagePeriodResponse(
                period=hu.period, granularity=hu.granularity,
                x_time=hu.x_time, tokens_usage=hu.tokens_usage,
                model_call_count=hu.model_call_count,
                total_tokens=hu.total_tokens, total_calls=hu.total_calls,
                models=[
                    HistoryModelUsageResponse(
                        model_name=m.model_name, total_tokens=m.total_tokens,
                        total_calls=m.total_calls, tokens_usage=m.tokens_usage,
                    )
                    for m in hu.models
                ],
            )

        model_stats = [
            ModelStatResponse(model=m.model, total_tokens=m.total_tokens, requests=m.requests)
            for m in parsed.model_stats
        ]

        return UsageResponse(
            provider=title, user_id=parsed.user_id,
            membership_level=parsed.membership_level,
            limits=limits, balances=parsed.balances,
            token_usage=token_usage, history_usage=history_usage,
            model_stats=model_stats,
        )

    async def _fetch_all(self) -> None:
        """Fetch all fetch-mode instances concurrently."""
        fetch_instances = {
            iid: cfg for iid, cfg in self._config.providers.items()
            if cfg.mode == "fetch"
        }
        if not fetch_instances:
            return

        tasks = {
            iid: self._fetch_one_instance(iid, cfg)
            for iid, cfg in fetch_instances.items()
        }
        results = await asyncio.gather(*tasks.values(), return_exceptions=True)

        for (iid, _), result in zip(tasks.items(), results):
            cfg = fetch_instances[iid]
            if isinstance(result, UsageResponse):
                self._consecutive_failures[iid] = 0
                self._prev_results[iid] = result
                self._fetch_results[iid] = result
                self._persist_snapshot(iid, cfg.type, cfg.mode, cfg.label, result)
            else:
                self._consecutive_failures[iid] = self._consecutive_failures.get(iid, 0) + 1
                title = make_card_title(cfg.type, cfg.label)
                if self._consecutive_failures[iid] < 3 and iid in self._prev_results:
                    self._fetch_results[iid] = self._prev_results[iid]
                else:
                    self._fetch_results[iid] = UsageResponse(provider=title, error=str(result))

    async def _background_refresh(self) -> None:
        """Background fetch loop for fetch-mode instances."""
        while True:
            try:
                await self._fetch_all()
                self._last_updated = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
            except Exception as e:
                logger.error("v2 background refresh failed: %s", e)
            await asyncio.sleep(REFRESH_INTERVAL)

    def restore_snapshots(self) -> None:
        """Restore v2 snapshots from DB for instances still in config."""
        active_ids = set(self._config.providers.keys())
        type_mode_map = {
            iid: (cfg.type, cfg.mode) for iid, cfg in self._config.providers.items()
        }
        label_map = {
            iid: cfg.label for iid, cfg in self._config.providers.items()
        }
        try:
            with closing(self._conn()) as conn:
                rows = provider_v2_store.load_v2_snapshots(
                    conn, active_ids, type_mode_map, label_map,
                )
        except Exception as e:
            logger.warning("v2 snapshot restore failed: %s", e)
            return

        for instance_id, type_name, mode, label, recorded_at, usage in rows:
            title = make_card_title(type_name, label)
            usage.provider = title  # Use current config title
            if mode == "push":
                try:
                    ts = datetime.fromisoformat(recorded_at)
                    if ts.tzinfo is None:
                        ts = ts.astimezone()
                    self._pushed_results[instance_id] = usage
                    self._pushed_at[instance_id] = ts
                except Exception:
                    continue
            elif mode == "fetch":
                self._fetch_results[instance_id] = usage
                self._prev_results[instance_id] = usage

        if rows:
            logger.info("v2: restored %d snapshot(s)", len(rows))

    def start(self) -> None:
        """Initialize DB schema, restore snapshots, start fetch loop if needed."""
        try:
            provider_v2_store.init_v2_db(self._db_path)
        except Exception as e:
            logger.error("v2 DB init failed: %s", e)

        self.restore_snapshots()

        if self.has_fetch_instances:
            self._refresh_task = asyncio.create_task(self._background_refresh())

    def stop(self) -> None:
        """Cancel the background fetch loop."""
        if self._refresh_task:
            self._refresh_task.cancel()
            self._refresh_task = None

    async def handle_push(self, instance_id: str, payload: Any) -> dict:
        """Process a push payload for one instance. Raises ValueError on error."""
        if instance_id not in self._config.providers:
            raise ValueError(f"instance {instance_id!r} not registered")

        cfg = self._config.providers[instance_id]
        if cfg.mode != "push":
            raise ValueError(f"instance {instance_id!r} is a fetch instance")

        title = make_card_title(cfg.type, cfg.label)
        usage = convert_push_payload(cfg.type, instance_id, title, payload)

        self._pushed_results[instance_id] = usage
        now = datetime.now().astimezone()
        self._pushed_at[instance_id] = now
        self._last_updated = now.strftime("%Y-%m-%d %H:%M:%S %Z")

        self._persist_snapshot(instance_id, cfg.type, cfg.mode, cfg.label, usage)

        return {"status": "ok", "instance_id": instance_id}

    def _sort_key(self, instance_id: str, provider_title: str) -> tuple[int, str, str]:
        cfg = self._config.providers.get(instance_id)
        order = cfg.order if cfg else 999
        return (order, provider_title, instance_id)

    def get_usage(self) -> list[UsageResponse]:
        """Return combined fetch + valid push results, sorted by (order, title, id)."""
        now = datetime.now().astimezone()
        cutoff = now - timedelta(seconds=PUSH_TTL_SECONDS)

        valid_pushed = [
            r for iid, r in self._pushed_results.items()
            if self._pushed_at.get(iid) and self._pushed_at[iid] >= cutoff
        ]

        combined = list(self._fetch_results.values()) + valid_pushed

        # Build a map from provider title back to instance_id for sorting
        title_to_iid: dict[str, str] = {}
        for iid, cfg in self._config.providers.items():
            title = make_card_title(cfg.type, cfg.label)
            title_to_iid[title] = iid

        combined.sort(
            key=lambda r: self._sort_key(
                title_to_iid.get(r.provider, ""), r.provider,
            )
        )
        return combined

    def get_usage_v2(self) -> list[dict]:
        """Return v2 response dicts with instance_id, type, instance_label."""
        cards = self.get_usage()
        result = []
        for card in cards:
            d = card.model_dump()
            # Find the matching instance
            for iid, cfg in self._config.providers.items():
                title = make_card_title(cfg.type, cfg.label)
                if card.provider == title:
                    d["instance_id"] = iid
                    d["type"] = cfg.type
                    d["instance_label"] = cfg.label
                    break
            result.append(d)
        return result

    def get_status(self) -> dict:
        return {
            "enabled": self._enabled,
            "last_updated": self._last_updated,
            "config_error": self._config_error,
        }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/flintylemming/Projects/ai-plan-insight && python -m pytest tests/test_provider_instances.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add ai_plan_insight/provider_instances.py tests/test_provider_instances.py
git commit -m "feat(v2): add v2 runtime manager with fetch scheduling and push dispatch"
```

---

### Task 5: v2 API Endpoints and Lifespan Wiring

**Files:**
- Modify: `ai_plan_insight/web.py`
- Modify: `ai_plan_insight/__main__.py`
- Create: `tests/test_v2_api.py`

**Interfaces:**
- Consumes: `V2RuntimeManager` from Task 4; `instance_config`, `provider_registry`
- Produces: 3 new endpoints: `GET /api/usage/v2`, `GET /api/status/v2`, `POST /api/push/v2/{instance_id}`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_v2_api.py
import json
import pytest
from pathlib import Path
from fastapi.testclient import TestClient


@pytest.fixture
def v2_config_path(tmp_path: Path) -> Path:
    p = tmp_path / "config.v2.json"
    p.write_text(json.dumps({
        "providers": {
            "claude-personal": {
                "type": "claude", "mode": "push", "label": "个人号", "order": 12,
            },
            "claude-work": {
                "type": "claude", "mode": "push", "label": "工作号", "order": 13,
            },
        },
        "push_auth_secret": "test-secret",
        "enforce_push_auth": True,
    }))
    return p


@pytest.fixture
def client(v2_config_path: Path, tmp_path: Path) -> TestClient:
    """Create a test client with v2 enabled."""
    # Write a minimal old config so the app starts
    old_config = tmp_path / "config.json"
    old_config.write_text(json.dumps({"providers": {}}))

    import ai_plan_insight.web as web_mod
    web_mod._config_path = str(old_config)
    web_mod._usage_db_path = tmp_path / "usage.db"
    web_mod._v2_config_path = str(v2_config_path)

    # Reset v2 manager state
    web_mod._v2_manager = None

    from ai_plan_insight.web import app
    with TestClient(app) as c:
        yield c

    # Cleanup
    web_mod._v2_manager = None
    web_mod._v2_config_path = None


class TestV2Status:
    def test_status_enabled(self, client: TestClient):
        resp = client.get("/api/status/v2")
        assert resp.status_code == 200
        data = resp.json()
        assert data["enabled"] is True
        assert data["config_error"] is None


class TestV2Usage:
    def test_empty_initially(self, client: TestClient):
        resp = client.get("/api/usage/v2")
        assert resp.status_code == 200
        assert resp.json() == []


class TestV2Push:
    def test_push_success(self, client: TestClient):
        resp = client.post(
            "/api/push/v2/claude-work",
            json={
                "seven_day": {"utilization": 45.2, "resets_at": "2026-07-08T12:00:00Z"},
                "five_hour": {"utilization": 12.8, "resets_at": "2026-07-01T15:00:00Z"},
            },
            headers={"Authorization": "Bearer test-secret"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["instance_id"] == "claude-work"

    def test_push_unknown_instance(self, client: TestClient):
        resp = client.post(
            "/api/push/v2/nonexistent",
            json={
                "seven_day": {"utilization": 10.0, "resets_at": "2026-07-08T12:00:00Z"},
                "five_hour": {"utilization": 5.0, "resets_at": "2026-07-01T15:00:00Z"},
            },
            headers={"Authorization": "Bearer test-secret"},
        )
        assert resp.status_code == 404

    def test_push_auth_required(self, client: TestClient):
        resp = client.post(
            "/api/push/v2/claude-work",
            json={
                "seven_day": {"utilization": 10.0, "resets_at": "2026-07-08T12:00:00Z"},
                "five_hour": {"utilization": 5.0, "resets_at": "2026-07-01T15:00:00Z"},
            },
            # No auth header
        )
        assert resp.status_code == 401

    def test_push_bad_schema(self, client: TestClient):
        resp = client.post(
            "/api/push/v2/claude-work",
            json={"bad": "data"},
            headers={"Authorization": "Bearer test-secret"},
        )
        assert resp.status_code == 422

    def test_push_appears_in_usage(self, client: TestClient):
        # Push to both instances
        for inst_id in ["claude-personal", "claude-work"]:
            client.post(
                f"/api/push/v2/{inst_id}",
                json={
                    "seven_day": {"utilization": 30.0, "resets_at": "2026-07-08T12:00:00Z"},
                    "five_hour": {"utilization": 10.0, "resets_at": "2026-07-01T15:00:00Z"},
                },
                headers={"Authorization": "Bearer test-secret"},
            )

        resp = client.get("/api/usage/v2")
        data = resp.json()
        assert len(data) == 2
        providers = {d["provider"] for d in data}
        assert "Claude 订阅 · 个人号" in providers
        assert "Claude 订阅 · 工作号" in providers
        # Check v2 fields
        for d in data:
            assert "instance_id" in d
            assert "type" in d
            assert "instance_label" in d

    def test_push_ordering(self, client: TestClient):
        for inst_id in ["claude-work", "claude-personal"]:
            client.post(
                f"/api/push/v2/{inst_id}",
                json={
                    "seven_day": {"utilization": 30.0, "resets_at": "2026-07-08T12:00:00Z"},
                    "five_hour": {"utilization": 10.0, "resets_at": "2026-07-01T15:00:00Z"},
                },
                headers={"Authorization": "Bearer test-secret"},
            )
        resp = client.get("/api/usage/v2")
        data = resp.json()
        # order 12 (personal) before order 13 (work)
        assert data[0]["instance_id"] == "claude-personal"
        assert data[1]["instance_id"] == "claude-work"


class TestV2Disabled:
    def test_disabled_when_no_config(self, tmp_path: Path):
        """When v2 config doesn't exist, v2 endpoints return disabled/empty."""
        old_config = tmp_path / "config.json"
        old_config.write_text(json.dumps({"providers": {}}))

        import ai_plan_insight.web as web_mod
        web_mod._config_path = str(old_config)
        web_mod._usage_db_path = tmp_path / "usage.db"
        web_mod._v2_config_path = str(tmp_path / "nonexistent_v2.json")
        web_mod._v2_manager = None

        from ai_plan_insight.web import app
        with TestClient(app) as c:
            resp = c.get("/api/status/v2")
            assert resp.status_code == 200
            assert resp.json()["enabled"] is False

            resp = c.get("/api/usage/v2")
            assert resp.status_code == 200
            assert resp.json() == []

            resp = c.post(
                "/api/push/v2/any-instance",
                json={"test": True},
            )
            assert resp.status_code == 503

        web_mod._v2_manager = None
        web_mod._v2_config_path = None


class TestOldSystemUntouched:
    def test_old_usage_still_works(self, client: TestClient):
        resp = client.get("/api/usage")
        assert resp.status_code == 200

    def test_old_status_still_works(self, client: TestClient):
        resp = client.get("/api/status")
        assert resp.status_code == 200
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/flintylemming/Projects/ai-plan-insight && python -m pytest tests/test_v2_api.py -v`
Expected: FAIL — endpoints don't exist yet

- [ ] **Step 3: Add v2 state variables and endpoints to `web.py`**

Add the following near the top of `web.py` (after existing globals around line 64):

```python
# v2 state
_v2_config_path: str | None = None  # set by main() via --v2-config
_v2_manager = None  # type: V2RuntimeManager | None
```

Add the v2 manager initialization function and modify lifespan (around line 360):

```python
def _init_v2_manager() -> None:
    """Try to load v2 config and create the runtime manager. Sets _v2_manager or leaves it None."""
    global _v2_manager
    from .instance_config import load_v2_config, resolve_v2_config_path
    from .provider_instances import V2RuntimeManager

    v2_path = resolve_v2_config_path(_v2_config_path, config_path=_config_path)
    if not v2_path.exists():
        logger.info("v2 config not found at %s, v2 disabled", v2_path)
        return

    try:
        v2_config = load_v2_config(str(v2_path))
    except Exception as e:
        logger.error("v2 config invalid: %s", e)
        # Create a disabled manager to surface the error via /api/status/v2
        mgr = V2RuntimeManager.__new__(V2RuntimeManager)
        mgr._config = None
        mgr._config_error = str(e)
        mgr._enabled = False
        mgr._last_updated = None
        mgr._pushed_results = {}
        mgr._pushed_at = {}
        mgr._fetch_results = {}
        mgr._consecutive_failures = {}
        mgr._prev_results = {}
        mgr._refresh_task = None
        mgr._db_path = resolve_usage_db_path()
        _v2_manager = mgr
        return

    _v2_manager = V2RuntimeManager(v2_config, resolve_usage_db_path())
```

Update the lifespan function:

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        usage_store.init_db(resolve_usage_db_path())
        _restore_push_card_snapshots()
    except Exception as e:
        logger.error("usage DB init failed: %s", e)

    # Initialize v2
    _init_v2_manager()
    if _v2_manager and _v2_manager.enabled:
        _v2_manager.start()

    task_refresh = asyncio.create_task(_background_refresh())
    yield
    task_refresh.cancel()
    if _v2_manager:
        _v2_manager.stop()
```

Add the three v2 endpoints at the end of `web.py`:

```python
# ── v2 endpoints ──────────────────────────────────────────────

@app.get("/api/usage/v2")
async def get_usage_v2():
    if _v2_manager is None or not _v2_manager.enabled:
        return []
    return _v2_manager.get_usage_v2()


@app.get("/api/status/v2")
async def get_status_v2():
    if _v2_manager is None:
        return {"enabled": False, "last_updated": None, "config_error": None}
    return _v2_manager.get_status()


@app.post("/api/push/v2/{instance_id}")
async def push_v2(instance_id: str, request: Request):
    if _v2_manager is None or not _v2_manager.enabled:
        raise HTTPException(status_code=503, detail="v2 not available")

    cfg = _v2_manager.config
    if instance_id not in cfg.providers:
        raise HTTPException(status_code=404, detail=f"instance {instance_id!r} not registered")

    inst = cfg.providers[instance_id]
    if inst.mode != "push":
        raise HTTPException(
            status_code=422,
            detail=f"instance {instance_id!r} is a fetch instance",
        )

    # Auth check
    is_valid, reason = _verify_push_auth_v2(request, cfg)
    if not is_valid:
        logger.warning("v2 push auth failed for %s: %s", instance_id, reason)
    if cfg.enforce_push_auth and not is_valid:
        raise HTTPException(status_code=401, detail="Unauthorized")

    # Get the request model for this type and parse the body
    from .provider_registry import get_push_request_model
    req_model = get_push_request_model(inst.type)
    if req_model is None:
        raise HTTPException(status_code=422, detail=f"no push schema for type {inst.type!r}")

    try:
        body = await request.json()
        payload = req_model.model_validate(body)
    except Exception as e:
        raise HTTPException(status_code=422, detail=str(e))

    try:
        result = await _v2_manager.handle_push(instance_id, payload)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    return result


def _verify_push_auth_v2(request: Request, v2_config) -> tuple[bool, str | None]:
    """Check Bearer token against v2 config's push_auth_secret."""
    secret = v2_config.push_auth_secret
    auth_header = request.headers.get("Authorization")
    if not auth_header:
        return False, "missing"
    parts = auth_header.strip().split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return False, "malformed"
    token = parts[1].strip()
    if not secret:
        return False, "invalid"
    if secrets.compare_digest(token, secret):
        return True, None
    return False, "invalid"
```

- [ ] **Step 4: Add `--v2-config` to `__main__.py`**

Add to the argument parser (after `--usage-db`):

```python
parser.add_argument(
    "--v2-config",
    default=None,
    help="Path to v2 configuration file (default: <config dir>/config.v2.json)",
)
```

Add to the `main()` function's web branch (after `web_mod._usage_db_path`):

```python
if args.v2_config:
    web_mod._v2_config_path = args.v2_config
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd /Users/flintylemming/Projects/ai-plan-insight && python -m pytest tests/test_v2_api.py -v`
Expected: all PASS

- [ ] **Step 6: Run all existing tests to verify nothing is broken**

Run: `cd /Users/flintylemming/Projects/ai-plan-insight && python -m pytest tests/ -v --ignore=tests/test_mobile_ui.py --ignore=tests/test_index_history_view.py --ignore=tests/test_index_usage_view.py`
Expected: all existing tests still PASS

- [ ] **Step 7: Commit**

```bash
git add ai_plan_insight/web.py ai_plan_insight/__main__.py tests/test_v2_api.py
git commit -m "feat(v2): add v2 API endpoints and lifespan wiring"
```

---

### Task 6: Frontend — New Tab and v2 Data Source

**Files:**
- Modify: `ai_plan_insight/index.html`

**Interfaces:**
- Consumes: `GET /api/usage/v2`, `GET /api/status/v2`
- Produces: New "订阅余额（新）" tab button, `balance-v2` tab state, v2 data fetching and rendering

- [ ] **Step 1: Add the new tab button**

Find the tab bar HTML (around line 558):

```html
<div class="tab-bar">
  <button class="tab active" data-tab="balance">订阅余额</button>
  <button class="tab" data-tab="usage">模型用量</button>
</div>
```

Replace with:

```html
<div class="tab-bar">
  <button class="tab active" data-tab="balance">订阅余额</button>
  <button class="tab" data-tab="balance-v2">订阅余额（新）</button>
  <button class="tab" data-tab="usage">模型用量</button>
</div>
```

- [ ] **Step 2: Update the `applyTab` function**

Find the `applyTab` function (around line 589). Replace the show/hide logic to handle three views:

```js
function applyTab(tab) {
  // Validate tab value; fall back to 'balance' for unknown values
  const validTabs = ['balance', 'balance-v2', 'usage'];
  if (!validTabs.includes(tab)) tab = 'balance';
  activeTab = tab;
  localStorage.setItem(TAB_KEY, tab);
  document.querySelectorAll('.tab').forEach(b => {
    b.classList.toggle('active', b.getAttribute('data-tab') === tab);
  });
  const grid = document.getElementById('grid');
  const usageView = document.getElementById('usage-chart-view');
  grid.style.display = (tab === 'balance' || tab === 'balance-v2') ? 'grid' : 'none';
  usageView.style.display = (tab === 'usage') ? 'block' : 'none';
  if (tab === 'balance') refresh();
  else if (tab === 'balance-v2') refreshV2();
  else if (tab === 'usage') refreshUsageChart();
}
```

- [ ] **Step 3: Add the v2 refresh function and data source**

Add after the existing `refresh()` function (around line 1505):

```js
async function refreshV2() {
  const [data, status] = await Promise.all([
    fetch('/api/usage/v2').then(r => r.json()),
    fetch('/api/status/v2').then(r => r.json()),
  ]);
  if (!status.enabled) {
    const grid = document.getElementById('grid');
    grid.innerHTML = '<div class="empty-usage">尚未配置订阅余额（新）</div>';
    return;
  }
  if (status.config_error) {
    const grid = document.getElementById('grid');
    grid.innerHTML = `<div class="error-card"><div class="card-title">配置错误</div><div style="margin-top:0.5rem;color:#fca5a5;">${status.config_error}</div></div>`;
    return;
  }
  latestV2Data = data;
  renderV2Grid(data);
  const updatedEl = document.querySelector('.updated');
  if (updatedEl && status.last_updated) {
    updatedEl.textContent = '最后更新：' + status.last_updated;
  }
}

let latestV2Data = [];

function renderV2Grid(data) {
  const grid = document.getElementById('grid');
  if (!data || data.length === 0) {
    grid.innerHTML = '<div class="empty-usage">暂无数据</div>';
    return;
  }
  grid.innerHTML = data.map(renderCard).join('');
  if (activeTab === 'balance-v2') grid.style.display = 'grid';
  bindHistoryCharts();
}
```

- [ ] **Step 4: Update the auto-refresh interval**

Find the auto-refresh `setInterval` calls (around lines 1507-1509). Replace the balance refresh interval with logic that refreshes the currently active balance source:

```js
setInterval(() => {
  if (activeTab === 'balance') refresh();
  else if (activeTab === 'balance-v2') refreshV2();
}, 60000);
```

Keep the existing usage chart interval unchanged.

- [ ] **Step 5: Update the history chart card view toggle**

The existing `renderHeader` and `cardViewModes` use `data.provider` (the display title) as the key. For v2 cards, we should use `data.instance_id` when available so two GLM instances don't share state.

Find the `renderHeader` function and the toggle handler. Update the key logic:

```js
// In renderHeader or wherever cardViewModes is indexed:
const cardKey = data.instance_id || data.provider;
```

Apply this to all places that use `data.provider` as a card-state key (view toggle, history chart DOM IDs, etc.).

- [ ] **Step 6: Verify the frontend manually**

Start the server with a v2 config and verify:

1. Three tabs appear in the tab bar
2. "订阅余额" shows old data (or empty)
3. "订阅余额（新）" shows v2 data (or "尚未配置" / "暂无数据")
4. "模型用量" works as before
5. Tab state persists in localStorage
6. Unknown tab values in localStorage fall back to "balance"
7. Pushing to two Claude instances shows two cards

- [ ] **Step 7: Commit**

```bash
git add ai_plan_insight/index.html
git commit -m "feat(v2): add 订阅余额（新）tab with v2 data source"
```

---

### Task 7: Full Regression and Integration Test

- [ ] **Step 1: Run all Python tests**

Run: `cd /Users/flintylemming/Projects/ai-plan-insight && python -m pytest tests/ -v`
Expected: all PASS (new and existing)

- [ ] **Step 2: Create a test `config.v2.json` and start the server**

```json
{
  "providers": {
    "claude-personal": {
      "type": "claude", "mode": "push", "label": "个人号", "order": 12
    },
    "claude-work": {
      "type": "claude", "mode": "push", "label": "工作号", "order": 13
    }
  },
  "push_auth_secret": "test-token",
  "enforce_push_auth": false
}
```

Start the server: `python -m ai_plan_insight --web --v2-config config.v2.json`

- [ ] **Step 3: Push to both Claude instances and verify**

```bash
curl -X POST http://localhost:8765/api/push/v2/claude-personal \
  -H 'Content-Type: application/json' \
  -d '{"seven_day":{"utilization":35.0,"resets_at":"2026-07-08T12:00:00Z"},"five_hour":{"utilization":15.0,"resets_at":"2026-07-01T15:00:00Z"}}'

curl -X POST http://localhost:8765/api/push/v2/claude-work \
  -H 'Content-Type: application/json' \
  -d '{"seven_day":{"utilization":55.0,"resets_at":"2026-07-08T12:00:00Z"},"five_hour":{"utilization":25.0,"resets_at":"2026-07-01T15:00:00Z"}}'
```

- [ ] **Step 4: Verify API responses**

```bash
curl http://localhost:8765/api/usage/v2 | python -m json.tool
curl http://localhost:8765/api/status/v2 | python -m json.tool
```

Verify:
- Two cards with different `instance_id` values
- Both have `type: "claude"` and their respective `instance_label`
- Sorted by order (个人号=12 before 工作号=13)

- [ ] **Step 5: Verify old system is untouched**

```bash
curl http://localhost:8765/api/usage | python -m json.tool
curl http://localhost:8765/api/status | python -m json.tool
```

Old endpoints should still work and return their own data.

- [ ] **Step 6: Verify frontend in browser**

Open `http://localhost:8765` and verify:
1. Three tabs appear
2. Click "订阅余额（新）" → two Claude cards appear
3. Click "订阅余额" → old system data (or empty)
4. Click "模型用量" → usage chart still works
5. Refresh page → tab state persists

- [ ] **Step 7: Test restart recovery**

Kill the server, restart it, and verify the two Claude cards reappear immediately from DB snapshots (before any new push).

- [ ] **Step 8: Final commit**

```bash
git add -A
git commit -m "feat(v2): provider multi-instance v2 complete"
```
