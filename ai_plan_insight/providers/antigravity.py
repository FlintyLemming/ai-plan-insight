import httpx
from datetime import datetime
from ..models import UsageInfo, LimitDetail
from .base import BaseProvider


class AntigravityProvider(BaseProvider):
    """Antigravity Tools (Antigravity-Manager) usage provider.

    Talks to the local/remote manager HTTP API exposed by Antigravity Tools.
    Endpoint reference from Antigravity-Manager (src-tauri/src/modules/http_api.rs):

    - GET {base_url}/api/accounts
      Returns:
      {
        "accounts": [
          {
            "id": "...",
            "email": "...",
            "is_current": true,
            "disabled": false,
            "quota": {
              "models": [
                {"name": "gemini-...", "percentage": 85, "reset_time": "2025-...T...Z"}
              ],
              "subscription_tier": "ULTRA"
            }
          }
        ]
      }

    Authentication (src-tauri/src/proxy/middleware/auth.rs):
    - Management endpoints require the configured admin_password.
    - If admin_password is not set, the proxy api_key is accepted as fallback.
    - Send the token as `Authorization: Bearer <token>` or `x-api-key: <token>`.
    """

    ACCOUNTS_ENDPOINT = "/api/accounts"

    @property
    def name(self) -> str:
        return "Antigravity"

    def authenticate(self) -> None:
        # Prefer admin_password for management endpoints; fall back to api_key.
        token = self.config.admin_password or self.config.api_key
        self._headers = {
            "Authorization": f"Bearer {token}",
            "x-api-key": token,
            "Content-Type": "application/json",
        }

    async def fetch_usage(self) -> dict:
        base = self.config.base_url.rstrip("/")
        url = f"{base}{self.ACCOUNTS_ENDPOINT}"

        self.log.info("Request: GET %s", url)
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url, headers=self._headers)
            self.log.info("Response: %d %s", response.status_code, response.reason_phrase)
            self.log.debug("Response body: %s", response.text)
            response.raise_for_status()
            return response.json()

    @staticmethod
    def _parse_reset_time(reset_time_str: str | None) -> datetime | None:
        if not reset_time_str:
            return None
        try:
            return datetime.fromisoformat(reset_time_str.replace("Z", "+00:00"))
        except ValueError:
            return None

    def parse_usage(self, raw_data: dict) -> UsageInfo:
        accounts = raw_data.get("accounts", [])

        # Collect per-series entries across all accounts.
        # Series -> list of (model_name, percentage, reset_time)
        series_buckets: dict[str, list[tuple[str, int, datetime | None]]] = {
            "Gemini": [],
            "Claude": [],
        }
        membership_levels: set[str] = set()

        for account in accounts:
            if not isinstance(account, dict):
                continue
            if account.get("disabled"):
                continue

            quota = account.get("quota")
            if not isinstance(quota, dict):
                continue

            if quota.get("subscription_tier"):
                membership_levels.add(quota["subscription_tier"])

            for model in quota.get("models", []):
                if not isinstance(model, dict):
                    continue

                name = model.get("name", "")
                name_lower = name.lower()
                series: str | None = None
                if name_lower.startswith("gemini"):
                    series = "Gemini"
                elif name_lower.startswith("claude"):
                    series = "Claude"

                if series is None:
                    continue

                percentage = model.get("percentage")
                if percentage is None:
                    continue

                reset_time = self._parse_reset_time(model.get("reset_time"))
                series_buckets[series].append((name, int(percentage), reset_time))

        limits: list[LimitDetail] = []
        for series, entries in series_buckets.items():
            if not entries:
                continue

            # Headline: lowest remaining percentage across all accounts/models.
            min_pct = min(pct for _, pct, _ in entries)
            # Earliest reset time among the entries.
            earliest_reset = min(
                (rt for _, _, rt in entries if rt is not None),
                default=None,
            )

            limits.append(
                LimitDetail(
                    duration=1,
                    time_unit=series,
                    limit="100",
                    used=str(100 - min_pct),
                    remaining=str(min_pct),
                    reset_time=earliest_reset,
                    limit_type="PERCENT",
                    usage_details=[]
                )
            )

        # Parse quota_groups for weekly usage buckets.
        # group_display_name -> list of (remaining_fraction, reset_time)
        weekly_buckets: dict[str, list[tuple[float, datetime | None]]] = {}

        for account in accounts:
            if not isinstance(account, dict):
                continue
            if account.get("disabled"):
                continue

            quota = account.get("quota")
            if not isinstance(quota, dict):
                continue

            for group in quota.get("quota_groups", []):
                if not isinstance(group, dict):
                    continue
                group_name = group.get("display_name", "")
                for bucket in group.get("buckets", []):
                    if not isinstance(bucket, dict):
                        continue
                    if bucket.get("window") != "weekly":
                        continue
                    remaining = bucket.get("remaining_fraction")
                    if remaining is None:
                        continue
                    reset_time = self._parse_reset_time(bucket.get("reset_time"))
                    weekly_buckets.setdefault(group_name, []).append(
                        (float(remaining), reset_time)
                    )

        for group_name, entries in weekly_buckets.items():
            min_remaining = min(fr for fr, _ in entries)
            earliest_reset = min(
                (rt for _, rt in entries if rt is not None),
                default=None,
            )
            used_pct = (1 - min_remaining) * 100

            limits.append(
                LimitDetail(
                    duration=1,
                    time_unit=group_name,
                    limit="100",
                    used=f"{used_pct:.2f}",
                    remaining=f"{min_remaining * 100:.2f}",
                    reset_time=earliest_reset,
                    limit_type="PERCENT",
                    usage_details=[],
                )
            )

        return UsageInfo(
            provider=self.name,
            membership_level=" / ".join(sorted(membership_levels)) if membership_levels else None,
            limits=limits,
            raw_response=raw_data,
        )
