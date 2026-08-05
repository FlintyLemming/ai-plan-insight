import httpx
from datetime import datetime, timezone

from ..models import UsageInfo, LimitDetail
from .base import BaseProvider


class NewApiProvider(BaseProvider):
    """New API / One API relay — per-token quota via ``GET /api/usage/token/``.

    new-api (https://github.com/QuantumNous/new-api) exposes a read-only
    token usage endpoint that accepts the relay API key itself as a Bearer
    token. Quota is stored in internal units where 500,000 quota == $1
    (``common.QuotaPerUnit`` in the new-api source).
    """

    QUOTA_PER_USD = 500_000.0
    USAGE_ENDPOINT = "/api/usage/token/"

    @property
    def name(self) -> str:
        return "New API"

    def authenticate(self) -> None:
        self._headers = {
            "Authorization": f"Bearer {self.config.api_key}",
        }

    async def fetch_usage(self) -> dict:
        base = (self.config.base_url or "").rstrip("/")
        if not base:
            raise ValueError(f"{self.name}: 未配置站点地址 (base_url)")
        url = f"{base}{self.USAGE_ENDPOINT}"

        self.log.info("Request: GET %s", url)
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url, headers=self._headers)
            self.log.info("Response: %d %s", response.status_code, response.reason_phrase)
            self.log.debug("Response body: %s", response.text)
            response.raise_for_status()
            return response.json()

    @staticmethod
    def _extract_data(raw_data: dict) -> dict:
        data = raw_data.get("data")
        if not isinstance(data, dict):
            message = raw_data.get("message") or "响应中没有 data 字段"
            raise ValueError(str(message))
        return data

    @staticmethod
    def _parse_expires_at(expires_at) -> datetime | None:
        """expires_at is unix seconds; 0 or -1 means no expiry."""
        try:
            ts = int(expires_at)
        except (TypeError, ValueError):
            return None
        if ts <= 0:
            return None
        return datetime.fromtimestamp(ts, tz=timezone.utc)

    def parse_usage(self, raw_data: dict) -> UsageInfo:
        data = self._extract_data(raw_data)

        granted = float(data.get("total_granted", 0) or 0) / self.QUOTA_PER_USD
        used = float(data.get("total_used", 0) or 0) / self.QUOTA_PER_USD
        available = float(data.get("total_available", 0) or 0) / self.QUOTA_PER_USD
        unlimited = bool(data.get("unlimited_quota"))

        balances: dict[str, str] = {}
        token_name = (data.get("name") or "").strip()
        if token_name:
            balances["令牌"] = token_name

        reset_time = self._parse_expires_at(data.get("expires_at"))
        if reset_time:
            balances["到期"] = reset_time.strftime("%Y-%m-%d")

        limits: list[LimitDetail] = []
        if unlimited:
            balances["额度"] = "不限"
            balances["已用"] = f"${used:.2f}"
        else:
            limits.append(
                LimitDetail(
                    duration=0,
                    time_unit="USD",
                    limit=f"{granted:.2f}",
                    used=f"{used:.2f}",
                    remaining=f"{available:.2f}",
                    reset_time=reset_time,
                )
            )

        return UsageInfo(
            provider=self.name,
            balances=balances,
            limits=limits,
            raw_response=data,
        )
