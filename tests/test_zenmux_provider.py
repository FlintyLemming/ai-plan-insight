from datetime import timezone

from ai_plan_insight.config import ProviderConfig
from ai_plan_insight.providers.zenmux import ZenMuxProvider


def _sample_raw() -> dict:
    return {
        "subscription": {
            "success": True,
            "data": {
                "plan": {
                    "tier": "ultra",
                    "amount_usd": 200,
                    "interval": "month",
                    "expires_at": "2026-04-12T08:26:56.000Z",
                },
                "currency": "usd",
                "base_usd_per_flow": 0.03283,
                "effective_usd_per_flow": 0.03283,
                "account_status": "healthy",
                "quota_5_hour": {
                    "usage_percentage": 0.0715,
                    "resets_at": "2026-03-24T08:35:09.000Z",
                    "max_flows": 800,
                    "used_flows": 57.2,
                    "remaining_flows": 742.8,
                    "used_value_usd": 1.88,
                    "max_value_usd": 26.27,
                },
                "quota_7_day": {
                    "usage_percentage": 0.0673,
                    "resets_at": "2026-03-26T02:15:05.000Z",
                    "max_flows": 6182,
                    "used_flows": 416.11,
                    "remaining_flows": 5765.89,
                    "used_value_usd": 13.66,
                    "max_value_usd": 202.99,
                },
                "quota_monthly": {
                    "max_flows": 34560,
                    "max_value_usd": 1134.33,
                },
            },
        },
        "balance": {
            "success": True,
            "data": {
                "currency": "usd",
                "total_credits": 482.74,
                "top_up_credits": 35.0,
                "bonus_credits": 447.74,
            },
        },
    }


def _provider() -> ZenMuxProvider:
    return ZenMuxProvider(ProviderConfig(api_key="sk-mg-v1-test"))


def test_authenticate_rejects_placeholder_keys():
    for bad in ("", "  ", "YOUR_ZENMUX_KEY", "xxxx", "put_zz"):
        provider = ZenMuxProvider(ProviderConfig(api_key=bad))
        raised = False
        try:
            provider.authenticate()
        except ValueError:
            raised = True
        assert raised, f"expected ValueError for {bad!r}"


def test_authenticate_accepts_real_key():
    provider = _provider()
    provider.authenticate()
    assert provider._headers["Authorization"] == "Bearer sk-mg-v1-test"


def test_parse_usage_builds_percentage_limits_and_balances():
    provider = _provider()
    info = provider.parse_usage(_sample_raw())

    assert info.provider == "ZenMux"
    assert info.membership_level == "Ultra"

    assert [l.limit_type for l in info.limits] == ["PERCENT", "PERCENT"]
    labels = [l.time_unit for l in info.limits]
    assert labels == ["5 小时滚动窗口", "7 天滚动窗口"]
    # usage_percentage 0.0715 -> 7.15%
    assert info.limits[0].used == "7.15"
    assert info.limits[0].remaining == "92.85"
    assert info.limits[0].limit == "100"
    assert info.limits[0].reset_time is not None
    assert info.limits[0].reset_time.tzinfo is not None
    assert info.limits[1].used == "6.73"

    # Monthly quota is no longer surfaced in balances
    assert "月配额" not in info.balances

    # PAYG balance (total only)
    assert info.balances["PAYG 余额"] == "$482.74"
    assert "PAYG 总余额" not in info.balances
    assert "PAYG 充值余额" not in info.balances
    assert "PAYG 赠送余额" not in info.balances

    # Plan info
    assert info.balances["套餐"] == "Ultra"
    assert info.balances["套餐到期"] == "2026-04-12"
    assert "Flow 汇率" not in info.balances

    # Healthy status is not surfaced as a balance row
    assert "账号状态" not in info.balances


def test_parse_usage_surfaces_non_healthy_status():
    raw = _sample_raw()
    raw["subscription"]["data"]["account_status"] = "monitored"
    info = _provider().parse_usage(raw)
    assert info.balances["账号状态"] == "monitored"


def test_parse_usage_tolerates_missing_balance_block():
    raw = _sample_raw()
    raw["balance"] = {}
    info = _provider().parse_usage(raw)
    # Subscription-derived fields still present
    assert info.membership_level == "Ultra"
    assert info.balances["套餐"] == "Ultra"
    assert "PAYG 余额" not in info.balances


def test_reset_time_parsed_as_utc():
    provider = _provider()
    dt = provider._parse_reset_time("2026-03-24T08:35:09.000Z")
    assert dt is not None
    assert dt.tzinfo is not None
    # +00:00 normalized; offset equivalent to UTC
    assert dt.utcoffset().total_seconds() == 0
    assert dt.year == 2026 and dt.month == 3 and dt.day == 24
