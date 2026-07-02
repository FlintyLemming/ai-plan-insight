import httpx
from datetime import datetime
from ..models import UsageInfo, LimitDetail
from .base import BaseProvider


class ZenMuxProvider(BaseProvider):
    """ZenMux subscription quota & PAYG balance provider."""

    SUBSCRIPTION_URL = "https://zenmux.ai/api/v1/management/subscription/detail"
    BALANCE_URL = "https://zenmux.ai/api/v1/management/payg/balance"

    @property
    def name(self) -> str:
        return "ZenMux"

    def authenticate(self) -> None:
        api_key = self.config.api_key.strip()
        normalized = api_key.lower()
        if not api_key or normalized.startswith(("your_", "put_")) or set(normalized) <= {"x"}:
            raise ValueError("ZenMux requires a Management API Key")
        self._headers = {
            "Authorization": f"Bearer {api_key}",
        }

    async def _get(self, client: httpx.AsyncClient, url: str) -> dict:
        self.log.info("Request: GET %s", url)
        response = await client.get(url, headers=self._headers)
        self.log.info("Response: %d %s", response.status_code, response.reason_phrase)
        self.log.debug("Response body: %s", response.text)
        response.raise_for_status()
        return response.json()

    async def fetch_usage(self) -> dict:
        async with httpx.AsyncClient(timeout=30) as client:
            subscription = await self._get(client, self.SUBSCRIPTION_URL)
            balance = await self._get(client, self.BALANCE_URL)
        return {"subscription": subscription, "balance": balance}

    @staticmethod
    def _parse_reset_time(value: str | None) -> datetime | None:
        if not value:
            return None
        return datetime.fromisoformat(value.replace("Z", "+00:00"))

    @staticmethod
    def _format_date(value: str | None) -> str:
        if not value:
            return "—"
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).strftime("%Y-%m-%d")
        except ValueError:
            return value

    def _parse_quota_limits(self, sub: dict) -> list[LimitDetail]:
        """5h/7d rolling windows as percentage bars; monthly quota lives in balances."""
        limits: list[LimitDetail] = []
        for key, label, duration in (
            ("quota_5_hour", "5 小时滚动窗口", 5),
            ("quota_7_day", "7 天滚动窗口", 7),
        ):
            quota = sub.get(key) or {}
            if not quota:
                continue
            pct = quota.get("usage_percentage")
            used = f"{float(pct) * 100:.2f}" if pct is not None else "0"
            limits.append(
                LimitDetail(
                    duration=duration,
                    time_unit=label,
                    limit="100",
                    used=used,
                    remaining=f"{max(0.0, 100 - float(used)):.2f}",
                    reset_time=self._parse_reset_time(quota.get("resets_at")),
                    limit_type="PERCENT",
                )
            )
        return limits

    def _parse_balances(self, sub: dict, bal: dict) -> dict[str, str]:
        balances: dict[str, str] = {}

        plan = sub.get("plan") or {}
        tier = plan.get("tier")
        if tier:
            balances["套餐"] = str(tier).capitalize()

        expires_at = plan.get("expires_at")
        if expires_at:
            balances["套餐到期"] = self._format_date(expires_at)

        monthly = sub.get("quota_monthly") or {}
        max_flows = monthly.get("max_flows")
        max_value = monthly.get("max_value_usd")
        if max_flows is not None and max_value is not None:
            balances["月配额"] = f"{float(max_flows):,.0f} flows / ${float(max_value):,.2f}"

        rate = sub.get("effective_usd_per_flow")
        if rate is not None:
            balances["Flow 汇率"] = f"${float(rate):.5f}/flow"

        status = sub.get("account_status")
        if status and status != "healthy":
            balances["账号状态"] = str(status)

        total = bal.get("total_credits")
        top_up = bal.get("top_up_credits")
        bonus = bal.get("bonus_credits")
        if total is not None:
            balances["PAYG 总余额"] = f"${float(total):.2f}"
        if top_up is not None:
            balances["PAYG 充值余额"] = f"${float(top_up):.2f}"
        if bonus is not None:
            balances["PAYG 赠送余额"] = f"${float(bonus):.2f}"

        return balances

    def parse_usage(self, raw_data: dict) -> UsageInfo:
        sub_resp = raw_data.get("subscription") or {}
        bal_resp = raw_data.get("balance") or {}
        sub = sub_resp.get("data") or {}
        bal = bal_resp.get("data") or {}

        plan = sub.get("plan") or {}
        tier = plan.get("tier")

        return UsageInfo(
            provider=self.name,
            membership_level=str(tier).capitalize() if tier else None,
            limits=self._parse_quota_limits(sub),
            balances=self._parse_balances(sub, bal),
            raw_response=raw_data,
        )
