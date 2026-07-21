import sqlite3

import pytest
from ai_plan_insight.api_schemas import UsagePoint, UsageReportRequest


def test_usage_point_accepts_valid_date_and_nonnegative_tokens():
    p = UsagePoint(date="2026-07-02", model_id="glm-5.2", input_tokens=10, output_tokens=0)
    assert p.input_tokens == 10


def test_usage_point_rejects_negative_input_tokens():
    with pytest.raises(Exception):
        UsagePoint(date="2026-07-02", model_id="glm-5.2", input_tokens=-1, output_tokens=0)


def test_usage_point_rejects_negative_output_tokens():
    with pytest.raises(Exception):
        UsagePoint(date="2026-07-02", model_id="glm-5.2", input_tokens=0, output_tokens=-5)


@pytest.mark.parametrize("bad_date", ["2026/07/02", "2026-7-2", "26-07-02", "not-a-date"])
def test_usage_point_rejects_bad_date_format(bad_date):
    with pytest.raises(Exception):
        UsagePoint(date=bad_date, model_id="glm-5.2", input_tokens=0, output_tokens=0)


def test_report_request_defaults_optional_fields():
    r = UsageReportRequest(source_id="m1")
    assert r.source_label is None
    assert r.points == []


import pytest
from datetime import datetime, timedelta
from fastapi.testclient import TestClient

from ai_plan_insight import usage_store, web
from ai_plan_insight.usage_store import UTC8

TODAY = datetime.now(UTC8).date().isoformat()
YESTERDAY = (datetime.now(UTC8).date() - timedelta(days=1)).isoformat()


@pytest.fixture
def usage_db(tmp_path, monkeypatch):
    """Point web at a temp DB and initialize the schema."""
    import json
    from ai_plan_insight.config_service import ConfigService

    db = tmp_path / "usage.db"
    monkeypatch.setattr(web, "_usage_db_path", db)
    usage_store.init_db(db)
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"providers": {}}))
    monkeypatch.setattr(web, "_config_service", ConfigService(cfg))
    return db


def test_report_upserts_points_and_returns_count(usage_db):
    client = TestClient(web.app)
    resp = client.post("/api/usage/report", json={
        "source_id": "macbook-flinty",
        "source_label": "MacBook Pro",
        "points": [
            {"date": TODAY, "model_id": "glm-5.2", "input_tokens": 100, "output_tokens": 50},
            {"date": TODAY, "model_id": "claude-sonnet-4-5", "input_tokens": 80, "output_tokens": 30},
        ],
    })
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "upserted": 2, "dropped": 0}


def test_report_freezes_days_before_reported_at(usage_db):
    client = TestClient(web.app)
    first = client.post("/api/usage/report", json={
        "source_id": "m1",
        "reported_at": TODAY,
        "points": [{"date": YESTERDAY, "model_id": "glm-5.2",
                    "input_tokens": 100, "output_tokens": 0}],
    })
    assert first.json() == {"ok": True, "upserted": 1, "dropped": 0}
    # corrupted local history tries to rewrite yesterday
    second = client.post("/api/usage/report", json={
        "source_id": "m1",
        "reported_at": TODAY,
        "points": [{"date": YESTERDAY, "model_id": "glm-5.2",
                    "input_tokens": 1, "output_tokens": 0}],
    })
    assert second.json() == {"ok": True, "upserted": 0, "dropped": 1}
    import sqlite3
    rows = usage_store.query_timeseries(sqlite3.connect(usage_db), 7, {})
    assert rows[0].input_tokens == 100


def test_report_bad_reported_at_returns_422(usage_db):
    client = TestClient(web.app)
    resp = client.post("/api/usage/report", json={
        "source_id": "m1", "reported_at": "2026/07/02", "points": [],
    })
    assert resp.status_code == 422


def test_report_missing_source_id_returns_400(usage_db):
    client = TestClient(web.app)
    resp = client.post("/api/usage/report", json={"points": []})
    assert resp.status_code == 400


def test_report_empty_source_id_returns_400(usage_db):
    client = TestClient(web.app)
    resp = client.post("/api/usage/report", json={"source_id": "", "points": []})
    assert resp.status_code == 400


def test_report_malformed_body_returns_422(usage_db):
    client = TestClient(web.app)
    resp = client.post("/api/usage/report", json={
        "source_id": "m1",
        "points": [{"date": "2026/07/02", "model_id": "x", "input_tokens": 1, "output_tokens": 1}],
    })
    assert resp.status_code == 422


def test_report_repost_overwrites_without_double_count(usage_db):
    client = TestClient(web.app)
    payload = {
        "source_id": "m1",
        "points": [{"date": TODAY, "model_id": "glm-5.2", "input_tokens": 100, "output_tokens": 50}],
    }
    client.post("/api/usage/report", json=payload)
    client.post("/api/usage/report", json=payload)  # same data again
    import sqlite3
    rows = usage_store.query_timeseries(sqlite3.connect(usage_db), 7, {})
    assert len(rows) == 1 and rows[0].total == 150


def _seed_two_sources(client):
    """Two sources, one model, same day -> should SUM."""
    client.post("/api/usage/report", json={
        "source_id": "m1", "points": [
            {"date": TODAY, "model_id": "glm-5.2", "input_tokens": 1000, "output_tokens": 500},
        ],
    })
    client.post("/api/usage/report", json={
        "source_id": "m2", "points": [
            {"date": TODAY, "model_id": "glm-5.2", "input_tokens": 2000, "output_tokens": 1000},
        ],
    })


def test_timeseries_default_range_is_90(usage_db, monkeypatch):
    _seed_two_sources(TestClient(web.app))

    resp = TestClient(web.app).get("/api/usage/timeseries")
    body = resp.json()
    assert body["range_days"] == 90
    assert body["generated_at"]  # non-empty


