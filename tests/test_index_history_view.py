from pathlib import Path


INDEX_HTML = Path(__file__).resolve().parents[1] / "ai_plan_insight" / "index.html"


def read_index() -> str:
    return INDEX_HTML.read_text(encoding="utf-8")


def test_index_contains_glm_history_view_state_and_toggle():
    html = read_index()

    assert "GLM_HISTORY_PROVIDERS" in html
    assert "cardViewModes" in html
    assert "latestUsageData" in html
    assert "toggleCardView" in html
    assert "data-view-toggle" in html
    assert "历史" in html
    assert "用量" in html


def test_index_contains_no_dependency_svg_history_renderer():
    html = read_index()

    assert "function renderHistoryView(data)" in html
    assert "function buildPolyline" in html
    assert "<svg" in html
    assert "polyline" in html
    assert "暂无近 30 天历史数据" in html


def test_index_limits_history_button_to_glm_providers():
    html = read_index()

    assert "GLM Coding Plan" in html
    assert "白嫖 GLM Coding Plan 国际版" in html
    assert "GLM_HISTORY_PROVIDERS.has(data.provider)" in html
