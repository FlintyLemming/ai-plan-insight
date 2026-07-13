# 模型名左对齐 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让模型用量表的模型名单元格在手机端和桌面端靠左对齐，同时保持数值列靠右。

**Architecture:** 保留现有表格结构和通用右对齐规则，仅为 `td.col-model` 增加更具体的 CSS 覆盖。使用现有的静态 HTML 回归测试验证规则存在，并通过 Playwright 计算样式检查实际浏览器行为。

**Tech Stack:** HTML, CSS, pytest, Playwright

---

### Task 1: 添加回归测试并修复模型名对齐

**Files:**
- Modify: `tests/test_index_usage_view.py`
- Modify: `ai_plan_insight/index.html`

- [ ] **Step 1: 写入失败测试**

在 `tests/test_index_usage_view.py` 中添加：

```python
def test_usage_table_model_cells_are_left_aligned():
    h = read_index()
    assert re.search(
        r"\.usage-table\s+td\.col-model\s*\{[^}]*text-align:\s*left;[^}]*\}",
        h,
        re.DOTALL,
    )
```

- [ ] **Step 2: 运行测试并确认按预期失败**

Run: `pytest tests/test_index_usage_view.py::test_usage_table_model_cells_are_left_aligned -v`

Expected: FAIL，因为 `index.html` 尚未包含 `.usage-table td.col-model` 左对齐规则。

- [ ] **Step 3: 添加最小 CSS 实现**

在 `ai_plan_insight/index.html` 的模型列表头规则附近添加：

```css
.usage-table td.col-model {
  text-align: left;
}
```

- [ ] **Step 4: 运行聚焦测试并确认通过**

Run: `pytest tests/test_index_usage_view.py::test_usage_table_model_cells_are_left_aligned -v`

Expected: PASS。

- [ ] **Step 5: 运行模型用量页测试**

Run: `pytest tests/test_index_usage_view.py -v`

Expected: 全部 PASS。

- [ ] **Step 6: 运行完整测试集**

Run: `pytest -q`

Expected: 全部 PASS，且没有新增错误或警告。

- [ ] **Step 7: 检查浏览器计算样式**

启动本地服务并在手机、桌面视口检查：

```javascript
getComputedStyle(document.querySelector('.usage-table td.col-model')).textAlign === 'left'
getComputedStyle(document.querySelector('.usage-table td.num')).textAlign === 'right'
```

Expected: 两个表达式均为 `true`，页面没有新增水平溢出或布局错位。

- [ ] **Step 8: 提交实现**

```bash
git add tests/test_index_usage_view.py ai_plan_insight/index.html
git commit -m "fix(ui): left-align model names in usage table"
```
