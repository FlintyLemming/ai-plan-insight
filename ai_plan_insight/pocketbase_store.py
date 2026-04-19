"""Store GLM Coding Plan usage data into PocketBase every 10 seconds."""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

import httpx

from .config import Config
from .config_loader import load_config
from .providers.bigmodel import BigModelProvider

logger = logging.getLogger(__name__)

PB_STORE_INTERVAL = 10  # seconds


class PocketBaseStore:
    """Handles storing BigModel/GLM usage data to PocketBase."""

    def __init__(self, config: Config):
        self.pb_config = config.pocketbase
        self.bigmodel_config = config.providers.get("bigmodel")
        self._token: str | None = None
        self._client = httpx.AsyncClient(timeout=30)

    async def close(self):
        await self._client.aclose()

    def _pb_url(self, path: str) -> str:
        base = (self.pb_config.url if self.pb_config else "").rstrip("/")
        return f"{base}{path}"

    async def _auth(self) -> str:
        if self._token:
            return self._token
        if not self.pb_config:
            raise RuntimeError("PocketBase config missing")
        resp = await self._client.post(
            self._pb_url("/api/collections/_superusers/auth-with-password"),
            json={
                "identity": self.pb_config.email,
                "password": self.pb_config.password,
            },
        )
        resp.raise_for_status()
        data = resp.json()
        self._token = data["token"]
        return self._token

    async def _pb_request(
        self, method: str, path: str, json_data: dict | None = None
    ) -> dict:
        token = await self._auth()
        headers = {"Authorization": token}
        if json_data is not None:
            headers["Content-Type"] = "application/json"
        resp = await self._client.request(
            method, self._pb_url(path), headers=headers, json=json_data
        )
        if resp.status_code == 401:
            self._token = None
            token = await self._auth()
            headers["Authorization"] = token
            resp = await self._client.request(
                method, self._pb_url(path), headers=headers, json=json_data
            )
        resp.raise_for_status()
        return resp.json() if resp.content else {}

    @staticmethod
    def _unit_name(unit: int) -> str:
        return {
            1: "second",
            2: "minute",
            3: "hour",
            4: "day",
            5: "month",
            6: "year",
        }.get(unit, f"unit_{unit}")

    @staticmethod
    def _format_pb_date(dt: datetime | None) -> str | None:
        if not dt:
            return None
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.000Z")

    async def _create_snapshot(
        self,
        membership_level: str | None,
        raw_quota: dict,
        raw_model_usage: dict,
    ) -> str:
        payload: dict = {
            "provider": "GLM Coding Plan",
            "raw_quota_response": raw_quota,
            "raw_model_usage_response": raw_model_usage,
        }
        if membership_level:
            payload["membership_level"] = membership_level
        result = await self._pb_request(
            "POST", "/api/collections/glm_usage_snapshots/records", payload
        )
        return result["id"]

    async def _create_limits(self, snapshot_id: str, limits: list[dict]):
        for limit in limits:
            unit = limit.get("unit", 0)
            number = limit.get("number", 1)
            limit_type = limit.get("type", "")
            reset_ts = limit.get("nextResetTime")
            reset_time = None
            if reset_ts:
                reset_time = datetime.fromtimestamp(reset_ts / 1000, tz=timezone.utc)

            if limit_type == "TIME_LIMIT":
                usage_limit = limit.get("usage", 0)
                current_value = limit.get("currentValue", 0)
                remaining = limit.get("remaining", 0)
            else:
                usage_limit = None
                current_value = None
                remaining = 100 - limit.get("percentage", 0)

            payload = {
                "snapshot": snapshot_id,
                "limit_type": limit_type,
                "time_unit": self._unit_name(unit),
                "duration": number,
                "percentage": limit.get("percentage", 0),
                "usage_limit": usage_limit,
                "current_value": current_value,
                "remaining": remaining,
                "next_reset_time": self._format_pb_date(reset_time),
            }
            result = await self._pb_request(
                "POST", "/api/collections/glm_usage_limits/records", payload
            )

            # Create model details for TIME_LIMIT
            if limit_type == "TIME_LIMIT":
                for detail in limit.get("usageDetails", []):
                    await self._pb_request(
                        "POST",
                        "/api/collections/glm_limit_model_details/records",
                        {
                            "usage_limit": result["id"],
                            "model_code": detail.get("modelCode", ""),
                            "usage": detail.get("usage", 0) or 0,
                        },
                    )

    async def _create_model_usage_totals(
        self, snapshot_id: str, total_usage: dict
    ):
        await self._pb_request(
            "POST",
            "/api/collections/glm_model_usage_totals/records",
            {
                "snapshot": snapshot_id,
                "total_model_call_count": total_usage.get("totalModelCallCount", 0),
                "total_tokens_usage": total_usage.get("totalTokensUsage", 0),
            },
        )

    async def _create_model_summaries(
        self, snapshot_id: str, model_summary_list: list[dict]
    ):
        for item in model_summary_list:
            await self._pb_request(
                "POST",
                "/api/collections/glm_model_summary/records",
                {
                    "snapshot": snapshot_id,
                    "model_name": item.get("modelName", ""),
                    "total_tokens": item.get("totalTokens", 0),
                    "sort_order": item.get("sortOrder"),
                },
            )

    async def _create_hourly_usage(
        self, snapshot_id: str, data: dict
    ):
        await self._pb_request(
            "POST",
            "/api/collections/glm_hourly_usage/records",
            {
                "snapshot": snapshot_id,
                "x_time": data.get("x_time", []),
                "model_call_count": data.get("modelCallCount", []),
                "tokens_usage": data.get("tokensUsage", []),
                "granularity": data.get("granularity", "hourly"),
            },
        )

    async def _create_hourly_model_usage(
        self, snapshot_id: str, model_data_list: list[dict]
    ):
        for item in model_data_list:
            await self._pb_request(
                "POST",
                "/api/collections/glm_hourly_model_usage/records",
                {
                    "snapshot": snapshot_id,
                    "model_name": item.get("modelName", ""),
                    "sort_order": item.get("sortOrder"),
                    "tokens_usage": item.get("tokensUsage", []),
                },
            )

    async def store(self):
        if not self.pb_config or not self.bigmodel_config:
            logger.warning("PocketBase or BigModel config missing, skipping store")
            return

        try:
            provider = BigModelProvider(self.bigmodel_config)
            provider.authenticate()

            # Fetch quota/limit
            raw_quota = await provider.fetch_usage()
            quota_data = raw_quota.get("data", {})
            membership_level = quota_data.get("level")
            limits = quota_data.get("limits", [])

            # Fetch model usage for last 30 days (full hourly data)
            now = datetime.now(timezone(timedelta(hours=8)))
            start = now - timedelta(days=30)
            async with httpx.AsyncClient() as client:
                raw_model_usage = await provider._fetch_model_usage(client, start, now)
            if not raw_model_usage:
                raw_model_usage = {"code": 500, "msg": "fetch failed", "data": {}}

            model_data = raw_model_usage.get("data", {})
            total_usage = model_data.get("totalUsage", {})
            model_summary_list = model_data.get("modelSummaryList", [])
            model_data_list = model_data.get("modelDataList", [])

            # Create snapshot and all related records
            snapshot_id = await self._create_snapshot(
                membership_level, raw_quota, raw_model_usage
            )

            await asyncio.gather(
                self._create_limits(snapshot_id, limits),
                self._create_model_usage_totals(snapshot_id, total_usage),
                self._create_model_summaries(snapshot_id, model_summary_list),
            )

            # Hourly data can be large; create sequentially to avoid overload
            await self._create_hourly_usage(snapshot_id, model_data)
            await self._create_hourly_model_usage(snapshot_id, model_data_list)

            logger.info("Stored GLM usage snapshot %s to PocketBase", snapshot_id)

        except Exception:
            logger.exception("Failed to store GLM usage to PocketBase")


async def background_store_glm():
    """Background task: fetch and store GLM data every 10 seconds."""
    config = load_config()
    store = PocketBaseStore(config)
    try:
        while True:
            await store.store()
            await asyncio.sleep(PB_STORE_INTERVAL)
    finally:
        await store.close()
