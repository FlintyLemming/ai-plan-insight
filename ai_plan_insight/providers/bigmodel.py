import httpx
from datetime import datetime, timezone, timedelta
from ..models import UsageInfo, LimitDetail, TokenUsagePeriod
from .base import BaseProvider


class BigModelProvider(BaseProvider):
    """智谱 GLM Coding Plan usage provider."""

    API_URL = "https://open.bigmodel.cn/api/monitor/usage/quota/limit"
    MODEL_USAGE_URL = "https://open.bigmodel.cn/api/monitor/usage/model-usage"

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

    async def _fetch_model_usage(self, client: httpx.AsyncClient, start: datetime, end: datetime) -> dict | None:
        params = {
            "startTime": start.strftime("%Y-%m-%d %H:%M:%S"),
            "endTime": end.strftime("%Y-%m-%d %H:%M:%S"),
        }
        self.log.info("Request: GET %s params=%s", self.MODEL_USAGE_URL, params)
        response = await client.get(self.MODEL_USAGE_URL, headers=self._headers, params=params)
        self.log.info("Response: %d %s", response.status_code, response.reason_phrase)
        response.raise_for_status()
        data = response.json()
        if data.get("code") != 200:
            self.log.warning("model-usage API error: %s", data.get("msg"))
            return None
        return data

    async def fetch_token_usage(self) -> list[TokenUsagePeriod]:
        """Fetch token usage for today, last 7 days, and last 30 days."""
        now = datetime.now(timezone(timedelta(hours=8)))
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

        periods = [
            ("today", today_start, now),
            ("7d", today_start - timedelta(days=7), now),
            ("30d", today_start - timedelta(days=30), now),
        ]

        results: list[TokenUsagePeriod] = []
        async with httpx.AsyncClient() as client:
            for period_name, start, end in periods:
                try:
                    data = await self._fetch_model_usage(client, start, end)
                    if data and (total := data.get("data", {}).get("totalUsage")):
                        results.append(TokenUsagePeriod(
                            period=period_name,
                            total_tokens=total.get("totalTokensUsage", 0),
                            total_calls=total.get("totalModelCallCount", 0),
                        ))
                except Exception as e:
                    self.log.warning("Failed to fetch %s token usage: %s", period_name, e)
        return results

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
