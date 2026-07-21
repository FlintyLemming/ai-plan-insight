# Balance Card Four-Column Layout Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Preserve a 364px desktop subscription-card width while allowing at most four centered columns and retaining narrow-screen fit.

**Architecture:** Keep the existing CSS Grid and card markup. Replace the fluid 280px-to-`1fr` tracks with a 364px fixed desktop track, a one-column narrow-screen fallback, and a calculated four-track container cap driven by CSS custom properties.

**Tech Stack:** Inline HTML/CSS, Python 3.12, pytest, Playwright/Chromium for rendered layout verification

---

### Task 1: Lock the balance-grid layout contract

**Files:**
- Create: `tests/test_balance_grid_layout.py`

- [ ] **Step 1: Write the failing regression test**

```python
from pathlib import Path


INDEX_HTML = Path(__file__).resolve().parents[1] / "ai_plan_insight" / "index.html"


def test_balance_grid_preserves_desktop_card_width_and_four_column_cap():
    html = INDEX_HTML.read_text(encoding="utf-8")
    base_grid = html[html.index("  .grid {"):html.index("  .card {")]
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
```

- [ ] **Step 2: Run the focused test and verify RED**

Run: `uv run pytest tests/test_balance_grid_layout.py -q`

Expected: FAIL because version 0.12.2 has no `@media (min-width: 388px)` block or 364px fixed-track variables.

### Task 2: Implement the fixed-width responsive grid

**Files:**
- Modify: `ai_plan_insight/index.html:22-43`
- Modify: `pyproject.toml:3`
- Modify: `uv.lock:7`

- [ ] **Step 1: Replace the fluid grid rules with the approved layout**

```css
  .grid {
    --balance-card-width: 364px;
    --balance-grid-gap: 1rem;
    display: grid;
    grid-template-columns: minmax(0, 1fr);
    gap: var(--balance-grid-gap);
    width: 100%;
    max-width: calc(4 * var(--balance-card-width) + 3 * var(--balance-grid-gap));
    justify-content: center;
  }
  @media (min-width: 388px) {
    .grid { grid-template-columns: repeat(auto-fit, var(--balance-card-width)); }
  }
```

In the existing `@media (min-width: 640px)` block, replace `.grid { gap: 1.5rem; }` with:

```css
    .grid { --balance-grid-gap: 1.5rem; }
```

- [ ] **Step 2: Bump the package version**

Change `pyproject.toml` from:

```toml
version = "0.12.2"
```

to:

```toml
version = "0.12.3"
```

Run `uv run pytest tests/test_balance_grid_layout.py -q` to synchronize the editable package entry in `uv.lock` to the same `0.12.3` version.

- [ ] **Step 3: Run the focused test and verify GREEN**

Run: `uv run pytest tests/test_balance_grid_layout.py -q`

Expected: `1 passed`.

- [ ] **Step 4: Run the complete automated test suite**

Run: `uv run pytest -q`

Expected: all tests pass with no failures.

### Task 3: Verify rendered behavior and publish

**Files:**
- Verify: `ai_plan_insight/index.html`
- Commit: `tests/test_balance_grid_layout.py`, `ai_plan_insight/index.html`, `pyproject.toml`, `uv.lock`, and this plan

- [ ] **Step 1: Start the application locally**

Run: `uv run python -m ai_plan_insight --web --host 127.0.0.1 --port 8765 --config tests/test-config.json`

Expected: Uvicorn serves `http://127.0.0.1:8765/`.

- [ ] **Step 2: Verify layout in Chromium**

Inject at least eight `.card` elements into `#grid`, make the grid visible, and check these viewport outcomes:

```text
360px viewport: 1 column, card fits available width, no horizontal overflow
1200px viewport: 3 columns, every card is 364px wide
1600px viewport: 4 columns, every card is 364px wide
1920px viewport: 4 columns, every card is 364px wide and the grid is centered
```

Capture desktop and mobile screenshots and confirm there is no text or control overlap.

- [ ] **Step 3: Review the final diff**

Run: `git diff --check && git diff -- ai_plan_insight/index.html pyproject.toml tests/test_balance_grid_layout.py docs/superpowers/plans/2026-07-21-balance-card-four-column-layout.md`

Expected: no whitespace errors; only the approved layout, regression test, plan, and version bump are present.

- [ ] **Step 4: Commit the implementation**

```bash
git add -- ai_plan_insight/index.html pyproject.toml uv.lock tests/test_balance_grid_layout.py docs/superpowers/plans/2026-07-21-balance-card-four-column-layout.md
git commit -m "fix(balance): preserve card width with four-column cap"
```

- [ ] **Step 5: Tag and push the release**

```bash
git tag -a v0.12.3 -m "v0.12.3"
git push origin main
git push origin v0.12.3
```

Expected: `origin/main` advances to the implementation commit and the `v0.12.3` tag is published.
