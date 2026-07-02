"""Tests for the config-driven card ordering in /api/usage.

The sort key resolves each card's display name back to a config entry's
`order` field. These tests monkeypatch `load_config` so they don't depend on
the real config.json living in the repo root.
"""
from datetime import datetime, timedelta

from fastapi.testclient import TestClient

from ai_plan_insight.api_schemas import LimitResponse, UsageResponse
from ai_plan_insight.config import Config, ProviderConfig
import ai_plan_insight.web as web


def _set_config(monkeypatch, providers: dict[str, ProviderConfig]):
    """Make web.load_config return a Config built from the given providers."""
    cfg = Config(providers=providers)
    monkeypatch.setattr(web, "load_config", lambda _path=None: cfg)


def _resp(name: str) -> UsageResponse:
    return UsageResponse(provider=name, limits=[])


def test_cards_ordered_by_config_order(monkeypatch):
    # Give each provider an explicit order that does NOT match alphabetical.
    _set_config(monkeypatch, {
        "bigmodel": ProviderConfig(order=20),       # GLM Coding Plan
        "kimi": ProviderConfig(order=30),           # Kimi Coding Plan
        "zenmux": ProviderConfig(order=13),         # ZenMux
    })

    # Seed cached results (sorted by the hardcoded dict previously).
    monkeypatch.setattr(web, "_cached_results", [
        _resp("Kimi Coding Plan"),
        _resp("GLM Coding Plan"),
        _resp("ZenMux"),
    ])
    monkeypatch.setattr(web, "_pushed_results", {})
    monkeypatch.setattr(web, "_pushed_at", {})

    client = TestClient(web.app)
    providers = [u["provider"] for u in client.get("/api/usage").json()]

    assert providers == ["ZenMux", "GLM Coding Plan", "Kimi Coding Plan"]


def test_missing_order_defaults_to_last(monkeypatch):
    # bigmodel has no `order` -> defaults to 999 -> sorts after kimi (order 30).
    _set_config(monkeypatch, {
        "bigmodel": ProviderConfig(),               # order=999
        "kimi": ProviderConfig(order=30),
    })
    monkeypatch.setattr(web, "_cached_results", [
        _resp("GLM Coding Plan"),
        _resp("Kimi Coding Plan"),
    ])
    monkeypatch.setattr(web, "_pushed_results", {})
    monkeypatch.setattr(web, "_pushed_at", {})

    client = TestClient(web.app)
    providers = [u["provider"] for u in client.get("/api/usage").json()]

    assert providers == ["Kimi Coding Plan", "GLM Coding Plan"]


def test_pushed_card_uses_config_order(monkeypatch):
    # Claude is push-only: it has a config entry carrying only `order`.
    _set_config(monkeypatch, {
        "bigmodel": ProviderConfig(order=20),       # GLM Coding Plan
        "claude": ProviderConfig(order=12),         # Claude 订阅 (push-only)
    })
    monkeypatch.setattr(web, "_cached_results", [_resp("GLM Coding Plan")])

    # Inject a Claude push as if /api/push/claude had just been called.
    monkeypatch.setattr(web, "_pushed_results", {
        "claude": UsageResponse(provider="Claude 订阅", limits=[
            LimitResponse(duration=5, time_unit="小时", limit="100", used="10", remaining="90"),
        ]),
    })
    monkeypatch.setattr(web, "_pushed_at", {"claude": datetime.now().astimezone()})

    client = TestClient(web.app)
    providers = [u["provider"] for u in client.get("/api/usage").json()]

    # Claude (order 12) should appear before GLM (order 20).
    assert providers == ["Claude 订阅", "GLM Coding Plan"]


def test_pushed_card_without_config_entry_sorts_last(monkeypatch):
    # No config entry for claude -> resolves display name fails -> 999 -> last.
    _set_config(monkeypatch, {
        "bigmodel": ProviderConfig(order=20),       # GLM Coding Plan
    })
    monkeypatch.setattr(web, "_cached_results", [_resp("GLM Coding Plan")])
    monkeypatch.setattr(web, "_pushed_results", {
        "claude": _resp("Claude 订阅"),
    })
    monkeypatch.setattr(web, "_pushed_at", {"claude": datetime.now().astimezone()})

    client = TestClient(web.app)
    providers = [u["provider"] for u in client.get("/api/usage").json()]

    assert providers == ["GLM Coding Plan", "Claude 订阅"]


def test_expired_push_not_returned(monkeypatch):
    _set_config(monkeypatch, {
        "bigmodel": ProviderConfig(order=20),
        "claude": ProviderConfig(order=12),
    })
    monkeypatch.setattr(web, "_cached_results", [_resp("GLM Coding Plan")])
    monkeypatch.setattr(web, "_pushed_results", {"claude": _resp("Claude 订阅")})
    # Push timestamp older than PUSH_TTL_SECONDS (30 min).
    monkeypatch.setattr(
        web, "_pushed_at",
        {"claude": datetime.now().astimezone() - timedelta(minutes=31)},
    )

    client = TestClient(web.app)
    providers = [u["provider"] for u in client.get("/api/usage").json()]

    assert providers == ["GLM Coding Plan"]
