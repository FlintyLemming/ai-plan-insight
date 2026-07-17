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
    # padL is assigned both a mobile value (132) and a desktop value (86).
    # The labels must fit at the smaller (desktop) padding.
    pad_values = [int(v) for v in re.findall(r"padL = \w+ \? (\d+) : (\d+)", chart_renderer)[0]]
    offset_match = re.search(r'x="\$\{padL - (\d+)\}"', chart_renderer)

    assert pad_values, "expected a mobile/desktop padL ternary"
    assert offset_match is not None
    # Use the smallest configured padL so the invariant holds on every viewport.
    label_anchor_x = min(pad_values) - int(offset_match.group(1))
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


def test_usage_table_model_cells_are_left_aligned():
    h = read_index()
    assert re.search(
        r"\.usage-table\s+td\.col-model\s*\{[^}]*text-align:\s*left;[^}]*\}",
        h,
        re.DOTALL,
    )


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


def test_usage_refresh_clears_global_loading_indicators():
    # Loading directly into the usage tab must clear the header placeholders
    # ("加载中..." / "正在获取数据..."), which are otherwise only handled by the
    # balance-tab refresh paths.
    h = read_index()
    fn = h[h.index("async function refreshUsageChart"):h.index("function renderStaleSources")]
    assert "getElementById('loading')" in fn
    assert "getElementById('updated')" in fn
    assert "last_updated" in fn
