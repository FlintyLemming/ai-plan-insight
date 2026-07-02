# 模型用量显示 (Model Usage Display) — Design Spec

**Date:** 2026-07-02
**Scope:** ai-plan-insight web app — new top-of-page tab showing recent per-model token usage as a stacked bar chart, backed by SQLite, fed by external agent clients.
**Sibling spec:** `ai-usage-reporter` repo — `docs/superpowers/specs/2026-07-02-reporter-design.md` (the agent client).

---

## 1. Goal & Context

ai-plan-insight is a Python/FastAPI dashboard (single inline `index.html`, vanilla JS, no build step, no DB today) that currently shows subscription/balance cards for AI coding providers. We are adding an unrelated feature: a **per-model token usage chart** covering the last 90/30/7 days, fed by multiple agent clients running on the user's machines.

This is the first database-backed feature in the project. The existing in-memory card data flow is untouched.

Reference: `tokscale` (Rust) already parses ZCode's SQLite (`~/.zcode/cli/db/db.sqlite`) and JSONL transcripts and aggregates daily-per-model token usage. The companion `ai-usage-reporter` agent invokes `tokscale graph` (JSON output) and POSTs it here.

---

## 2. Top-level Layout — Tabs

Insert a tab bar immediately after `<h1>AI Plan Insight</h1>` in `index.html`. Two tabs:

- **订阅余额** — the existing card grid (current default; renamed from implicit "usage").
- **模型用量** — the new chart view.

A new container `#usage-chart-view` is added alongside the existing `.grid`. Tab switching toggles `display` between `.grid` and `#usage-chart-view`. The existing poll loop (`setInterval(refresh, 60000)`) is untouched for the card view; the chart view fetches its own data on activation and on its own refresh.

Tab state is persisted in `localStorage` key `ai-plan-insight:tab` (`'balance' | 'usage'`) and restored on load.

CSS for the tab bar goes in the existing inline `<style>` block, matching the dark-slate theme (`#1e293b` background, `#334155` borders, `#38bdf8` accent).

---

## 3. Range Selector — Pill Buttons

Below the tab bar, visible only when the **模型用量** tab is active, a row of three pill buttons: `90天` / `30天` / `7天`. Clicking re-fetches the timeseries with the chosen `days` param and re-renders the chart.

Selected range persisted in `localStorage` key `ai-plan-insight:usage-range` (default `90`). Style mirrors the existing per-card 用量/历史 toggle button.

---

## 4. Storage — SQLite

First DB in the project. Uses the stdlib `sqlite3` module — **no new dependency**. A new module `ai_plan_insight/usage_store.py` owns all DB access. DB path resolved as:

