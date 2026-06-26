import asyncio
from datetime import timedelta

from ai_plan_insight.config import ProviderConfig
from ai_plan_insight.models import HistoryModelUsage, HistoryUsagePeriod, UsageInfo
from ai_plan_insight.providers.bigmodel import BigModelProvider


def sample_history_response() -> dict:
    return {
        "code": 200,
        "data": {
            "granularity": "daily",
            "x_time": ["2026-06-24", "2026-06-25", "2026-06-26"],
            "tokensUsage": [100, 200, 300],
            "modelCallCount": [1, 2, 3],
            "totalUsage": {
                "totalTokensUsage": 600,
                "totalModelCallCount": 6,
            },
            "modelDataList": [
                {
                    "modelName": "glm-4.6",
                    "tokensUsage": [40, 50, 60],
                    "totalModelCallCount": 4,
                },
                {
                    "modelName": "glm-4.6-air",
                    "tokensUsage": [60, 150, 240],
                },
            ],
        },
    }


def test_history_usage_models_can_be_nested_in_usage_info():
    history = HistoryUsagePeriod(
        period="30d",
        granularity="daily",
        x_time=["2026-06-24", "2026-06-25", "2026-06-26"],
        tokens_usage=[100, 200, 300],
        model_call_count=[1, 2, 3],
        total_tokens=600,
        total_calls=6,
        models=[
            HistoryModelUsage(
                model_name="glm-4.6",
                total_tokens=150,
                total_calls=None,
                tokens_usage=[40, 50, 60],
            )
        ],
    )

    usage = UsageInfo(provider="GLM Coding Plan", raw_response={}, history_usage=history)

    assert usage.history_usage == history
    assert usage.history_usage.models[0].total_calls is None


def test_parse_history_usage_totals_and_models():
    provider = BigModelProvider(ProviderConfig(api_key="test-key"))

    history = provider._parse_history_usage(sample_history_response())

    assert history is not None
    assert history.period == "30d"
    assert history.granularity == "daily"
    assert history.x_time == ["2026-06-24", "2026-06-25", "2026-06-26"]
    assert history.tokens_usage == [100, 200, 300]
    assert history.model_call_count == [1, 2, 3]
    assert history.total_tokens == 600
    assert history.total_calls == 6
    assert [model.model_name for model in history.models] == ["glm-4.6", "glm-4.6-air"]
    assert history.models[0].tokens_usage == [40, 50, 60]
    assert history.models[0].total_tokens == 150
    assert history.models[0].total_calls == 4
    assert history.models[1].tokens_usage == [60, 150, 240]
    assert history.models[1].total_tokens == 450
    assert history.models[1].total_calls is None


def test_parse_history_usage_normalizes_arrays_to_x_time_length():
    provider = BigModelProvider(ProviderConfig(api_key="test-key"))
    raw = sample_history_response()
    raw["data"]["tokensUsage"] = [100]
    raw["data"]["modelCallCount"] = [1, 2, 3, 4]
    raw["data"]["modelDataList"][0]["tokensUsage"] = [9]

    history = provider._parse_history_usage(raw)

    assert history is not None
    assert history.tokens_usage == [100, 0, 0]
    assert history.model_call_count == [1, 2, 3]
    assert history.models[0].tokens_usage == [9, 0, 0]
    assert history.models[0].total_tokens == 9


def test_parse_history_usage_returns_none_for_api_error():
    provider = BigModelProvider(ProviderConfig(api_key="test-key"))

    history = provider._parse_history_usage({"code": 500, "msg": "bad", "data": {}})

    assert history is None


def test_fetch_history_usage_uses_30_calendar_days_from_beijing_midnight(monkeypatch):
    provider = BigModelProvider(ProviderConfig(api_key="test-key"))
    captured = {}

    async def fake_fetch_model_usage(client, start, end):
        captured["start"] = start
        captured["end"] = end
        return sample_history_response()

    monkeypatch.setattr(provider, "_fetch_model_usage", fake_fetch_model_usage)

    history = asyncio.run(provider.fetch_history_usage(days=30))

    assert history is not None
    assert captured["start"].hour == 0
    assert captured["start"].minute == 0
    assert captured["start"].second == 0
    assert captured["start"].utcoffset() == timedelta(hours=8)
    assert captured["end"].utcoffset() == timedelta(hours=8)
    assert (captured["end"].date() - captured["start"].date()).days == 29
