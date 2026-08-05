import pytest

from ai_plan_insight.config import ProviderConfig
from ai_plan_insight.providers.new_api import NewApiProvider


def _provider() -> NewApiProvider:
    return NewApiProvider(
        ProviderConfig(api_key="sk-test", base_url="https://example.test")
    )


def _payload(**overrides) -> dict:
    """Real shape of GET /api/usage/token/ on a new-api deployment."""
    data = {
        "object": "token_usage",
        "name": "free",
        "total_granted": 49968003,
        "total_used": 9624219,
        "total_available": 40343784,
        "unlimited_quota": False,
        "model_limits": {},
        "model_limits_enabled": False,
        "expires_at": 0,
    }
    data.update(overrides)
    return {"code": True, "message": "ok", "data": data}


def test_quota_is_converted_to_usd():
    """new-api quota unit: 500,000 quota == $1 (QuotaPerUnit)."""
    info = _provider().parse_usage(_payload())
    limit = info.limits[0]
    assert limit.time_unit == "USD"
    assert limit.limit == "99.94"
    assert limit.used == "19.25"
    assert limit.remaining == "80.69"
    assert limit.reset_time is None
    assert info.balances["令牌"] == "free"


def test_expiry_is_reported():
    info = _provider().parse_usage(_payload(expires_at=1767225600))  # 2026-01-01 UTC
    assert info.limits[0].reset_time is not None
    assert info.balances["到期"] == "2026-01-01"


def test_never_expiring_token_has_no_expiry_row():
    info = _provider().parse_usage(_payload(expires_at=-1))
    assert "到期" not in info.balances
    assert info.limits[0].reset_time is None


def test_unlimited_quota_shows_balance_instead_of_limit_bar():
    info = _provider().parse_usage(
        _payload(
            unlimited_quota=True,
            total_granted=123,
            total_used=123,
            total_available=0,
        )
    )
    assert info.limits == []
    assert info.balances["额度"] == "不限"
    assert info.balances["已用"] == "$0.00"


def test_blank_token_name_is_omitted():
    info = _provider().parse_usage(_payload(name=""))
    assert "令牌" not in info.balances


def test_error_payload_without_data_raises():
    with pytest.raises(ValueError):
        _provider().parse_usage({"success": False, "message": "令牌无效"})
