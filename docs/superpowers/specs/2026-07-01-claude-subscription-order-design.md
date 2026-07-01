# Claude Subscription Limits Reordering Design Spec

**Goal:** Put the 5-hour session usage card above the 7-day usage card for "Claude 订阅" in the AI Plan Insight interface.

## Proposed Changes

### 1. Web Backend (`ai_plan_insight/web.py`)
Swap the list elements in the `limits` field for the `UsageResponse` model created under the `/api/push/claude` endpoint, putting the 5-hour limit first.

### 2. Testing (`tests/test_web_push_claude.py`)
Swap the assertions verifying `limits[0]` (now 5-hour) and `limits[1]` (now 7-day).
