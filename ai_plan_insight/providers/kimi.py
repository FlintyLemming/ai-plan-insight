import httpx
from datetime import datetime
from ..models import UsageInfo, LimitDetail
from .base import BaseProvider


class KimiProvider(BaseProvider):
    """Kimi Coding Plan usage provider."""

    API_URL = "https://api.kimi.com/coding/v1/usages"

    @property
    def name(self) -> str:
        return "Kimi Coding Plan"

    def authenticate(self) -> None:
        self._headers = {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "application/json",
        }

    async def fetch_usage(self) -> dict:
        self.log.info("Request: GET %s", self.API_URL)
        async with httpx.AsyncClient() as client:
            response = await client.get(self.API_URL, headers=self._headers)
            self.log.info("Response: %d %s", response.status_code, response.reason_phrase)
            self.log.debug("Response body: %s", response.text)
            response.raise_for_status()
            return response.json()

    def _parse_reset_time(self, reset_time_str: str | None) -> datetime | None:
        if not reset_time_str:
            return None
        return datetime.fromisoformat(reset_time_str.replace("Z", "+00:00"))

    def _parse_limits(self, raw_data: dict) -> list[LimitDetail]:
        limits = []

        # Weekly limit from top-level usage object
        usage = raw_data.get("usage", {})
        if usage:
            reset_time = self._parse_reset_time(usage.get("resetTime"))
            limits.append(
                LimitDetail(
                    duration=1,
                    time_unit="week",
                    limit=usage.get("limit", "0"),
                    used=usage.get("used", "0"),
                    remaining=usage.get("remaining", "0"),
                    reset_time=reset_time,
                )
            )

        # Other rate limits
        for limit in raw_data.get("limits", []):
            window = limit.get("window", {})
            detail = limit.get("detail", {})
            reset_time = self._parse_reset_time(detail.get("resetTime"))
            time_unit = window.get("timeUnit", "").replace("TIME_UNIT_", "").lower()

            # Normalize minutes to hours when >= 60
            dur = window.get("duration", 0)
            if time_unit == "minute" and dur >= 60 and dur % 60 == 0:
                dur = dur // 60
                time_unit = "hour"

            limits.append(
                LimitDetail(
                    duration=dur,
                    time_unit=time_unit,
                    limit=detail.get("limit", "0"),
                    used=detail.get("used", "0"),
                    remaining=detail.get("remaining", "0"),
                    reset_time=reset_time,
                )
            )
        return limits

    def parse_usage(self, raw_data: dict) -> UsageInfo:
        user = raw_data.get("user", {})
        limits = self._parse_limits(raw_data)

        return UsageInfo(
            provider=self.name,
            user_id=user.get("userId", ""),
            membership_level="Andante",
            limits=limits,
            raw_response=raw_data,
        )
