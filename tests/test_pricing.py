"""Tests for the USD pricing module (parsers, id normalization, service)."""

import asyncio
import json

import pytest

from ai_plan_insight import pricing
from ai_plan_insight.pricing import (
    ModelPrice,
    PricingService,
    _parse_litellm,
    _parse_modelsdev,
    _parse_openrouter,
    lookup_candidates,
)

# ── parsers ───────────────────────────────────────────────────


def test_litellm_parses_per_token_and_filters_junk():
    raw = {
        "gpt-4o": {
            "litellm_provider": "openai",
            "max_tokens": 128000,
            "input_cost_per_token": 2.5e-6,
            "output_cost_per_token": 10e-6,
            "cache_read_input_token_cost": 1.25e-6,
            "cache_creation_input_token_cost": 0.0,
            "input_cost_per_token_above_128k_tokens": 5e-6,  # tiered: ignored
        },
        "github_copilot/gpt-4o": {
            "input_cost_per_token": 0.0,
            "output_cost_per_token": 0.0,
        },
        "free-model": {"litellm_provider": "x"},  # no prices: unusable
        "_comment": "metadata, not a dict-of-prices",
    }
    out = _parse_litellm(raw)
    assert set(out) == {"gpt-4o"}
    p = out["gpt-4o"]
    assert (p.input, p.output, p.cache_read, p.cache_write) == (
        2.5e-6,
        10e-6,
        1.25e-6,
        0.0,
    )


def test_litellm_lowercases_keys():
    out = _parse_litellm(
        {"GPT-4O": {"input_cost_per_token": 1e-6, "output_cost_per_token": 2e-6}}
    )
    assert set(out) == {"gpt-4o"}


def test_openrouter_parses_string_prices_no_cache():
    raw = {
        "data": [
            {
                "id": "anthropic/claude-sonnet-4",
                "pricing": {"prompt": "0.000003", "completion": "0.000015"},
            },
            {"id": "broken", "pricing": {"prompt": "n/a", "completion": "n/a"}},
            {"id": "no-pricing"},
        ]
    }
    out = _parse_openrouter(raw)
    assert set(out) == {"anthropic/claude-sonnet-4"}
    p = out["anthropic/claude-sonnet-4"]
    assert (p.input, p.output, p.cache_read, p.cache_write) == (3e-6, 15e-6, 0.0, 0.0)


def test_modelsdev_converts_per_million_and_dual_keys():
    raw = {
        "anthropic": {
            "models": {
                "claude-sonnet-4": {
                    "cost": {
                        "input": 3.0,
                        "output": 15.0,
                        "cache_read": 0.3,
                        "cache_write": 3.75,
                    }
                }
            }
        }
    }
    out = _parse_modelsdev(raw)
    assert out["anthropic/claude-sonnet-4"].input == 3e-6
    assert out["anthropic/claude-sonnet-4"].output == 15e-6
    assert out["claude-sonnet-4"].cache_read == 0.3e-6
    assert out["claude-sonnet-4"].cache_write == 3.75e-6


def test_modelsdev_skips_entries_without_cost():
    raw = {"p": {"models": {"m": {"name": "no cost"}, "n": "not-a-dict"}}}
    assert _parse_modelsdev(raw) == {}


def test_reasoning_tokens_priced_at_output_rate():
    p = ModelPrice(input=1e-6, output=5e-6)
    assert p.cost(0, 0, 0, 0, 1000) == pytest.approx(0.005)
    assert p.cost(1000, 1000, 1000, 1000, 1000) == pytest.approx(
        1000 * 1e-6 + 1000 * 5e-6 + 1000 * 5e-6
    )


# ── lookup candidates ─────────────────────────────────────────


def test_lookup_candidates_lowercase_and_backend_strip():
    cands = lookup_candidates("GLM-5.2:BigModel")
    assert cands[:2] == ["glm-5.2:bigmodel", "glm-5.2"]


def test_lookup_candidates_org_strip_and_backend_combo():
    cands = lookup_candidates("z-ai/glm-5.2:bigmodel")
    assert "z-ai/glm-5.2" in cands
    assert "glm-5.2" in cands


def test_lookup_candidates_date_suffix_strip():
    cands = lookup_candidates("qwen3.7-max-2026-05-17")
    assert "qwen3.7-max" in cands
    # org-qualified dated id strips both the date and the org prefix
    cands = lookup_candidates("qwen/qwen3.7-max-2026-05-17")
    assert "qwen/qwen3.7-max" in cands
    assert "qwen3.7-max" in cands


def test_lookup_candidates_version_separator_swap():
    assert "claude-opus-4.8" in lookup_candidates("claude-opus-4-8")
    assert "claude-opus-4-8" in lookup_candidates("claude-opus-4.8")


def test_lookup_candidates_org_prepend_variants():
    cands = lookup_candidates("kimi-k2.7-code")
    assert "moonshotai/kimi-k2.7-code" in cands
    cands = lookup_candidates("glm-5.2")
    assert "z-ai/glm-5.2" in cands


