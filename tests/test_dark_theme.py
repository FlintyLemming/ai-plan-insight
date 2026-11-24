import re
from pathlib import Path

PKG = Path(__file__).resolve().parents[1] / "ai_plan_insight"
INDEX_HTML = PKG / "index.html"
ADMIN_HTML = PKG / "admin.html"

from ai_plan_insight import web


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def dark_block(h: str) -> str:
    start = h.index(':root[data-theme="dark"] {')
    return h[start:h.index("}", start)]


def theme_script(h: str) -> str:
    match = re.search(r"<script>\s*// Theme: .*?</script>", h, re.S)
    assert match is not None, "expected the theme script in <head>"
    return match.group(0)


SHARED_DARK_TOKENS = (
    "color-scheme: dark;",
    "--canvas: #131210;",
    "--surface-card: #1c1b18;",
    "--hairline: #2c2b26;",
    "--ink: #efede6;",
    "--body: #b6b3a9;",
    "--error: #ec5f82;",
)


def test_both_pages_define_the_same_dark_tokens():
    for path in (INDEX_HTML, ADMIN_HTML):
        block = dark_block(read(path))
        for token in SHARED_DARK_TOKENS:
            assert token in block, f"{token} missing from {path.name}"


def test_theme_script_runs_in_head_and_is_shared():
    index, admin = read(INDEX_HTML), read(ADMIN_HTML)
    script = theme_script(index)
    assert script == theme_script(admin)
    assert "ai-plan-insight:theme" in script
    assert "prefers-color-scheme: dark" in script
    # Before first paint: the script sits in <head>, ahead of <body>.
    for h in (index, admin):
        assert h.index("// Theme: ") < h.index("</head>")


def test_both_pages_render_the_theme_toggle():
    for path in (INDEX_HTML, ADMIN_HTML):
        h = read(path)
        assert 'id="theme-toggle"' in h
        for icon in ("system", "light", "dark"):
            assert f'data-icon="{icon}"' in h


def test_admin_ink_tints_are_tokens():
    h = read(ADMIN_HTML)
    # Each light value appears once — in its :root token — and nowhere else.
    assert h.count("#3a3930") == 1
    assert h.count("rgba(38, 37, 30") == 2  # --focus-ring and --backdrop
    assert "var(--ink-hover)" in h
    assert "var(--focus-ring)" in h
    assert "var(--backdrop)" in h


def test_series_tokens_match_the_server_palette():
    h = read(INDEX_HTML)
    light = h[h.index(":root {"):h.index(':root[data-theme="dark"]')]
    for slot, hex_ in enumerate(web.USAGE_PALETTE, start=1):
        assert f"--series-{slot}: {hex_};" in light
    assert f"--series-other: {web.USAGE_OTHER_COLOR};" in light
    dark = dark_block(h)
    for slot in range(1, len(web.USAGE_PALETTE) + 1):
        assert f"--series-{slot}: #" in dark
    assert "--series-other: #" in dark
    assert "--chart-dim: #" in dark


def test_chart_colors_go_through_series_tokens():
    h = read(INDEX_HTML)
    assert "function seriesColor(hex)" in h
    assert "#usage-svg rect[data-model] { fill: var(--seg); }" in h
    assert "fill: var(--chart-dim);" in h
    assert "USAGE_COLORS.slice(0, 6).map(seriesColor)" in h
    # var() does not resolve in SVG presentation attributes.
    assert 'stroke="${model.color}"' not in h
    assert 'fill="${model.color}"' not in h
    assert "setAttribute('fill', model.color)" not in h
