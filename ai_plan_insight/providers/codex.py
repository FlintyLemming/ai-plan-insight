import httpx
from datetime import datetime

from ..models import UsageInfo, LimitDetail, ModelStat
from .base import BaseProvider


class CodexProvider(BaseProvider):
    """Sub2API Codex relay — quota-limited keys or wallet balance."""

    DEFAULT_BASE_URL = "https://api2.ai.aiatechco.com"
    HIDDEN_MODELS = {"gpt5.5", "glm-5.5", "glm5.5"}

    @property
    def name(self) -> str:
        return "自购 Codex 中转站"

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

    def _parse_model_stats(self, raw_data: dict) -> list[ModelStat]:
        return [
            ModelStat(
                model=m["model"],
                total_tokens=m["total_tokens"],
                requests=m["requests"],
            )
            for m in raw_data.get("model_stats", [])
            if m["model"] not in self.HIDDEN_MODELS
        ]

    def _parse_expires_at(self, raw_data: dict) -> datetime | None:
        expires_at = raw_data.get("expires_at")
        if not expires_at:
            return None
        if isinstance(expires_at, datetime):
            return expires_at
        try:
            return datetime.fromisoformat(str(expires_at).replace("Z", "+00:00"))
        except (ValueError, TypeError):
            return None

    def _parse_quota_limited(self, raw_data: dict) -> UsageInfo:
        quota = raw_data.get("quota", {})
        limit = float(quota.get("limit", 0) or 0)
        used = float(quota.get("used", 0) or 0)
        remaining = float(quota.get("remaining", raw_data.get("remaining", 0)) or 0)

        reset_time = self._parse_expires_at(raw_data)
        balances: dict[str, str] = {}
        if reset_time:
            balances["套餐到期"] = reset_time.strftime("%Y-%m-%d")
        days_left = raw_data.get("days_until_expiry")
        if days_left is not None:
            balances["剩余天数"] = f"{days_left} 天"

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
            model_stats=self._parse_model_stats(raw_data),
        )

    def _parse_unrestricted(self, raw_data: dict) -> UsageInfo:
        balance = float(raw_data.get("balance", raw_data.get("remaining", 0)) or 0)
        today = (raw_data.get("usage") or {}).get("today") or {}
        today_cost = float(today.get("actual_cost", today.get("cost", 0)) or 0)

        balances: dict[str, str] = {
            "套餐": str(raw_data.get("planName", "钱包余额")),
            "余额": f"${balance:.2f}",
        }

        sub = raw_data.get("subscription")
        if isinstance(sub, dict):
            expires_at = sub.get("expires_at")
            if expires_at:
                try:
                    end = datetime.fromisoformat(str(expires_at).replace("Z", "+00:00"))
                    balances["订阅到期"] = end.strftime("%Y-%m-%d")
                except (ValueError, TypeError):
                    pass

        return UsageInfo(
            provider=self.name,
            balances=balances,
            limits=[
                LimitDetail(
                    duration=0,
                    time_unit="USD",
                    limit=f"{balance:.2f}",
                    used=f"{today_cost:.2f}",
                    remaining=f"{balance:.2f}",
                )
            ],
            raw_response=raw_data,
            model_stats=self._parse_model_stats(raw_data),
        )

    def parse_usage(self, raw_data: dict) -> UsageInfo:
        if raw_data.get("mode") == "quota_limited" or raw_data.get("quota"):
            return self._parse_quota_limited(raw_data)
        return self._parse_unrestricted(raw_data)


class CodexSecurityProvider(CodexProvider):
    """Sub2API Codex relay (tinykittens / shared wallet)."""

    DEFAULT_BASE_URL = "https://tinykittens.online"

    @property
    def name(self) -> str:
        return "白嫖 Codex Security 中转"
