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


def test_chart_renderer_and_palette_present():
    h = read_index()
    assert "function renderUsageChart" in h
    assert "USAGE_COLORS" in h
    assert "#38bdf8" in h   # palette present
    assert "#64748b" in h   # "其他" color present


def test_chart_empty_state_message_present():
    h = read_index()
    assert "暂无用量数据" in h


def test_usage_fetch_and_refresh_wired():
    h = read_index()
    assert "function refreshUsageChart" in h
    assert "/api/usage/timeseries" in h
