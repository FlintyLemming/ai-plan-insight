import httpx
from ..models import UsageInfo, LimitDetail
from .base import BaseProvider


class XfyunProvider(BaseProvider):
    """讯飞星辰 MaaS Coding Plan usage provider."""

    API_URL = "https://maas.xfyun.cn/api/v1/gpt-finetune/coding-plan/list"

    @property
    def name(self) -> str:
        return "讯飞星辰 Coding Plan"

    def authenticate(self) -> None:
        parts = []
        if self.config.sso_session_id:
            parts.append(f"ssoSessionId={self.config.sso_session_id}")
        if self.config.account_id:
            parts.append(f"account_id={self.config.account_id}")
        self._headers = {
            "Cookie": "; ".join(parts),
            "Accept": "application/json",
            "User-Agent": "XfyunUsageMonitor/1.0",
        }

    async def fetch_usage(self) -> dict:
        self.log.info("Request: GET %s", self.API_URL)
        async with httpx.AsyncClient() as client:
            response = await client.get(
                self.API_URL,
                params={"page": 1, "size": 6},
                headers=self._headers,
            )
            self.log.info("Response: %d %s", response.status_code, response.reason_phrase)
            self.log.debug("Response body: %s", response.text)
            response.raise_for_status()
            data = response.json()
            if data.get("code") != 0:
                raise ValueError(f"API error: code={data.get('code')}, msg={data.get('msg')}")
            return data

    def parse_usage(self, raw_data: dict) -> UsageInfo:
        rows = raw_data.get("data", {}).get("rows", [])
        if not rows:
            return UsageInfo(provider=self.name, raw_response=raw_data)

        # Use the first plan
        row = rows[0]
        usage = row.get("codingPlanUsageDTO", {})
        limits = []

        # 5-hour limit
        rp5h_usage = usage.get("rp5hUsage", 0) or 0
        rp5h_limit = usage.get("rp5hLimit", 0) or 0
        if rp5h_limit > 0:
            limits.append(LimitDetail(
                duration=5,
                time_unit="hour",
                limit=str(rp5h_limit),
                used=str(rp5h_usage),
                remaining=str(rp5h_limit - rp5h_usage),
            ))

        # Weekly limit
        rpw_usage = usage.get("rpwUsage", 0) or 0
        rpw_limit = usage.get("rpwLimit", 0) or 0
        if rpw_limit > 0:
            limits.append(LimitDetail(
                duration=1,
                time_unit="week",
                limit=str(rpw_limit),
                used=str(rpw_usage),
                remaining=str(rpw_limit - rpw_usage),
            ))

        # Package (monthly) limit
        pkg_usage = usage.get("packageUsage", 0) or 0
        pkg_limit = usage.get("packageLimit", 0) or 0
        pkg_left = usage.get("packageLeft", 0) or 0
        if pkg_limit > 0:
            limits.append(LimitDetail(
                duration=1,
                time_unit="month",
                limit=str(pkg_limit),
                used=str(pkg_usage),
                remaining=str(pkg_left),
            ))

        balances = {}
        expires_at = row.get("expiresAt")
        if expires_at:
            balances["套餐到期"] = expires_at

        return UsageInfo(
            provider=self.name,
            membership_level="高效版",
            user_id="GLM-5.1",
            limits=limits,
            balances=balances,
            raw_response=raw_data,
        )
