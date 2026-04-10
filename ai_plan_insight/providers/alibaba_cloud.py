import json
import subprocess
from datetime import datetime
from typing import Any

from ..models import UsageInfo
from ..config import ProviderConfig
from .base import BaseProvider


class AlibabaCloudProvider(BaseProvider):
    """阿里云AI代金券余额 provider（通过 aliyun CLI）。"""

    @staticmethod
    def _parse_end_time(raw_end_time: Any) -> datetime | None:
        if not raw_end_time:
            return None

        end_time = str(raw_end_time).strip()
        if not end_time:
            return None

        normalized = end_time.replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(normalized)
        except ValueError:
            return None

    @staticmethod
    def _format_balance(raw_value: Any) -> str | None:
        if raw_value is None:
            return None

        cleaned = str(raw_value).replace("¥", "").replace("CNY", "").strip()
        return cleaned or None

    @staticmethod
    def _format_end_time(raw_end_time: Any) -> str | None:
        parsed = AlibabaCloudProvider._parse_end_time(raw_end_time)
        if parsed is None:
            if raw_end_time is None:
                return None
            text = str(raw_end_time).strip()
            return text or None
        return parsed.date().isoformat()

    @staticmethod
    def _format_reset_date(raw_end_time: Any) -> str:
        parsed = AlibabaCloudProvider._parse_end_time(raw_end_time)
        if parsed is None:
            return "每月重置"
        return f"每月{parsed.day}日"

    @property
    def name(self) -> str:
        return "阿里云AI代金券"

    def authenticate(self) -> None:
        """配置 aliyun CLI 的访问凭证。"""
        access_key_id = self.config.access_key_id or ""
        access_key_secret = self.config.access_key_secret or ""
        subprocess.run(
            [
                "aliyun",
                "configure",
                "set",
                "--mode",
                "AK",
                "--access-key-id",
                access_key_id,
                "--access-key-secret",
                access_key_secret,
                "--region",
                "cn-hangzhou",
                "--profile",
                "default",
            ],
            capture_output=True,
            timeout=10,
        )

    async def fetch_usage(self) -> dict:
        """通过 aliyun CLI 调用 BSS OpenAPI。"""
        cmd = [
            "aliyun",
            "bssopenapi",
            "QuerySavingsPlansInstance",
            "--PageSize",
            "1",
            "--PageNum",
            "1",
        ]
        self.log.info("Request: aliyun CLI %s", " ".join(cmd[1:]))
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            self.log.error("Response: CLI failed (rc=%d): %s", result.returncode, result.stderr)
            raise RuntimeError(f"aliyun CLI failed: {result.stderr}")
        self.log.info("Response: CLI success")
        self.log.debug("Response body: %s", result.stdout)
        return json.loads(result.stdout)

    def parse_usage(self, raw_data: dict) -> UsageInfo:
        items = raw_data.get("Data", {}).get("Items", [])
        balances: dict[str, str] = {}

        if items:
            item = items[0]
            balance = self._format_balance(item.get("RestPoolValue"))
            if balance:
                balances["余额"] = balance

            end_time = self._format_end_time(item.get("EndTime"))
            if end_time:
                balances["套餐结束日期"] = end_time

            balances["余额重置日期"] = self._format_reset_date(item.get("EndTime"))

        return UsageInfo(
            provider=self.name,
            balances=balances,
            raw_response=raw_data,
        )
