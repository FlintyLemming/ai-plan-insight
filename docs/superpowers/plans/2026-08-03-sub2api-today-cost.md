# Sub2API 今日花费 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Sub2API/Codex quota、套餐、钱包卡片的 `balances` 增加「今日花费」。

**Architecture:** 从 `/v1/usage` 的 `usage.today.actual_cost`（回退 `cost`）解析金额，统一 helper 写入 balances；前端沿用现有 balance-row。

**Tech Stack:** Python, pytest, existing `CodexProvider` / `Sub2ApiProvider`

## Global Constraints

- Prefer `actual_cost` over `cost`; omit row when missing
- Format as `$x.xx` with two decimal places
- Do not add today-cost balance row to Codex unrestricted path
- Do not change frontend or Sub2API server

---

## File Map

| File | Role |
|------|------|
| `tests/test_codex_provider.py` | New unit tests for parse paths |
| `ai_plan_insight/providers/codex.py` | Helper + balances update in 3 paths |

---

### Task 1: Tests + helper + wire into parse paths

**Files:**
- Create: `tests/test_codex_provider.py`
- Modify: `ai_plan_insight/providers/codex.py`

- [ ] **Step 1: Write failing tests** covering plan/wallet/quota/unrestricted cases from the spec

- [ ] **Step 2: Run tests — expect fail**

```bash
cd /home/flintylemming/Projects/ai-plan-insight && .venv/bin/python -m pytest tests/test_codex_provider.py -q
```

- [ ] **Step 3: Implement helper and wire into `_parse_quota_limited`, `_parse_plan`, `_parse_wallet`**

- [ ] **Step 4: Run tests — expect pass**

```bash
cd /home/flintylemming/Projects/ai-plan-insight && .venv/bin/python -m pytest tests/test_codex_provider.py -q
```

- [ ] **Step 5: Commit**