1. `--usage-db` CLI flag if given, else
2. same directory as `--config` (so `config.json`'s dir), else
3. repo root `/data/usage.db` (Docker default).

Connection opened per-request (SQLite handles concurrency; writes are short transactions). `PRAGMA journal_mode=WAL` set on init for concurrent read/write.

### Schema

```sql
CREATE TABLE IF NOT EXISTS usage_point (
    date          TEXT NOT NULL,          -- 'YYYY-MM-DD', UTC+8 bucket (agent responsibility)
    source_id     TEXT NOT NULL,          -- machine id, supplied by agent
    model_id      TEXT NOT NULL,          -- RAW model id e.g. 'glm-5.2'; aliasing is read-time
    input_tokens  INTEGER NOT NULL,
    output_tokens INTEGER NOT NULL,
    updated_at    TEXT NOT NULL,          -- ISO timestamp of last UPSERT
    PRIMARY KEY (date, source_id, model_id)
);

CREATE TABLE IF NOT EXISTS source (
    source_id   TEXT PRIMARY KEY,
    label       TEXT,
    last_seen   TEXT
);
```

**Why this key:** `(date, source_id, model_id)` is the natural UPSERT key. Cross-machine aggregation happens at read time via `SUM ... GROUP BY date, model_id`. A repeat POST for the same `(date, source_id, model_id)` overwrites rather than adds — so re-running the agent during a day refreshes today's figure without double-counting.

The `source_id` is mandatory in the payload; posts without it are rejected with HTTP 400.

---

## 5. Submit Endpoint — `POST /api/usage/report`

New route in `web.py`, modeled on the existing `POST /api/push/*` endpoints.

**Request body:**
```json
{
  "source_id": "macbook-flinty",
  "source_label": "MacBook Pro",
  "points": [
    {"date": "2026-07-02", "model_id": "glm-5.2",      "input_tokens": 1200000, "output_tokens": 450000},
    {"date": "2026-07-02", "model_id": "claude-sonnet-4-5", "input_tokens": 80000, "output_tokens": 30000},
    {"date": "2026-07-01", "model_id": "glm-5.2",      "input_tokens": 980000, "output_tokens": 410000}
  ]
}
```

**Behavior:**
- Validate with a pydantic model in `api_schemas.py` (`UsageReportRequest` → `source_id: str`, `source_label: str | None`, `points: list[UsagePoint]` where `UsagePoint{date, model_id, input_tokens, output_tokens}` with `date` matching `^\d{4}-\d{2}-\d{2}$`).
- For each point: `INSERT INTO usage_point (...) VALUES (...) ON CONFLICT(date, source_id, model_id) DO UPDATE SET input_tokens=excluded.input_tokens, output_tokens=excluded.output_tokens, updated_at=?` — single transaction.
- UPSERT the `source` row (`last_seen = now`).
- Return `{"ok": true, "upserted": N}` with HTTP 200.

**Auth:** none (matches existing push endpoints — localhost deployment assumption). A shared-secret `X-Report-Key` header is a future option; not built now (YAGNI).

**Errors:** malformed JSON → 422; missing `source_id` → 400; DB error → 500 with `{"error": "..."}`. The agent retries on next run.

---

## 6. Read Endpoint — `GET /api/usage/timeseries?days=90`

Returns the alias-resolved, cross-machine-aggregated chart payload.

**Behavior:**
- `days` ∈ {7, 30, 90}. Any other value (or missing) → default 90. No partial clamping — just the documented default.
- Compute date window: last `days` days ending today (UTC+8 "today" — server reads system clock; agent guarantees UTC+8 buckets, server just filters by date string).
- `SELECT date, model_id, SUM(input_tokens), SUM(output_tokens) FROM usage_point WHERE date >= :start GROUP BY date, model_id`.
- Apply alias map: map each raw `model_id` → canonical label via config's `model_aliases` (see §8). Sum tokens per canonical label per date.
- Compute per-model grand total across the window and percentage share.
- Assign colors: sort models by grand_total desc; top N get fixed palette colors, remainder rolled into an "其他" (Other) bucket with gray.

**Response:**
```json
{
  "range_days": 90,
  "generated_at": "2026-07-02T14:38:00+08:00",
  "days": [
    {
      "date": "2026-07-02",
      "models": [
        {"label": "GLM 5.2", "raw_ids": ["glm-5.2"], "input_tokens": 2180000, "output_tokens": 860000, "total": 3040000},
        {"label": "Claude Sonnet 4.5", "raw_ids": ["claude-sonnet-4-5"], "input_tokens": 80000, "output_tokens": 30000, "total": 110000}
      ]
    }
  ],
  "models": [
    {"label": "GLM 5.2", "color": "#38bdf8", "grand_total": 12345678, "share_pct": 87.3},
    {"label": "Claude Sonnet 4.5", "color": "#f59e0b", "grand_total": 2345678, "share_pct": 12.7}
  ]
}
```

---

## 7. Chart Rendering — Inline SVG

New JS function `renderUsageChart(data)` in `index.html`, building an SVG string (same hand-rolled approach as the existing `bindHistoryCharts` line chart). No external library, no CDN, no build step.

**Layout:**
- One vertical stacked bar per `days[]` entry. Bar count = 90/30/7. Approximate bar+gap widths sized so 90 fits comfortably (e.g. 6–8px each), 30 wider (~20px), 7 widest (~40px).
- Each bar is a stack of `<rect>` segments, one per model, colored by the model's assigned color. Segment height ∝ model's `total` tokens within that day.
- **Input/output split is shown in the tooltip, not as sub-segments** — a per-model × input/output double split would make bars unreadable. One color per model; tooltip shows input/output/total.
- Y-axis auto-scales to the max daily total in the window. A few horizontal gridlines + value labels.
- X-axis: date label every Nth bar (N chosen so labels don't collide; e.g. every 7th bar at 90-day range, every 3rd at 30, every bar at 7).
- **Tooltip:** on hover over a bar, a floating `<div>` shows date + per-model rows (label, input, output, total). Implementation: an overlay div positioned at mouse coords, populated from the bar's data index.
- **Corner legend (top-right inside the chart):** vertical list of models with color swatch + `share_pct`. Sourced from `response.models[]`.

**Palette (fixed, in order of model rank):**
`#38bdf8` (sky), `#f59e0b` (amber), `#34d399` (emerald), `#f472b6` (pink), `#a78bfa` (violet), `#fbbf24` (yellow), `#22d3ee` (cyan), `#fb7185` (rose). 9th+ → `#64748b` (slate, "其他").

**Empty state:** if `days` is empty, render a centered "暂无用量数据" message instead of the chart.

---

## 8. Alias System — `model_aliases` in `config.json`

New top-level config key, array form for easy editing:

```json
"model_aliases": {
  "GLM 5.2": ["glm-5.2", "glm5.2"],
  "GPT 5.2": ["gpt-5.2"],
  "Claude Sonnet 4.5": ["claude-sonnet-4-5", "claude-sonnet-4-5-20250929"]
}
```

- **Canonical label** = the key. **Aliases** = the array of raw ids that map to it.
- At config load, `config_loader.py` builds a reverse lookup `{raw_id → canonical}` once (e.g. `{"glm-5.2": "GLM 5.2", "glm5.2": "GLM 5.2"}`). Stored on the `Config` object as `alias_lookup: dict[str,str]`.
- Applied at read time in the timeseries endpoint, after raw aggregation. Storage always keeps the raw `model_id`.
- **Unknown raw ids** (not in any alias array) become their own canonical label (the raw id itself). They get a deterministic color. No error — a brand-new model renders fine; user can add an alias later.
- Duplicate raw ids across multiple arrays → last definition wins (build the reverse dict by simple assignment).
- Alias map cached at startup; config edit requires restart (consistent with the existing `order` field behavior).

`config.py` additions:
```python
class Config(BaseModel):
    providers: dict[str, ProviderConfig]
    model_aliases: dict[str, list[str]] = Field(default_factory=dict)

    @property
    def alias_lookup(self) -> dict[str, str]:
        # built lazily / cached
        ...
```

Update `config.json.example` with a commented `model_aliases` example.

---

## 9. Files Touched (ai-plan-insight)

| File | Change |
|---|---|
| `ai_plan_insight/index.html` | Tab bar, pill range buttons, `#usage-chart-view` container, `renderUsageChart()`, tooltip, legend, tab/range state in localStorage, CSS |
| `ai_plan_insight/web.py` | `POST /api/usage/report`, `GET /api/usage/timeseries`, init DB on lifespan startup |
| `ai_plan_insight/usage_store.py` | **NEW** — schema init, `upsert_points()`, `query_timeseries()` |
| `ai_plan_insight/api_schemas.py` | `UsageReportRequest`, `UsagePoint`, `UsageTimeseriesResponse` pydantic models |
| `ai_plan_insight/config.py` | `model_aliases` field + `alias_lookup` property |
| `ai_plan_insight/config_loader.py` | Pass `model_aliases` through; build reverse lookup |
| `ai_plan_insight/__main__.py` | `--usage-db` flag |
| `config.json.example` | `model_aliases` example |
| `tests/test_usage_store.py` | **NEW** — UPSERT idempotency, cross-source SUM, alias resolution |
| `tests/test_usage_api.py` | **NEW** — report endpoint validation, timeseries shape |
| `tests/test_usage_chart.py` | **NEW** — alias map building (pure fn, easy to test) |

---

## 10. Edge Cases & Error Handling

- **Missing day in window** → no bar rendered for that date (not a zero bar — avoids misleading "0 usage" spikes).
- **Only today has data** → single bar, Y-axis auto-scales. Normal.
- **Future-dated payload** → stored and rendered as-is (no timezone-boundary guard; user explicitly waived this).
- **DB locked / error on submit** → endpoint returns 500; agent logs and retries next run. Read endpoint returns empty chart (does not crash the page).
- **`source_id` change (new machine)** → old source's rows persist; aggregation sums both. Old source rows are never auto-deleted.
- **Negative token values** → rejected at pydantic validation (`ge=0`).
- **Very large token counts** → `INTEGER` in SQLite is 64-bit; no overflow concern at realistic scales.

---

## 11. Out of Scope (YAGNI)

- Input/output sub-segment split inside bars (tooltip-only instead).
- Per-source breakdown view (sum across machines only, for now).
- Cost/pricing display (tokscale computes it; we ignore — could add later).
- Cache/reasoning token display (input+output only).
- Auth on submit endpoint.
- Auto-deletion of stale source rows.
- Timezone handling on the server (agent owns the UTC+8 boundary).
