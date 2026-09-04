import asyncio
import httpx
from datetime import datetime
from ..models import UsageInfo, LimitDetail
from .base import BaseProvider


class KimiProvider(BaseProvider):
    """Kimi Coding Plan usage provider."""

    API_URL = "https://api.kimi.com/coding/v1/usages"
    USER_URL = "https://api.kimi.com/coding/v1/me"

    MEMBERSHIP_LEVEL_MAP = {
        "LEVEL_BASIC": "Andante",
        "LEVEL_INTERMEDIATE": "Allegretto",
        "LEVEL_ADVANCED": "Allegro",
    }

    @property
    def name(self) -> str:
        return "Kimi Coding Plan"

    def authenticate(self) -> None:
        self._headers = {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "application/json",
        }

    async def fetch_usage(self) -> dict:
        self.log.info("Request: GET %s and GET %s", self.API_URL, self.USER_URL)
        async with httpx.AsyncClient() as client:
            usages_task = client.get(self.API_URL, headers=self._headers)
            user_task = client.get(self.USER_URL, headers=self._headers)
            usages_res, user_res = await asyncio.gather(usages_task, user_task, return_exceptions=True)

            if isinstance(usages_res, Exception):
                raise usages_res

            self.log.info("Response usages: %d %s", usages_res.status_code, usages_res.reason_phrase)
            self.log.debug("Response usages body: %s", usages_res.text)
            usages_res.raise_for_status()
            usages_data = usages_res.json()

            user_data = None
            if not isinstance(user_res, Exception) and user_res.status_code == 200:
                self.log.info("Response me: %d %s", user_res.status_code, user_res.reason_phrase)
                self.log.debug("Response me body: %s", user_res.text)
                user_data = user_res.json()
            elif isinstance(user_res, Exception):
                self.log.warning("Failed to fetch Kimi user info: %s", user_res)
            else:
                self.log.warning("Kimi /me returned %d: %s", user_res.status_code, user_res.text)

            return {
                "usages": usages_data,
                "user_info": user_data,
            }

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
        # Support both wrapped format {"usages": ..., "user_info": ...} and direct /usages response
        usages_data = (
            raw_data.get("usages")
            if "usages" in raw_data and isinstance(raw_data.get("usages"), dict)
            else raw_data
        )
        user_info = (
            raw_data.get("user_info")
            if isinstance(raw_data.get("user_info"), dict)
            else {}
        )

        user = usages_data.get("user", {})
        limits = self._parse_limits(usages_data)

        # 1. First priority: user_level_name from /coding/v1/me (e.g. "Allegretto")
        membership_level = user_info.get("user_level_name")

        # 2. Fallback: map internal level enum from /usages (e.g. "LEVEL_INTERMEDIATE" -> "Allegretto")
        if not membership_level:
            raw_level = user.get("membership", {}).get("level")
            if raw_level:
                membership_level = self.MEMBERSHIP_LEVEL_MAP.get(raw_level, raw_level)

        user_id = user_info.get("user_id") or user.get("userId", "")

        return UsageInfo(
            provider=self.name,
            user_id=user_id,
            membership_level=membership_level,
            limits=limits,
            raw_response=raw_data,
        )
