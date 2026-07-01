# Claude Subscription Limits Reordering Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Put the 5-hour session usage card above the 7-day usage card for "Claude 订阅" in the AI Plan Insight interface.

**Architecture:** Swap the order of limits list elements returned in `UsageResponse` for `push_claude` in `ai_plan_insight/web.py`, and update the assertions in `tests/test_web_push_claude.py` to match the new order.

**Tech Stack:** Python, FastAPI, Pytest

---

### Task 1: Swap Limit Order in web.py

**Files:**
- Modify: `ai_plan_insight/web.py:336-353`

- [ ] **Step 1: Edit web.py to swap 7-day and 5-hour limit responses**

Target code block in `ai_plan_insight/web.py`:
```python
    _pushed_results["claude"] = UsageResponse(
        provider="Claude 订阅",
        limits=[
            LimitResponse(
                duration=7,
                time_unit="天",
                limit="100",
                used=str(int(req.seven_day.utilization)),
                remaining=str(int(100 - req.seven_day.utilization)),
                reset_time=req.seven_day.resets_at,
            ),
            LimitResponse(
                duration=5,
                time_unit="小时",
                limit="100",
                used=str(int(req.five_hour.utilization)),
                remaining=str(int(100 - req.five_hour.utilization)),
                reset_time=req.five_hour.resets_at,
            ),
        ],
    )
```

Replace the block with:
```python
    _pushed_results["claude"] = UsageResponse(
        provider="Claude 订阅",
        limits=[
            LimitResponse(
                duration=5,
                time_unit="小时",
                limit="100",
                used=str(int(req.five_hour.utilization)),
                remaining=str(int(100 - req.five_hour.utilization)),
                reset_time=req.five_hour.resets_at,
            ),
            LimitResponse(
                duration=7,
                time_unit="天",
                limit="100",
                used=str(int(req.seven_day.utilization)),
                remaining=str(int(100 - req.seven_day.utilization)),
                reset_time=req.seven_day.resets_at,
            ),
        ],
    )
```

- [ ] **Step 2: Commit backend change**

Run:
```bash
git add ai_plan_insight/web.py
git commit -m "feat: put 5-hour limit above 7-day limit in Claude subscription"
```


### Task 2: Swap Assertions in test_web_push_claude.py and Verify

**Files:**
- Modify: `tests/test_web_push_claude.py:48-67`

- [ ] **Step 1: Swap assertions in test_web_push_claude.py**

Target block in `tests/test_web_push_claude.py`:
```python
    seven = limits[0]
    assert seven["duration"] == 7
    assert seven["time_unit"] == "天"
    assert seven["limit"] == "100"
    assert seven["used"] == "45"
    # remaining = str(int(100 - 45.2)) = str(int(54.8)) = "54"（沿用 Antigravity 的截断写法）
    # 注意：设计文档测试叙述里写的 "55" 是笔误，与设计给出的实现代码矛盾，此处以实现为准。
    assert seven["remaining"] == "54"
    assert seven["reset_time"] == "2026-07-08T12:00:00Z"
    assert seven["limit_type"] == ""

    five = limits[1]
    assert five["duration"] == 5
    assert five["time_unit"] == "小时"
    assert five["limit"] == "100"
    # used = str(int(12.8)) = str(12) = "12"（截断，非四舍五入；设计文档叙述里的 "13" 是笔误）
    assert five["used"] == "12"
    assert five["remaining"] == "87"
    assert five["reset_time"] == "2026-07-01T15:00:00Z"
    assert five["limit_type"] == ""
```

Replace the block with:
```python
    five = limits[0]
    assert five["duration"] == 5
    assert five["time_unit"] == "小时"
    assert five["limit"] == "100"
    # used = str(int(12.8)) = str(12) = "12"（截断，非四舍五入；设计文档叙述里的 "13" 是笔误）
    assert five["used"] == "12"
    assert five["remaining"] == "87"
    assert five["reset_time"] == "2026-07-01T15:00:00Z"
    assert five["limit_type"] == ""

    seven = limits[1]
    assert seven["duration"] == 7
    assert seven["time_unit"] == "天"
    assert seven["limit"] == "100"
    assert seven["used"] == "45"
    # remaining = str(int(100 - 45.2)) = str(int(54.8)) = "54"（沿用 Antigravity 的截断写法）
    # 注意：设计文档测试叙述里写的 "55" 是笔误，与设计给出的实现代码矛盾，此处以实现为准。
    assert seven["remaining"] == "54"
    assert seven["reset_time"] == "2026-07-08T12:00:00Z"
    assert seven["limit_type"] == ""
```

- [ ] **Step 2: Run pytest to verify all tests pass**

Run: `pytest tests/test_web_push_claude.py`
Expected: PASS

- [ ] **Step 3: Commit test change**

Run:
```bash
git add tests/test_web_push_claude.py
git commit -m "test: update Claude subscription limit assertions to match new order"
```
