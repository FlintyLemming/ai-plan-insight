import hashlib
import hmac
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import quote, urlsplit

import httpx

from ..models import UsageInfo
from .base import BaseProvider


class HuaweiCloudBssProvider(BaseProvider):
    """Huawei Cloud BSS account balance provider."""

    DEFAULT_BASE_URL = "https://bss.myhuaweicloud.com"
    BALANCE_PATH = "/v2/accounts/customer-accounts/balances"
    ALGORITHM = "SDK-HMAC-SHA256"

    ACCOUNT_TYPE_LABELS = {
        1: "余额",
        2: "信用额度",
        5: "奖励金",
        7: "保证金",
    }

    @property
    def name(self) -> str:
        return "华为云余额"

    @staticmethod
    def _is_placeholder(value: str) -> bool:
        normalized = value.strip().lower()
        if not normalized:
            return True
        return normalized.startswith(("your_", "put_")) or set(normalized) <= {"x"}

    def authenticate(self) -> None:
        access_key_id = self.config.access_key_id.strip()
        access_key_secret = self.config.access_key_secret.strip()
        if self._is_placeholder(access_key_id) or self._is_placeholder(access_key_secret):
            raise ValueError("Huawei Cloud BSS requires access_key_id and access_key_secret")

    @staticmethod
    def _sha256_hex(value: bytes) -> str:
        return hashlib.sha256(value).hexdigest()

    @staticmethod
    def _canonical_uri(path: str) -> str:
        quoted = quote(path or "/", safe="/~")
        return quoted if quoted.endswith("/") else f"{quoted}/"

    @staticmethod
    def _format_amount(raw_value: Any) -> str | None:
        if raw_value is None:
            return None

        try:
            value = Decimal(str(raw_value))
        except (InvalidOperation, ValueError):
            text = str(raw_value).strip()
            return text or None

        return format(value.quantize(Decimal("0.01")), "f")

    @staticmethod
    def _is_zero(raw_value: Any) -> bool:
        try:
            return Decimal(str(raw_value)) == 0
        except (InvalidOperation, ValueError):
            return False

    def _build_signed_headers(self, method: str, url: str, body: bytes = b"") -> dict[str, str]:
        parsed = urlsplit(url)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        headers = {
            "content-type": "application/json",
            "host": parsed.netloc,
            "x-sdk-date": timestamp,
        }

        canonical_headers = "".join(
            f"{key}:{value.strip()}\n" for key, value in sorted(headers.items())
        )
        signed_headers = ";".join(sorted(headers))
        canonical_request = "\n".join(
            [
                method.upper(),
                self._canonical_uri(parsed.path),
                parsed.query,
                canonical_headers,
                signed_headers,
                self._sha256_hex(body),
            ]
        )
        string_to_sign = "\n".join(
            [
                self.ALGORITHM,
                timestamp,
                self._sha256_hex(canonical_request.encode("utf-8")),
            ]
        )
        signature = hmac.new(
            self.config.access_key_secret.strip().encode("utf-8"),
            string_to_sign.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        return {
            "Content-Type": headers["content-type"],
            "Host": headers["host"],
            "X-Sdk-Date": headers["x-sdk-date"],
            "Authorization": (
                f"{self.ALGORITHM} Access={self.config.access_key_id.strip()}, "
                f"SignedHeaders={signed_headers}, Signature={signature}"
            ),
        }

    def _balance_url(self) -> str:
        base_url = (self.config.base_url or self.DEFAULT_BASE_URL).rstrip("/")
        return f"{base_url}{self.BALANCE_PATH}"

    async def fetch_usage(self) -> dict:
        url = self._balance_url()
        headers = self._build_signed_headers("GET", url)
        self.log.info("Request: GET %s", url)

        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(url, headers=headers)

        self.log.info("Response: %d %s", response.status_code, response.reason_phrase)
        self.log.debug("Response body: %s", response.text)
        response.raise_for_status()
        return response.json()

    def parse_usage(self, raw_data: dict) -> UsageInfo:
        balances: dict[str, str] = {}

        for item in raw_data.get("account_balances", []):
            account_type = item.get("account_type")
            label = self.ACCOUNT_TYPE_LABELS.get(account_type, f"账户{account_type}余额")
            amount = self._format_amount(item.get("amount"))
            if amount is not None:
                balances[label] = amount

            if account_type == 2:
                credit_amount = self._format_amount(item.get("credit_amount"))
                if credit_amount is not None and credit_amount != amount:
                    balances["总信用额度"] = credit_amount

        debt_amount = raw_data.get("debt_amount")
        if debt_amount is not None and not self._is_zero(debt_amount):
            formatted_debt = self._format_amount(debt_amount)
            if formatted_debt is not None:
                balances["欠款"] = formatted_debt

        currency = raw_data.get("currency")
        if currency and currency != "CNY":
            balances["币种"] = str(currency)

        return UsageInfo(
            provider=self.name,
            user_id=self.config.user_name or None,
            balances=balances,
            raw_response=raw_data,
        )
