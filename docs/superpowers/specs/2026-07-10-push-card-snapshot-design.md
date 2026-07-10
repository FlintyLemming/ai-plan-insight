# Push Card Snapshot (Restart Restore)

## Problem

Push-only cards (Cursor / Claude / Grok / MiMo / Antigravity) live only in
memory (`_pushed_results` + `_pushed_at`). After a process restart the cards
disappear until the local agent pushes again.

## Goal

Always keep one latest snapshot per push key so restart can restore the last
card state. Existing TTL (`PUSH_TTL_SECONDS = 30min`) still filters: if the
restored snapshot is older than the TTL, the card is not shown.

## Scope

- Restore **push data only** (not fetch-cached providers).
- Keys: `antigravity`, `cursor`, `claude`, `mimo_token_plan`, `grok`.

## Design

### Table `push_card_snapshot`

```sql
CREATE TABLE IF NOT EXISTS push_card_snapshot (
    push_key     TEXT PRIMARY KEY,
    recorded_at  TEXT NOT NULL,
    raw_json     TEXT NOT NULL
)
```

- UPSERT on every successful push (one row per key).
- `raw_json` = `UsageResponse.model_dump_json()`.
- `recorded_at` = the same timestamp written to `_pushed_at`.

### Store API

- `upsert_push_card_snapshot(conn, push_key, usage, recorded_at=None)`
- `load_push_card_snapshots(conn) -> list[tuple[str, datetime, UsageResponse]]`

### Web layer

- On each `/api/push/*`: after updating memory, call upsert (best-effort).
- On lifespan startup: after `init_db`, load snapshots into
  `_pushed_results` / `_pushed_at`.
- `/api/usage` TTL filter unchanged.

## Non-goals

- No change to historical `provider_snapshot` table.
- No restore of fetch provider cards.
- No multi-version history for push cards (only latest).
