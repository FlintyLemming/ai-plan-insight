import httpx
import hmac
import hashlib
import base64
import uuid
from urllib.parse import quote_plus
from datetime import datetime, timezone
from ..models import UsageInfo
from ..config import ProviderConfig
from .base import BaseProvider


class AlibabaCloudProvider(BaseProvider):
    """阿里云AI代金券余额 provider.

    Queries the Aliyun BSS OpenAPI for savings plan balance (RestPoolValue).
    """

    ENDPOINT = "https://bssopenapi.aliyuncs.com"

    @property
    def name(self) -> str:
        return "阿里云AI代金券"

    def authenticate(self) -> None:
        self._access_key_id = self.config.access_key_id or ""
        self._access_key_secret = self.config.access_key_secret or ""

    def _sign_string(self, string_to_sign: str) -> str:
        h = hmac.new(
            (self._access_key_secret + "&").encode("utf-8"),
            string_to_sign.encode("utf-8"),
            hashlib.sha1,
        )
        return base64.b64encode(h.digest()).decode("utf-8")

    def _build_signed_params(self) -> dict:
        params = {
            "Format": "JSON",
            "Version": "2017-12-14",
            "AccessKeyId": self._access_key_id,
            "SignatureMethod": "HMAC-SHA1",
            "SignatureNonce": str(uuid.uuid4()),
            "SignatureVersion": "1.0",
            "Timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "Action": "QuerySavingsPlansInstance",
            "RegionId": "cn-hangzhou",
            "PageSize": "20",
            "PageNum": "1",
            "Locale": "ZH",
        }

        sorted_params = sorted(params.items())
        canonicalized_query = "&".join(
            f"{quote_plus(str(k))}={quote_plus(str(v))}" for k, v in sorted_params
        )
        string_to_sign = f"GET&{quote_plus('/')}&{quote_plus(canonicalized_query)}"
        params["Signature"] = self._sign_string(string_to_sign)

        return params

    async def fetch_usage(self) -> dict:
        params = self._build_signed_params()
        async with httpx.AsyncClient() as client:
            response = await client.get(self.ENDPOINT, params=params, timeout=30)
            response.raise_for_status()
            return response.json()

    def parse_usage(self, raw_data: dict) -> UsageInfo:
        items = raw_data.get("Data", {}).get("Items", [])
        balances: dict[str, str] = {}
        raw_rest = ""

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
