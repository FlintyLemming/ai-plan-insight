import httpx
from datetime import datetime, timezone
from ..models import UsageInfo, LimitDetail, UsageDetail
from .base import BaseProvider


class BigModelProvider(BaseProvider):
    """智谱 GLM Coding Plan usage provider."""

    API_URL = "https://open.bigmodel.cn/api/monitor/usage/quota/limit"

    @property
    def name(self) -> str:
        return "GLM Coding Plan"

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

    def _parse_reset_time(self, timestamp_ms: int | None) -> datetime | None:
        if not timestamp_ms:
            return None
        return datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc)

    def _get_unit_name(self, unit: int) -> str:
        unit_names = {
            1: "second",
            2: "minute",
            3: "hour",
            4: "day",
            5: "month",
            6: "year",
        }
        return unit_names.get(unit, f"unit_{unit}")

    def _parse_limits(self, raw_data: dict) -> list[LimitDetail]:
        limits = []
        data = raw_data.get("data", {})

        for limit in data.get("limits", []):
            limit_type = limit.get("type", "")
            unit = limit.get("unit", 0)
            number = limit.get("number", 1)
            reset_time = self._parse_reset_time(limit.get("nextResetTime"))

            duration = number
            time_unit = self._get_unit_name(unit)

            # Model-level breakdown for TIME_LIMIT (MCP usage)
            usage_details = []
            if limit_type == "TIME_LIMIT" and "usageDetails" in limit:
                for detail in limit["usageDetails"]:
                    usage_details.append(
                        UsageDetail(
                            model_code=detail.get("modelCode", ""),
                            usage=detail.get("usage", 0),
                        )
                    )

            if limit_type == "TIME_LIMIT":
                limit_val = str(limit.get("usage", 0))
                used = str(limit.get("currentValue", 0))
                remaining = str(limit.get("remaining", 0))
            else:
                limit_val = str(100)
                used = str(limit.get("percentage", 0))
                remaining = str(100 - limit.get("percentage", 0))

            limits.append(
                LimitDetail(
                    duration=duration,
                    time_unit=time_unit,
                    limit=limit_val,
                    used=used,
                    remaining=remaining,
                    reset_time=reset_time,
                    usage_details=usage_details,
                    limit_type=limit_type,
                )
            )
        return limits

    def parse_usage(self, raw_data: dict) -> UsageInfo:
        limits = self._parse_limits(raw_data)
        return UsageInfo(
            provider=self.name,
            membership_level=raw_data.get("data", {}).get("level"),
            limits=limits,
            raw_response=raw_data,
        )
