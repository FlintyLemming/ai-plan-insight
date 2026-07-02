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
