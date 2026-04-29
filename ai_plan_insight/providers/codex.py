import httpx
from datetime import datetime

from ..models import UsageInfo, LimitDetail, ModelStat
from .base import BaseProvider


class CodexProvider(BaseProvider):
    """Codex relay API provider."""

    DEFAULT_BASE_URL = "https://api2.ai.aiatechco.com"
    HIDDEN_MODELS = {"gpt5.5", "gpt-5.5", "glm-5.5", "glm5.5"}

    @property
    def name(self) -> str:
        return "Codex 中转站"

    def authenticate(self) -> None:
        self._headers = {
            "Authorization": f"Bearer {self.config.api_key}",
        }
        self._base_url = self.config.base_url or self.DEFAULT_BASE_URL

    async def fetch_usage(self) -> dict:
        url = f"{self._base_url}/v1/usage"
        self.log.info("Request: GET %s", url)
        async with httpx.AsyncClient() as client:
            response = await client.get(url, headers=self._headers)
            self.log.info("Response: %d %s", response.status_code, response.reason_phrase)
            self.log.debug("Response body: %s", response.text)
            response.raise_for_status()
            return response.json()

    def parse_usage(self, raw_data: dict) -> UsageInfo:
        quota = raw_data.get("quota", {})
        limit = quota.get("limit", 0)
        used = quota.get("used", 0)
        remaining = quota.get("remaining", 0)

        expires_at = raw_data.get("expires_at")
        reset_time = None
        if expires_at:
            try:
                reset_time = datetime.fromisoformat(expires_at)
            except (ValueError, TypeError):
                pass

        balances = {}
        if reset_time:
            balances["套餐到期"] = reset_time.strftime("%Y-%m-%d")
        days_left = raw_data.get("days_until_expiry")
        if days_left is not None:
            balances["剩余天数"] = f"{days_left} 天"

        model_stats = [
            ModelStat(
                model=m["model"],
                total_tokens=m["total_tokens"],
                requests=m["requests"],
            )
            for m in raw_data.get("model_stats", [])
            if m["model"] not in self.HIDDEN_MODELS
        ]

        return UsageInfo(
            provider=self.name,
            balances=balances,
            limits=[
                LimitDetail(
                    duration=0,
                    time_unit="USD",
                    limit=f"{limit:.2f}",
                    used=f"{used:.2f}",
                    remaining=f"{remaining:.2f}",
                    reset_time=reset_time,
                )
            ],
            raw_response=raw_data,
            model_stats=model_stats,
        )
