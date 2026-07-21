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
    QianwenPushRequest,
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

    def test_claude_with_fable(self):
        payload = ClaudePushRequest(
            seven_day={"utilization": 25.0, "resets_at": "2026-07-26T07:00:00Z"},
            five_hour={"utilization": 15.0, "resets_at": "2026-07-20T04:40:00Z"},
            fable={"utilization": 44.0, "resets_at": "2026-07-26T07:00:00Z"},
        )
        resp = convert_push_payload("claude", "claude-work", "Claude 订阅 · 工作号", payload)
        assert len(resp.limits) == 3
        fable = resp.limits[2]
        assert fable.time_unit == "天（Fable）"
        assert fable.used == "44"
        assert fable.remaining == "56"
        assert fable.reset_time == "2026-07-26T07:00:00Z"

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

    def test_qianwen_percentage_and_reset(self):
        """千问推送：0-100 百分比 + resets_at，走 PERCENT 条，不再依赖绝对 Credits。"""
        payload = QianwenPushRequest(
            plan="lite",
            status="VALID",
            remaining_days=29,
            auto_renew=False,
            expires_at="2026-08-20 00:00:00+0800",
            five_hour={
                "used_percentage": 70.0,
                "resets_at": "2026-07-21T15:24:00+08:00",
            },
            seven_day={
                "used_percentage": 19.6,
                "resets_at": "2026-07-28T10:24:00+08:00",
            },
        )
        resp = convert_push_payload(
            "qianwen", "qianwen-personal", "千问 Token Plan", payload,
        )
        assert resp.provider == "千问 Token Plan"
        assert resp.membership_level == "lite"
        assert len(resp.limits) == 2
        w5, w7 = resp.limits
        assert w5.limit_type == "PERCENT"
        assert w5.time_unit == "5 小时"
        assert w5.used == "70.00"
        assert w5.remaining == "30.00"
        assert w5.reset_time == "2026-07-21T15:24:00+08:00"
        assert w7.time_unit == "周"
        assert w7.used == "19.60"
        assert w7.remaining == "80.40"
        assert w7.reset_time == "2026-07-28T10:24:00+08:00"
        assert resp.balances.get("剩余天数") == "29 天"
        assert resp.balances.get("到期时间") == "2026-08-20"
