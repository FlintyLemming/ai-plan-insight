from pathlib import Path
import re


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


def test_index_contains_hover_tooltip_and_vertical_cursor():
    html = read_index()

    assert "history-cursor" in html
    assert "history-tooltip" in html
    assert "function handleHistoryHover" in html
    assert "function showHistoryTooltip" in html
    assert "function hideHistoryTooltip" in html
    assert "data-history-chart" in html
    assert "addEventListener('mousemove'" in html
    assert "addEventListener('mouseleave'" in html


def test_index_tooltip_renders_date_and_per_model_tokens():
    html = read_index()

    assert "data-idx" in html or "dataset.idx" in html
    assert "formatTokens" in html
    assert "history-tooltip-row" in html or "history-tooltip-model" in html


def test_index_hover_uses_screen_ctm_for_coordinate_conversion():
    html = read_index()

    assert "getScreenCTM" in html
    assert "createSVGPoint" in html
    assert "matrixTransform" in html


def test_index_hover_passes_vertical_padding_to_cursor_dot_math():
    html = read_index()

    signature = re.search(r"function handleHistoryHover\(([^)]*)\)", html)
    assert signature is not None
    assert signature.group(1).replace(" ", "").endswith("width,height,padX,padY")

    assert "handleHistoryHover(event, svg, tooltipEl, cursorEl, dotEls, points, normalizedModels, dates, width, height, padX, padY);" in html
