from pathlib import Path


INDEX_HTML = Path(__file__).resolve().parents[1] / "ai_plan_insight" / "index.html"


def test_balance_grid_preserves_desktop_card_width_and_four_column_cap():
    html = INDEX_HTML.read_text(encoding="utf-8")
    base_grid = html[html.index("  .grid {"):html.index("  .card {")]
    assert "  @media (min-width: 388px)" in html
    fixed_track_media = html[
        html.index("  @media (min-width: 388px)"):
        html.index("  @media (min-width: 640px)")
    ]

    assert "--balance-card-width: 364px;" in base_grid
    assert "--balance-grid-gap: 1rem;" in base_grid
    assert "grid-template-columns: minmax(0, 1fr);" in base_grid
    assert "gap: var(--balance-grid-gap);" in base_grid
    assert (
        "max-width: calc(4 * var(--balance-card-width) + "
        "3 * var(--balance-grid-gap));"
    ) in base_grid
    assert "justify-content: center;" in base_grid
    assert (
        "grid-template-columns: repeat(auto-fit, var(--balance-card-width));"
    ) in fixed_track_media
    assert "minmax(280px, 1fr)" not in base_grid
