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
    "bigmodel_international": "GLM Coding Plan 国际版",
    "aiping": "AIPing",
    "huawei_cloud": "华为云余额",
    "zenmux": "ZenMux",
    "codex": "自购 Codex 中转站",
    "codex_sub2api": "Codex Sub2API 中转",
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
    "codex_sub2api": CodexSecurityProvider,
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
