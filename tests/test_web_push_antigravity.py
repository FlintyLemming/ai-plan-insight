from fastapi.testclient import TestClient

import ai_plan_insight.web as web

VALID_PAYLOAD = {
    "plan": "Google AI Ultra",
    "windows": [
        {"group": "Claude", "window": "5h", "utilization": 25,
         "resets_at": "2026-11-25T11:21:31Z"},
        {"group": "Claude", "window": "weekly", "utilization": 10,
         "resets_at": "2026-12-02T06:21:31Z"},
        {"group": "Gemini", "window": "5h", "utilization": 0,
         "resets_at": "2026-11-25T11:21:45Z"},
    ],
}


def _client(tmp_path, mode="push"):
    from config_fixtures import seed_config
    db = seed_config(
        tmp_path / "usage.db",
        providers={
            "antigravity": {
                "type": "antigravity", "mode": mode, "label": "主账号", "order": 1,
                **({"api_key": "k", "base_url": "http://127.0.0.1:1"} if mode == "fetch" else {}),
            },
        },
        push_auth_secret="abc",
        enforce_push_auth=True,
    )
    web._config_path = str(tmp_path / "config.json")
    web._usage_db_path = db
    web._v2_manager = None
    web._config_service = None
    return TestClient(web.app)


def _reset():
    web._v2_manager = None
    web._config_service = None


def test_push_antigravity_appears_in_usage_v2(tmp_path):
    with _client(tmp_path) as c:
        resp = c.post("/api/push/v2/antigravity", json=VALID_PAYLOAD,
                      headers={"Authorization": "Bearer abc"})
        assert resp.status_code == 200
        cards = c.get("/api/usage/v2").json()
        card = next(x for x in cards if x["instance_id"] == "antigravity")
        assert card["membership_level"] == "Google AI Ultra"
        assert [l["time_unit"] for l in card["limits"]] == [
            "Claude 模型 · 5小时", "Claude 模型 · 周", "Gemini 模型 · 5小时",
        ]
        assert card["limits"][0]["used"] == "25"
        assert card["error"] is None
    _reset()


def test_push_antigravity_error_card(tmp_path):
    with _client(tmp_path) as c:
        resp = c.post("/api/push/v2/antigravity", json={"error": "CPA 凭证失效"},
                      headers={"Authorization": "Bearer abc"})
        assert resp.status_code == 200
        card = next(x for x in c.get("/api/usage/v2").json()
                    if x["instance_id"] == "antigravity")
        assert card["error"] == "CPA 凭证失效"
    _reset()


def test_push_antigravity_legacy_payload_rejected(tmp_path):
    with _client(tmp_path) as c:
        resp = c.post(
            "/api/push/v2/antigravity",
            json={"gemini_3_1_pro_percentage": 5.0,
                  "gemini_3_1_pro_reset_time": "2026-04-15T00:00:00Z"},
            headers={"Authorization": "Bearer abc"},
        )
        assert resp.status_code == 422
    _reset()


def test_push_antigravity_to_fetch_instance_rejected(tmp_path):
    with _client(tmp_path, mode="fetch") as c:
        resp = c.post("/api/push/v2/antigravity", json=VALID_PAYLOAD,
                      headers={"Authorization": "Bearer abc"})
        assert resp.status_code == 422
    _reset()
