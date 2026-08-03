from ai_plan_insight.config import ProviderConfig
from ai_plan_insight.providers.codex import CodexProvider, Sub2ApiProvider


def _sub2() -> Sub2ApiProvider:
    return Sub2ApiProvider(ProviderConfig(api_key="sk-test", base_url="https://example.test"))


def _codex() -> CodexProvider:
    return CodexProvider(ProviderConfig(api_key="sk-test", base_url="https://example.test"))


def _usage_today(*, actual_cost=None, cost=None) -> dict:
    today: dict = {}
    if actual_cost is not None:
        today["actual_cost"] = actual_cost
    if cost is not None:
        today["cost"] = cost
    return {"today": today, "total": {"actual_cost": 9.99, "cost": 10.0}}


def test_plan_includes_today_spend_from_actual_cost():
    raw = {
        "mode": "unrestricted",
        "planName": "Pro",
        "subscription": {
            "daily_limit_usd": 10,
            "daily_usage_usd": 1.5,
            "monthly_limit_usd": 100,
            "monthly_usage_usd": 20,
        },
        "usage": _usage_today(actual_cost=0.12, cost=0.15),
    }
    info = _sub2().parse_usage(raw)
    assert info.balances["今日花费"] == "$0.12"
    assert info.balances["套餐"] == "Pro"


def test_wallet_falls_back_to_standard_cost():
    raw = {
        "mode": "unrestricted",
        "planName": "钱包余额",
        "balance": 42.5,
        "remaining": 42.5,
        "usage": _usage_today(cost=0.15),
    }
    info = _sub2().parse_usage(raw)
    assert info.balances["今日花费"] == "$0.15"
    assert info.balances["余额"] == "$42.50"


def test_quota_limited_omits_today_spend_when_missing():
    raw = {
        "mode": "quota_limited",
        "quota": {"limit": 100, "used": 10, "remaining": 90},
        "expires_at": "2026-12-01T00:00:00Z",
        "days_until_expiry": 120,
    }
    info = _sub2().parse_usage(raw)
    assert "今日花费" not in info.balances
    assert info.balances["套餐到期"] == "2026-12-01"


def test_quota_limited_includes_today_spend():
    raw = {
        "mode": "quota_limited",
        "quota": {"limit": 100, "used": 10, "remaining": 90},
        "usage": _usage_today(actual_cost=1.234),
    }
    info = _codex().parse_usage(raw)
    assert info.balances["今日花费"] == "$1.23"


def test_codex_unrestricted_does_not_duplicate_today_in_balances():
    """Today cost already drives the progress-bar used field."""
    raw = {
        "mode": "unrestricted",
        "planName": "钱包余额",
        "balance": 10,
        "usage": _usage_today(actual_cost=0.5),
    }
    info = _codex().parse_usage(raw)
    assert "今日花费" not in info.balances
    assert info.limits[0].used == "0.50"
