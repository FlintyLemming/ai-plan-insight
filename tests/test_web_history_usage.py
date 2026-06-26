import asyncio

from ai_plan_insight.config import ProviderConfig
from ai_plan_insight.models import HistoryModelUsage, HistoryUsagePeriod, UsageInfo
import ai_plan_insight.web as web


class HistoryProvider:
    @property
    def name(self) -> str:
        return "GLM Coding Plan"

    def authenticate(self) -> None:
        pass

    async def fetch_usage(self) -> dict:
        return {"data": {"level": "pro", "limits": []}}

    def parse_usage(self, raw_data: dict) -> UsageInfo:
        return UsageInfo(
            provider="GLM Coding Plan",
            membership_level="pro",
            limits=[],
            raw_response=raw_data,
        )

    async def fetch_token_usage(self):
        return []

    async def fetch_history_usage(self):
        return HistoryUsagePeriod(
            period="30d",
            granularity="daily",
            x_time=["2026-06-25", "2026-06-26"],
            tokens_usage=[100, 200],
            model_call_count=[1, 2],
            total_tokens=300,
            total_calls=3,
            models=[
                HistoryModelUsage(
                    model_name="glm-4.6",
                    total_tokens=300,
                    total_calls=None,
                    tokens_usage=[100, 200],
                )
            ],
        )


class FailingHistoryProvider(HistoryProvider):
    async def fetch_history_usage(self):
        raise RuntimeError("history endpoint failed")


def test_fetch_one_attaches_history_usage(monkeypatch):
    monkeypatch.setattr(web, "_build_provider", lambda name, config: HistoryProvider())

    response = asyncio.run(web._fetch_one("bigmodel", ProviderConfig(api_key="test-key")))

    assert response.provider == "GLM Coding Plan"
    assert response.history_usage is not None
    assert response.history_usage.period == "30d"
    assert response.history_usage.total_tokens == 300
    assert response.history_usage.total_calls == 3
    assert response.history_usage.models[0].model_name == "glm-4.6"
    assert response.history_usage.models[0].total_calls is None


def test_fetch_one_ignores_history_failures(monkeypatch, caplog):
    monkeypatch.setattr(web, "_build_provider", lambda name, config: FailingHistoryProvider())

    response = asyncio.run(web._fetch_one("bigmodel", ProviderConfig(api_key="test-key")))

    assert response.provider == "GLM Coding Plan"
    assert response.history_usage is None
    assert "Failed to fetch history usage for bigmodel" in caplog.text
