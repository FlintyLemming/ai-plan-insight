import httpx
from datetime import datetime, timezone, timedelta
from ..models import UsageInfo, LimitDetail, TokenUsagePeriod, HistoryModelUsage, HistoryUsagePeriod
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

    @staticmethod
    def _normalize_int_series(values: list | None, length: int) -> list[int]:
        normalized: list[int] = []
        for value in (values or [])[:length]:
            try:
                normalized.append(int(value or 0))
            except (TypeError, ValueError):
                normalized.append(0)
        if len(normalized) < length:
            normalized.extend([0] * (length - len(normalized)))
        return normalized

    @staticmethod
    def _optional_total_calls(item: dict) -> int | None:
        for key in ("totalModelCallCount", "modelCallCount", "totalCalls", "calls", "requests"):
            if key in item and item.get(key) is not None:
                try:
                    return int(item.get(key) or 0)
                except (TypeError, ValueError):
                    return None
        return None

    def _parse_history_usage(self, raw_data: dict | None) -> HistoryUsagePeriod | None:
        if not raw_data or raw_data.get("code") != 200:
            return None

        data = raw_data.get("data", {})
        x_time = [str(item) for item in data.get("x_time", [])]
        length = len(x_time)
        tokens_usage = self._normalize_int_series(data.get("tokensUsage"), length)
        model_call_count = self._normalize_int_series(data.get("modelCallCount"), length)

        total_usage = data.get("totalUsage", {})
        total_tokens = int(total_usage.get("totalTokensUsage") or sum(tokens_usage))
        total_calls = int(total_usage.get("totalModelCallCount") or sum(model_call_count))

        models: list[HistoryModelUsage] = []
        for item in data.get("modelDataList", []):
            model_name = str(item.get("modelName") or item.get("model") or "Unknown")
            model_tokens = self._normalize_int_series(item.get("tokensUsage"), length)
            model_total_tokens = int(
                item.get("totalTokens")
                or item.get("totalTokensUsage")
                or sum(model_tokens)
            )
            models.append(
                HistoryModelUsage(
                    model_name=model_name,
                    total_tokens=model_total_tokens,
                    total_calls=self._optional_total_calls(item),
                    tokens_usage=model_tokens,
                )
            )

        return HistoryUsagePeriod(
            period="30d",
            granularity=str(data.get("granularity") or "daily"),
            x_time=x_time,
            tokens_usage=tokens_usage,
            model_call_count=model_call_count,
            total_tokens=total_tokens,
            total_calls=total_calls,
            models=models,
        )

    async def fetch_history_usage(self, days: int = 30) -> HistoryUsagePeriod | None:
        """Fetch daily model token usage for the last `days` calendar days."""
        now = datetime.now(timezone(timedelta(hours=8)))
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        start = today_start - timedelta(days=days - 1)

        async with httpx.AsyncClient() as client:
            data = await self._fetch_model_usage(client, start, now)
        return self._parse_history_usage(data)

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
            7: "week",
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

            # BigModel sometimes reports the weekly token limit as unit=year
            # while the next reset time is only ~7 days away. Correct it.
            if (
                limit_type == "TOKENS_LIMIT"
                and unit == 6
                and number == 1
                and reset_time is not None
            ):
                seconds_to_reset = (reset_time - datetime.now(timezone.utc)).total_seconds()
                if 0 < seconds_to_reset <= 10 * 24 * 3600:
                    duration = 1
                    time_unit = "week"

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

        # Sort by time window length, putting TIME_LIMIT (MCP call count)
        # at the end when windows overlap.
        limits.sort(key=lambda l: (self._time_window_seconds(l), l.limit_type == "TIME_LIMIT"))
        return limits

    @staticmethod
    def _time_window_seconds(limit: LimitDetail) -> int:
        multipliers = {
            "second": 1,
            "minute": 60,
            "hour": 3600,
            "day": 86400,
            "week": 7 * 86400,
            "month": 30 * 86400,
            "year": 365 * 86400,
        }
        return limit.duration * multipliers.get(limit.time_unit, 0)

    def parse_usage(self, raw_data: dict) -> UsageInfo:
        limits = self._parse_limits(raw_data)
        return UsageInfo(
            provider=self.name,
            membership_level=raw_data.get("data", {}).get("level"),
            limits=limits,
            raw_response=raw_data,
        )
