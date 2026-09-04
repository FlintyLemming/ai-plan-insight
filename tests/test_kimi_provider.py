import pytest
import httpx
from datetime import datetime
from ai_plan_insight.config import ProviderConfig
from ai_plan_insight.providers.kimi import KimiProvider


def _sample_usages_raw() -> dict:
    return {
        "user": {
            "userId": "dabd2o3kvk6r4bcj85vg",
            "region": "REGION_CN",
            "membership": {
                "level": "LEVEL_INTERMEDIATE"
            },
            "businessId": ""
        },
        "usage": {
            "limit": "100",
            "used": "55",
            "remaining": "45",
            "resetTime": "2026-09-08T13:23:49.332693Z"
        },
        "limits": [
            {
                "window": {
                    "duration": 300,
                    "timeUnit": "TIME_UNIT_MINUTE"
                },
                "detail": {
                    "limit": "100",
                    "used": "8",
                    "remaining": "92",
                    "resetTime": "2026-09-04T11:23:49.332693Z"
                }
            }
        ]
    }


def _sample_user_info_raw() -> dict:
    return {
        "user_id": "dabd2o3kvk6r4bcj85vg",
        "nickname": "登月者3392",
        "user_level": 25,
        "user_level_name": "Allegretto"
    }


def _provider() -> KimiProvider:
    return KimiProvider(ProviderConfig(api_key="sk-kimi-test123456"))


def test_parse_usage_with_user_info():
    provider = _provider()
    raw = {
        "usages": _sample_usages_raw(),
        "user_info": _sample_user_info_raw()
    }
    info = provider.parse_usage(raw)

    assert info.provider == "Kimi Coding Plan"
    assert info.user_id == "dabd2o3kvk6r4bcj85vg"
    assert info.membership_level == "Allegretto"
    assert len(info.limits) == 2
    assert info.limits[0].time_unit == "week"
    assert info.limits[0].limit == "100"
    assert info.limits[0].used == "55"
    assert info.limits[1].duration == 5
    assert info.limits[1].time_unit == "hour"


def test_parse_usage_fallback_to_level_mapping_without_user_info():
    provider = _provider()
    # Direct legacy format (only /usages payload)
    raw = _sample_usages_raw()
    info = provider.parse_usage(raw)

    assert info.user_id == "dabd2o3kvk6r4bcj85vg"
    assert info.membership_level == "Allegretto"


def test_parse_usage_level_mappings():
    provider = _provider()
    for enum_level, expected_name in [
        ("LEVEL_BASIC", "Andante"),
        ("LEVEL_INTERMEDIATE", "Allegretto"),
        ("LEVEL_ADVANCED", "Allegro"),
    ]:
        raw = _sample_usages_raw()
        raw["user"]["membership"]["level"] = enum_level
        info = provider.parse_usage(raw)
        assert info.membership_level == expected_name


@pytest.mark.anyio
async def test_fetch_usage_concurrent_success(monkeypatch):
    provider = _provider()
    provider.authenticate()

    async def mock_get(self, url, headers=None):
        if "usages" in str(url):
            return httpx.Response(200, json=_sample_usages_raw(), request=httpx.Request("GET", str(url)))
        elif "me" in str(url):
            return httpx.Response(200, json=_sample_user_info_raw(), request=httpx.Request("GET", str(url)))
        return httpx.Response(404, request=httpx.Request("GET", str(url)))

    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)

    data = await provider.fetch_usage()
    assert "usages" in data
    assert "user_info" in data
    assert data["user_info"]["user_level_name"] == "Allegretto"

    parsed = provider.parse_usage(data)
    assert parsed.membership_level == "Allegretto"


@pytest.mark.anyio
async def test_fetch_usage_me_failure_tolerated(monkeypatch):
    provider = _provider()
    provider.authenticate()

    async def mock_get(self, url, headers=None):
        if "usages" in str(url):
            return httpx.Response(200, json=_sample_usages_raw(), request=httpx.Request("GET", str(url)))
        elif "me" in str(url):
            return httpx.Response(500, request=httpx.Request("GET", str(url)))
        return httpx.Response(404, request=httpx.Request("GET", str(url)))

    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)

    data = await provider.fetch_usage()
    assert "usages" in data
    assert data["user_info"] is None

    # Falls back to LEVEL_INTERMEDIATE -> Allegretto
    parsed = provider.parse_usage(data)
    assert parsed.membership_level == "Allegretto"
