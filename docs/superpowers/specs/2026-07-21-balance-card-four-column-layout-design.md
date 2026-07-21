# Balance Card Four-Column Layout Design

## Goal

Keep subscription balance cards at the pre-change desktop width of 364px while allowing up to four centered columns on sufficiently wide screens. Cards may shrink only when the viewport cannot provide 364px of usable width.

## Root Cause

The current grid uses `repeat(auto-fit, minmax(280px, 1fr))` inside a 1200px container. At four columns with three 24px gaps, each track is `(1200 - 72) / 4 = 282px`. The 280px value is only a minimum, so restoring that declaration did not restore the previous rendered width.

## Layout

- Use a 364px fixed track for desktop balance cards.
- Let the grid create as many fixed tracks as fit and center incomplete rows.
- Cap the grid at four tracks: `4 * 364px + 3 * 24px = 1528px`.
- Keep the existing 16px mobile gap and 24px desktop gap.
- When the viewport's usable width is below 364px, use one flexible track so the card fits without horizontal overflow.
- Do not change card padding, content, rendering, or the model-usage view.

Expected desktop behavior:

| Usable grid width | Columns | Card width |
| --- | ---: | ---: |
| Below 364px | 1 | Available width |
| 364px to 751px | 1 | 364px |
| 752px to 1139px | 2 | 364px |
| 1140px to 1527px | 3 | 364px |
| 1528px and above | 4 | 364px |

## Verification

- Add a regression test that locks the 364px track and four-column 1528px cap.
- Confirm the test fails against version 0.12.2 before changing production CSS.
- Run the focused test and the full test suite after the fix.
- Verify rendered card widths and column counts at narrow mobile, 1200px-class desktop, and wide desktop viewports.
- Confirm there is no horizontal overflow and no overlap at any checked viewport.

## Release

Bump the project version from 0.12.2 to 0.12.3. Deployment is outside this code change.