def test_lookup_candidates_sentinel_has_no_variants():
    # sentinels still get lowercased but never match anything
    assert "unknown" in lookup_candidates("unknown")
    assert lookup_candidates("UNKNOWN")[:1] == ["unknown"]


def test_lookup_candidates_deduped():
    cands = lookup_candidates("gpt-4o")
    assert len(cands) == len(set(cands))


# ── PricingService ────────────────────────────────────────────

LITE = {
    "gpt-4o": {"input_cost_per_token": 2.5e-6, "output_cost_per_token": 10e-6},
    "claude-opus-4.8": {"input_cost_per_token": 15e-6, "output_cost_per_token": 75e-6},
}
OROUTER = {
    "data": [
        {"id": "gpt-4o", "pricing": {"prompt": "9", "completion": "9"}},
        {"id": "or-only", "pricing": {"prompt": "4", "completion": "8"}},
    ]
}
MDEV = {"openai": {"models": {"mdev-only": {"cost": {"input": 3.0, "output": 15.0}}}}}


def _payloads():
    return {
        pricing.LITELLM_URL: LITE,
        pricing.OPENROUTER_URL: OROUTER,
        pricing.MODELSDEV_URL: MDEV,
    }


@pytest.fixture
def fake_fetch(monkeypatch):
    payloads = _payloads()

    async def _fake(client, url):
        return payloads[url]

    monkeypatch.setattr(pricing, "_fetch_json", _fake)


def test_chain_prefers_litellm_then_openrouter_then_modelsdev(fake_fetch, tmp_path):
    svc = PricingService(tmp_path / "cache.json")
    asyncio.run(svc.ensure_ready())
    assert svc.ready
    # LiteLLM beats OpenRouter for the same id
    assert svc.price_for("gpt-4o").input == 2.5e-6
    # separator-swap candidate hits LiteLLM before OpenRouter is consulted
    assert svc.price_for("claude-opus-4-8").input == 15e-6
    # OpenRouter is the fallback for ids LiteLLM lacks
    assert svc.price_for("or-only").input == 4.0
    # models.dev is the last resort
    assert svc.price_for("mdev-only").input == 3e-6
    # unknown id → no price
    assert svc.price_for("totally-unknown") is None


def test_not_ready_when_no_source_available(tmp_path):
    svc = PricingService(tmp_path / "cache.json")
    assert not svc.ready
    assert svc.price_for("gpt-4o") is None


def test_cache_roundtrip_and_ttl_skip(monkeypatch, fake_fetch, tmp_path):
    path = tmp_path / "cache.json"
    asyncio.run(PricingService(path).ensure_ready())
    assert path.exists()

    calls = []

    async def _boom(client, url):
        calls.append(url)
        raise RuntimeError("offline")

    monkeypatch.setattr(pricing, "_fetch_json", _boom)
    svc2 = PricingService(path)  # loads cache, ready immediately
    assert svc2.ready
    assert svc2.price_for("gpt-4o").input == 2.5e-6
    asyncio.run(svc2.refresh())  # TTL fresh → no fetch
    assert calls == []


def test_stale_cache_served_when_refresh_fails(monkeypatch, fake_fetch, tmp_path):
    path = tmp_path / "cache.json"
    svc = PricingService(path)
    asyncio.run(svc.ensure_ready())
    svc._fetched_at = 0.0  # force stale

    async def _boom(client, url):
        raise RuntimeError("offline")

    monkeypatch.setattr(pricing, "_fetch_json", _boom)
    asyncio.run(svc.refresh())  # must not raise
    assert svc.price_for("gpt-4o") is not None  # stale data retained


def test_all_sources_fail_does_not_mark_fresh(monkeypatch, tmp_path):
    async def _boom(client, url):
        raise RuntimeError("offline")

    monkeypatch.setattr(pricing, "_fetch_json", _boom)
    svc = PricingService(tmp_path / "cache.json")
    asyncio.run(svc.refresh())
    assert svc._fetched_at == 0.0
    assert not (tmp_path / "cache.json").exists()


def test_partial_source_failure_keeps_other_sources(monkeypatch, tmp_path):
    payloads = _payloads()

    async def _half(client, url):
        if url == pricing.OPENROUTER_URL:
            raise RuntimeError("down")
        return payloads[url]

    monkeypatch.setattr(pricing, "_fetch_json", _half)
    svc = PricingService(tmp_path / "cache.json")
    asyncio.run(svc.ensure_ready())
    assert svc.price_for("mdev-only").input == 3e-6
    assert svc.price_for("gpt-4o").input == 2.5e-6
    assert svc.price_for("or-only") is None


def test_ensure_ready_timeout_never_raises(monkeypatch, tmp_path):
    async def _hang(client, url):
        await asyncio.sleep(30)
        return {}

    monkeypatch.setattr(pricing, "_fetch_json", _hang)
    svc = PricingService(tmp_path / "cache.json")
    asyncio.run(svc.ensure_ready(timeout=0.05))  # must not raise
    assert not svc.ready
