import asyncio
import re

import httpx

from ..models import UsageInfo, LimitDetail
from .base import BaseProvider

PLATFORM_BASE_URL = "https://platform.xiaomimimo.com/api/v1"


class MimoCookieExpiredError(Exception):
    """Raised when platform cookie is rejected (typically HTTP 401)."""


class MimoTokenPlanProvider(BaseProvider):
    """小米 MiMo Token Plan 用量 provider.

    调用 platform.xiaomimimo.com 的 Web API（需浏览器登录 Cookie），
    参考 CLIProxyPoolWidget Shared/PoolAPIClient.swift。
    """

    @property
    def name(self) -> str:
        return "小米 MiMo Token Plan"

    def authenticate(self) -> None:
        cookie = self.config.cookie.strip()
        if not cookie:
            raise ValueError("mimo_token_plan 需要配置 cookie")
        self._headers = {
            "Accept": "application/json,*/*",
            "Accept-Language": "zh",
            "Content-Type": "application/json",
            "Cookie": cookie,
            "Referer": "https://platform.xiaomimimo.com/console/plan-manage",
            "X-Timezone": "Asia/Shanghai",
            "User-Agent": "ai-plan-insight/1.0",
        }

    async def fetch_usage(self) -> dict:
        usage, detail = await asyncio.gather(
            self._fetch_json("/tokenPlan/usage"),
            self._fetch_json("/tokenPlan/detail"),
        )
        return {"usage": usage, "detail": detail}

    async def _fetch_json(self, path: str) -> dict:
        url = f"{PLATFORM_BASE_URL}{path}"
        self.log.info("Request: GET %s", url)
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url, headers=self._headers)
            self.log.info("Response: %d %s", response.status_code, response.reason_phrase)
            self.log.debug("Response body: %s", response.text)
            if response.status_code == 401:
                raise MimoCookieExpiredError(
                    "Cookie 已过期，请重新登录 https://platform.xiaomimimo.com "
                    "并在 config 中更新 mimo_token_plan.cookie"
                )
            response.raise_for_status()
            return response.json()

    @staticmethod
    def _format_m(value: float) -> str:
        return f"{value / 1_000_000:.2f}M"

    @staticmethod
    def _item(bucket: dict | None, name: str) -> dict | None:
        if not isinstance(bucket, dict):
            return None
        items = bucket.get("items") or []
        for item in items:
            if isinstance(item, dict) and item.get("name") == name:
                return item
        return items[0] if items and isinstance(items[0], dict) else None

    @staticmethod
    def _limit_from_item(label: str, item: dict) -> LimitDetail:
        used = float(item.get("used", 0))
        limit = float(item.get("limit", 0))
        pct = (used / limit * 100) if limit > 0 else 0.0
        return LimitDetail(
            duration=1,
            time_unit=f"{label} · {MimoTokenPlanProvider._format_m(used)} / {MimoTokenPlanProvider._format_m(limit)}",
            limit="100",
            used=f"{pct:.2f}",
            remaining=f"{100 - pct:.2f}",
            limit_type="PERCENT",
        )

    @staticmethod
    def _user_id_from_cookie(cookie: str) -> str | None:
        match = re.search(r"(?:^|;\s*)userId=(\d+)", cookie)
        return match.group(1) if match else None

    def parse_usage(self, raw_data: dict) -> UsageInfo:
        usage_root = (raw_data.get("usage") or {}).get("data") or {}
        detail_root = (raw_data.get("detail") or {}).get("data") or {}

        limits: list[LimitDetail] = []

        plan_item = self._item(usage_root.get("usage"), "plan_total_token")
        if plan_item:
            limits.append(self._limit_from_item("套餐周期", plan_item))

        compensation_item = self._item(usage_root.get("usage"), "compensation_total_token")
        if compensation_item and float(compensation_item.get("used", 0)) > 0:
            limits.append(self._limit_from_item("补偿额度", compensation_item))

        balances: dict[str, str] = {}
        if detail_root.get("currentPeriodEnd"):
            balances["有效期至"] = str(detail_root["currentPeriodEnd"])
        if detail_root.get("expired"):
            balances["状态"] = "已过期"

        return UsageInfo(
            provider=self.name,
            user_id=self._user_id_from_cookie(self.config.cookie),
            membership_level=detail_root.get("planName") or detail_root.get("planCode"),
            limits=limits,
            balances=balances,
            raw_response=raw_data,
        )
