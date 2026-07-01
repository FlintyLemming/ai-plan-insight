from datetime import datetime, timedelta

from fastapi.testclient import TestClient

import ai_plan_insight.web as web


VALID_PAYLOAD = {
    "seven_day": {
        "utilization": 45.2,
        "resets_at": "2026-07-08T12:00:00Z",
    },
    "five_hour": {
        "utilization": 12.8,
        "resets_at": "2026-07-01T15:00:00Z",
    },
}


def _reset_push_state():
    web._pushed_results.clear()
    web._pushed_at.clear()


def test_push_claude_returns_ok():
    _reset_push_state()
    client = TestClient(web.app)

    resp = client.post("/api/push/claude", json=VALID_PAYLOAD)

    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_usage_returns_two_claude_limits_after_push():
    _reset_push_state()
    client = TestClient(web.app)

    client.post("/api/push/claude", json=VALID_PAYLOAD)
    resp = client.get("/api/usage")

    assert resp.status_code == 200
    providers = [u for u in resp.json() if u["provider"] == "Claude 订阅"]
    assert len(providers) == 1
    limits = providers[0]["limits"]
    assert len(limits) == 2

    five = limits[0]
    assert five["duration"] == 5
    assert five["time_unit"] == "小时"
    assert five["limit"] == "100"
    # used = str(int(12.8)) = str(12) = "12"（截断，非四舍五入；设计文档叙述里的 "13" 是笔误）
    assert five["used"] == "12"
    assert five["remaining"] == "87"
    assert five["reset_time"] == "2026-07-01T15:00:00Z"
    assert five["limit_type"] == ""

    seven = limits[1]
    assert seven["duration"] == 7
    assert seven["time_unit"] == "天"
    assert seven["limit"] == "100"
    assert seven["used"] == "45"
    # remaining = str(int(100 - 45.2)) = str(int(54.8)) = "54"（沿用 Antigravity 的截断写法）
    # 注意：设计文档测试叙述里写的 "55" 是笔误，与设计给出的实现代码矛盾，此处以实现为准。
    assert seven["remaining"] == "54"
    assert seven["reset_time"] == "2026-07-08T12:00:00Z"
    assert seven["limit_type"] == ""


def test_expired_push_is_not_returned():
    _reset_push_state()
    client = TestClient(web.app)

    client.post("/api/push/claude", json=VALID_PAYLOAD)
    # 手动把推送时间改为 31 分钟前，模拟 TTL 过期
    web._pushed_at["claude"] = datetime.now().astimezone() - timedelta(minutes=31)

    resp = client.get("/api/usage")

    assert resp.status_code == 200
    providers = [u for u in resp.json() if u["provider"] == "Claude 订阅"]
    assert providers == []
