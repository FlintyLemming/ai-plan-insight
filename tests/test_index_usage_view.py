from pathlib import Path

INDEX_HTML = Path(__file__).resolve().parents[1] / "ai_plan_insight" / "index.html"


def read_index() -> str:
    return INDEX_HTML.read_text(encoding="utf-8")


def test_tab_bar_present():
    h = read_index()
    assert 'data-tab="balance"' in h
    assert 'data-tab="usage"' in h
    assert "订阅余额" in h
    assert "模型用量" in h


def test_tab_state_keys_in_localstorage():
    h = read_index()
    assert "ai-plan-insight:tab" in h


def test_usage_chart_view_container_present():
    h = read_index()
    assert 'id="usage-chart-view"' in h


def test_range_pills_present():
    h = read_index()
    assert "range-pill" in h
    assert "90天" in h
    assert "30天" in h
    assert "7天" in h
    assert "ai-plan-insight:usage-range" in h
