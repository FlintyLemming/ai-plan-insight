import re
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


def test_usage_chart_view_has_full_width_layout():
    h = read_index()
    assert "#usage-chart-view" in h
    assert "#usage-chart-view {\n    width: 100%;" in h


def test_usage_chart_y_axis_has_room_for_wide_labels():
    h = read_index()
    chart_renderer = h[h.index("function renderUsageChart"):h.index("// Stacked bars")]
    pad_match = re.search(r"padL = (\d+)", chart_renderer)
    offset_match = re.search(r'x="\$\{padL - (\d+)\}"', chart_renderer)

    assert pad_match is not None
    assert offset_match is not None
    label_anchor_x = int(pad_match.group(1)) - int(offset_match.group(1))
    assert label_anchor_x >= 70


def test_usage_chart_segments_do_not_depend_on_day_model_color():
    h = read_index()
    start = h.index("function renderUsageChart")
    end = h.index("// Legend below the chart")
    bar_renderer = h[start:end]
    assert 'fill="${m.color}"' not in bar_renderer
    assert 'fill="${segmentColor}"' in bar_renderer


def test_usage_chart_legend_renders_below_svg():
    h = read_index()
    assert ".usage-legend {" in h
    assert "let legendHtml = '';" in h
    assert '<div class="usage-legend">' in h
    svg_template = h[h.index('<svg id="usage-svg"'):h.index('</svg>')]
    assert "legendHtml" not in svg_template


def test_usage_table_container_present_after_chart():
    h = read_index()
    assert 'id="usage-table-container"' in h
    chart_pos = h.index('id="usage-chart-container"')
    table_pos = h.index('id="usage-table-container"')
    assert table_pos > chart_pos


def test_usage_table_renderer_and_pagination_present():
    h = read_index()
    assert "function renderUsageTable" in h
    assert "tableAllModels" in h
    assert "renderUsageTable()" in h


def test_usage_table_page_size_persisted_in_localstorage():
    h = read_index()
    assert "ai-plan-insight:usage-table-page-size" in h
    assert "TABLE_PAGE_SIZES" in h
    assert "10" in h and "20" in h and "50" in h and "100" in h


def test_usage_table_columns_match_spec():
    h = read_index()
    assert "模型名" in h
    assert "输入 token" in h
    assert "输出 token" in h


def test_usage_table_pagination_controls_present():
    h = read_index()
    assert "usage-page-btn" in h
    assert "上一页" in h
    assert "下一页" in h
    assert "usage-page-size-select" in h
    assert "buildPageNumbers" in h


def test_usage_table_empty_state_message_present():
    h = read_index()
    assert "usage-table-empty" in h