def test_timeseries_invalid_days_falls_back_to_90(usage_db, monkeypatch):
    resp = TestClient(web.app).get("/api/usage/timeseries?days=5")
    assert resp.json()["range_days"] == 90


def test_timeseries_aggregates_across_sources_and_applies_alias(usage_db, monkeypatch, tmp_path):
    import json
    from ai_plan_insight.config_service import ConfigService

    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({
        "providers": {},
        "model_aliases": {"GLM 5.2": ["glm-5.2"]},
    }))
    monkeypatch.setattr(web, "_config_service", ConfigService(cfg))
    _seed_two_sources(TestClient(web.app))

    resp = TestClient(web.app).get("/api/usage/timeseries?days=7")
    body = resp.json()
    assert len(body["days"]) == 1
    day = body["days"][0]
    assert day["date"] == TODAY
    assert len(day["models"]) == 1
    m = day["models"][0]
    assert m["label"] == "GLM 5.2"
    assert m["raw_ids"] == ["glm-5.2"]
    assert m["input_tokens"] == 3000
    assert m["output_tokens"] == 1500
    assert m["total"] == 4500
    # summary
    assert len(body["models"]) == 1
    assert body["models"][0]["label"] == "GLM 5.2"
    assert body["models"][0]["grand_total"] == 4500
    assert body["models"][0]["share_pct"] == 100.0
    assert body["models"][0]["color"]


def test_timeseries_unknown_model_kept_as_own_label(usage_db, monkeypatch):
    client = TestClient(web.app)
    client.post("/api/usage/report", json={
        "source_id": "m1", "points": [
            {"date": TODAY, "model_id": "brand-new-model", "input_tokens": 10, "output_tokens": 5},
        ],
    })
    body = client.get("/api/usage/timeseries?days=7").json()
    labels = {m["label"] for m in body["models"]}
    assert "brand-new-model" in labels  # unknown -> own label, renders fine


def test_timeseries_empty_returns_empty_days(monkeypatch, usage_db):
    body = TestClient(web.app).get("/api/usage/timeseries?days=7").json()
    assert body["days"] == []
    assert body["models"] == []


def test_timeseries_all_models_carries_input_output_and_is_not_collapsed(usage_db, monkeypatch):
    """`all_models` keeps every model separate (no top-8 + 其他 rollup)
    and reports input/output token totals for each."""
    client = TestClient(web.app)
    # one full-day snapshot, as the agent sends it (a repeat post for the same
    # date replaces the whole day, so models must arrive together)
    client.post("/api/usage/report", json={
        "source_id": "m1",
        "points": [{"date": TODAY, "model_id": f"model-{i}",
                    "input_tokens": 100 * (i + 1), "output_tokens": 50 * (i + 1)}
                   for i in range(10)],
    })

    body = client.get("/api/usage/timeseries?days=7").json()
    assert "all_models" in body
    all_models = body["all_models"]
    assert len(all_models) == 10  # none collapsed into 其他
    labels = {m["label"] for m in all_models}
    assert "其他" not in labels
    # input/output reported per model
    by_label = {m["label"]: m for m in all_models}
    m0 = by_label["model-0"]
    assert m0["input_tokens"] == 100
    assert m0["output_tokens"] == 50
    assert m0["grand_total"] == 150
    assert m0["share_pct"] >= 0
    # chart `models` still collapses (<= 8 + 其他), so it's smaller than all_models
    assert len(body["models"]) <= 9
    assert len(body["models"]) < len(all_models)


def test_timeseries_models_summary_includes_input_output(usage_db, monkeypatch):
    client = TestClient(web.app)
    client.post("/api/usage/report", json={
        "source_id": "m1",
        "points": [{"date": TODAY, "model_id": "glm-5.2", "input_tokens": 300, "output_tokens": 120}],
    })
    body = client.get("/api/usage/timeseries?days=7").json()
    m = body["models"][0]
    assert m["input_tokens"] == 300
    assert m["output_tokens"] == 120


def test_report_accepts_token_breakdown_and_timeseries_surfaces_it(usage_db, monkeypatch):
    """The five token categories round-trip end-to-end through report -> timeseries."""
    client = TestClient(web.app)
    resp = client.post("/api/usage/report", json={
        "source_id": "m1",
        "points": [{
            "date": TODAY, "model_id": "glm-5.2",
            "input_tokens": 100, "output_tokens": 50,
            "cache_read_tokens": 80, "cache_write_tokens": 20, "reasoning_tokens": 5,
        }],
    })
    assert resp.status_code == 200
    body = client.get("/api/usage/timeseries?days=7").json()
    m = body["days"][0]["models"][0]
    assert m["cache_read_tokens"] == 80
    assert m["cache_write_tokens"] == 20
    assert m["reasoning_tokens"] == 5
    assert m["total"] == 255  # input+output+cache_read+cache_write+reasoning
    summary = body["models"][0]
    assert summary["cache_read_tokens"] == 80
    assert summary["grand_total"] == 255


def test_report_legacy_payload_without_cache_still_accepted(usage_db, monkeypatch):
    """An older reporter sending only input/output must keep working (back-compat)."""
    client = TestClient(web.app)
    resp = client.post("/api/usage/report", json={
        "source_id": "m1",
        "points": [{"date": TODAY, "model_id": "glm-5.2",
                    "input_tokens": 100, "output_tokens": 50}],
    })
    assert resp.status_code == 200
    body = client.get("/api/usage/timeseries?days=7").json()
    m = body["days"][0]["models"][0]
    assert m["cache_read_tokens"] == 0
    assert m["total"] == 150


