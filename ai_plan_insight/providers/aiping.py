import httpx
from ..models import UsageInfo
from .base import BaseProvider


class AipingProvider(BaseProvider):
    """AIPing balance provider."""

    API_URL = "https://aiping.cn/api/v1/user/remain/points"

    @property
    def name(self) -> str:
        return "AIPing"

    def authenticate(self) -> None:
        self._headers = {
            "Authorization": f"Bearer {self.config.api_key}",
        }

    async def fetch_usage(self) -> dict:
        self.log.info("Request: GET %s", self.API_URL)
        async with httpx.AsyncClient() as client:
            response = await client.get(self.API_URL, headers=self._headers)
            self.log.info("Response: %d %s", response.status_code, response.reason_phrase)
            self.log.debug("Response body: %s", response.text)
            response.raise_for_status()
            return response.json()

    def parse_usage(self, raw_data: dict) -> UsageInfo:
        data = raw_data.get("data", {})
        balances = {}
        if "total_remain" in data:
            balances["total"] = f"{data['total_remain']:.2f}"
        if "gift_remain" in data:
            balances["gift"] = f"{data['gift_remain']:.2f}"
        if "recharge_remain" in data:
            balances["recharge"] = f"{data['recharge_remain']:.2f}"

        return UsageInfo(
            provider=self.name,
            balances=balances,
            raw_response=raw_data,
        )
