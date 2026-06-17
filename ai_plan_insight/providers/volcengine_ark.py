import httpx
from datetime import datetime, timezone

from ..models import UsageInfo, LimitDetail
from .base import BaseProvider
from .volcengine_signing import sign_request


class VolcEngineArkProvider(BaseProvider):
    """火山方舟 Coding Plan 用量 provider.

    调用管控面 API GetCodingPlanUsage（AK/SK HMAC-SHA256 签名鉴权），
    返回 session(5h)/weekly/monthly 三窗口的已用百分比与重置时间。
    """

    HOST = "ark.cn-beijing.volcengineapi.com"
    REGION = "cn-beijing"
    SERVICE = "ark"
    ACTION = "GetCodingPlanUsage"
    VERSION = "2024-01-01"

    # QuotaUsage.Level -> (展示标签, duration)
    _LEVEL_MAP = {
        "session": ("5 小时", 1),
        "weekly": ("周", 1),
        "monthly": ("月", 1),
    }

    @property
    def name(self) -> str:
        return "火山方舟 Coding Plan"

    def authenticate(self) -> None:
        # AK/SK 在请求时按 body 动态签名，这里只做存在性校验。
        if not self.config.access_key_id or not self.config.access_key_secret:
            raise ValueError("volcengine_ark 需要配置 access_key_id 和 access_key_secret")
        self._ak = self.config.access_key_id
        self._sk = self.config.access_key_secret

    async def fetch_usage(self) -> dict:
        body = "{}"
        headers = sign_request(
            host=self.HOST,
            method="POST",
            path="/",
            query={"Action": self.ACTION, "Version": self.VERSION},
            body=body,
            ak=self._ak,
            sk=self._sk,
            region=self.REGION,
            service=self.SERVICE,
        )
        url = f"https://{self.HOST}/?Action={self.ACTION}&Version={self.VERSION}"
        self.log.info("Request: POST %s", url)
        async with httpx.AsyncClient() as client:
            response = await client.post(url, headers=headers, content=body)
            self.log.info("Response: %d %s", response.status_code, response.reason_phrase)
            self.log.debug("Response body: %s", response.text)
            response.raise_for_status()
            return response.json()

    @staticmethod
    def _parse_reset_time(ts: int | None) -> datetime | None:
        # GetCodingPlanUsage 的 ResetTimestamp 是 epoch 秒（非毫秒）。
        if not ts:
            return None
        try:
            return datetime.fromtimestamp(int(ts), tz=timezone.utc)
        except (ValueError, OSError, OverflowError):
            return None

    def parse_usage(self, raw_data: dict) -> UsageInfo:
        result = raw_data.get("Result", {}) if isinstance(raw_data, dict) else {}
        status = result.get("Status") or None

        limits: list[LimitDetail] = []
        for item in result.get("QuotaUsage", []):
            if not isinstance(item, dict):
                continue
            level = item.get("Level")
            mapped = self._LEVEL_MAP.get(level)
            if mapped is None:
                continue
            label, duration = mapped

            try:
                used_pct = float(item.get("Percent", 0) or 0)
            except (TypeError, ValueError):
                used_pct = 0.0
            remaining_pct = max(100.0 - used_pct, 0.0)
            reset_time = self._parse_reset_time(item.get("ResetTimestamp"))

            limits.append(
                LimitDetail(
                    duration=duration,
                    time_unit=label,
                    limit="100",
                    used=f"{used_pct:.2f}",
                    remaining=f"{remaining_pct:.2f}",
                    reset_time=reset_time,
                    limit_type="PERCENT",
                )
            )

        return UsageInfo(
            provider=self.name,
            membership_level=status,
            limits=limits,
            raw_response=raw_data,
        )
