import json
import subprocess
from typing import Any

from ..models import UsageInfo
from ..config import ProviderConfig
from .base import BaseProvider


class AlibabaCloudProvider(BaseProvider):
    """阿里云AI代金券余额 provider（通过 aliyun CLI）。"""

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
        result = subprocess.run(
            [
                "aliyun",
                "bssopenapi",
                "QuerySavingsPlansInstance",
                "--PageSize",
                "1",
                "--PageNum",
                "1",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            raise RuntimeError(f"aliyun CLI failed: {result.stderr}")
        return json.loads(result.stdout)

    def parse_usage(self, raw_data: dict) -> UsageInfo:
        items = raw_data.get("Data", {}).get("Items", [])
        balances: dict[str, str] = {}

        if items:
            item = items[0]
            raw_rest = item.get("RestPoolValue", "")
            cleaned = raw_rest.replace("¥", "").replace("CNY", "").strip()
            if cleaned:
                balances["balance"] = cleaned
            if "InstanceId" in item:
                balances["instance_id"] = item["InstanceId"]
            if "Status" in item:
                balances["status"] = item["Status"]
            if "Utilization" in item:
                balances["utilization"] = str(item["Utilization"])
            if "PoolValue" in item:
                balances["total"] = str(item["PoolValue"])
            if "EndTime" in item:
                balances["end_time"] = item["EndTime"]

        return UsageInfo(
            provider=self.name,
            balances=balances,
            raw_response=raw_data,
        )
