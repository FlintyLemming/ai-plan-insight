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
from datetime import date
from fastapi.testclient import TestClient

from ai_plan_insight import usage_store, web

TODAY = date.today().isoformat()


@pytest.fixture
def usage_db(tmp_path, monkeypatch):
    """Point web at a temp DB and initialize the schema."""
    db = tmp_path / "usage.db"
    monkeypatch.setattr(web, "_usage_db_path", db)
    usage_store.init_db(db)
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
    assert resp.json() == {"ok": True, "upserted": 2}


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
