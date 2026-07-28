# 管理页订阅实例拖拽排序

日期：2026-07-28

## 问题

管理页「订阅实例」表格用「排序」数字输入框调整实例顺序（改数字 → 整行 `PUT /api/admin/instances/{id}` 保存）。问题：

- 数字无语义。默认值 999，实例间的数字关系全靠管理员心算；要把第 20 位挪到第 2 位得先想清楚填几。
- 两个数字入口互相竞争：表格列一个输入框，新建/编辑弹窗里还有一个「排序」字段，两处都能改同一个值。
- 交互笨重：改完数字焦点离开才触发保存，整表刷新。

## 目标

- 表格行支持**拖拽排序**：抓手柄拖动、目标位置让位、松手落定即保存。
- 数字输入彻底移除（表格列与弹窗字段都删），拖拽 + 键盘是仅有的两种调序手段。
- 后端数据模型（`provider_instance.sort_order`）与公开面板排序逻辑不动。
- 零新增前端依赖，保持纯手写 vanilla JS 的项目风格（Docker 自托管、可离线部署）。

## 非目标

- 不改公开仪表盘（index.html）的任何排序/展示逻辑。
- 不做管理页其他列表（模型别名、价格）的拖拽。
- 不做多人并发编辑协同。
- 不做数据库迁移——复用现有 `sort_order` 列。

## 关键决策

1. **Pointer Events 零依赖自实现**，不用 HTML5 Drag&Drop API（幽灵图不可定制、触屏完全不支持），也不引入 SortableJS（45KB 第三方库换一张小表的拖拽，破坏零依赖与离线部署，不划算）。Pointer Events 同时覆盖鼠标与触屏，视觉反馈完全可控。
2. **「排序」列删除，拖抓手柄移到最左列**。拖拽操作放最左是通行惯例（手柄列窄、无表头文字）；原「排序」列位置不留空列。
3. **新增批量排序端点** `PUT /api/admin/instances/order`，一次提交全部实例的新顺序。不复用单行 `PUT /instances/{id}` 发 N 次请求：批量端点一个事务完成、只 bump 一次 `config_version`，且可做整体性校验。
4. **落盘即归一化**：批量保存时把 `sort_order` 重编号为 `0..n-1`。新建实例仍用默认 999 → 自动排末尾；首次拖拽后全体归一化。存量数据无需迁移（999 并列时按 `instance_id` 次序，行为不变）。
5. **严格校验请求集合**：`instance_ids` 必须与库中实例集合完全相等（不缺、不多、不重复），否则 422。拖拽只改变顺序不增删实例，集合不一致说明客户端状态过期，应拒绝并重取。
6. **弹窗「排序」字段移除，但 API 字段保留**。前端保存实例时透传 `inst ? inst.order : 999`；`InstancePayload.order` 不动，配置导入导出（`_STRUCTURAL_KEYS` 含 `order`）继续 round-trip。
7. **失败回滚可见**：保存失败 → `loadInstances()` 恢复服务端顺序 + 面板内短暂错误提示（仿 `alias-status`/`price-status` 的内联状态行，数秒后自动消失）。顺带修掉现状：旧 `saveOrder` 失败时静默吞错，用户无感知。
8. **键盘通道**作为数字输入移除后的精确调序替代：Tab 聚焦手柄，Space/Enter 拾起，↑/↓ 逐位移动，Space/Enter 落定保存，Esc 取消。

## 架构

```
拖拽/键盘落定
  → INSTANCES 数组乐观重排（UI 立即到位）
  → PUT /api/admin/instances/order  { instance_ids: [...] }
  → config_store.reorder_instances()：集合校验 → 事务内 UPDATE ×n → _bump_version()
  → 成功 → loadInstances() 与服务端对齐
  → 失败 → loadInstances() 回滚 + 内联错误提示
```

`config_version` bump 触发 `ConfigService` 热加载，与其他管理写操作路径一致；排序不影响用量逻辑，热加载是无害副作用。

## 交互设计

### 手柄

- 最左窄列（约 36px，无表头文字）。手柄为真实 `<button>`，字符 `⠿`，`aria-label="拖拽调整「{label}」的顺序"`。
- 默认灰淡，行 hover 时提亮；`cursor: grab`，按下后整页 `grabbing`；`touch-action: none` 保证触屏拖拽不被页面滚动抢走。

### 拖拽状态机（idle → lifted → dragging → settled）

| 阶段 | 行为 |
|---|---|
| 按下 | `pointerdown`（仅 primary 指针）记录起点；位移超 5px 视为拖拽开始，`setPointerCapture` 到手柄 |
| 提起 | 行进入 lifted：浮起阴影 + `scale(1.02)` + 强调边框色 + 顶层 `z-index`；整页 `user-select: none` |
| 拖动 | 行 `transform: translateY(dy)` 跟随指针；拖拽开始时缓存各行实际高度（报错行更高，行高不等） |
| 让位 | 指针越过某行中线时，该行以 150ms transition 平移让位，目标槽位始终可见 |
| 自动滚动 | 指针距视口上/下边缘约 60px 内时持续滚动 window |
| 落定 | 松手 → 行过渡动画落入目标槽位 → DOM 实际重排 + `INSTANCES` 重排 → 触发保存 |
| 取消 | 拖拽中按 Esc：行回原位，不发请求 |

- 拖动期间行始终留在原 DOM 位置，只动 transform；落定时才真正重排——回滚路径简单。
- 拖拽只能从手柄发起，「编辑/删除」按钮点击不受影响。

### 键盘通道

- 手柄可 Tab 聚焦。Space/Enter 切换拾起/落定；拾起后 ↑/↓ 逐槽移动（直接挪 DOM 节点 + 同步 `INSTANCES`，焦点保持在手柄上），行呈现 lifted 样式。
- 落定即保存（与拖拽同一保存函数）；Esc 取消并还原。
- 面板附近放视觉隐藏的 `aria-live="polite"` 区域，播报「已将『个人号』移至第 2 位」。

### 保存与失败处理

- 每次落定发一次批量请求；前端乐观更新，成功后 `loadInstances()` 对齐。
- 失败：`loadInstances()` 恢复服务端顺序，表头右侧内联状态行（`#instances-status`）显示错误文案（取 `api()` 抛出的 `detail`），约 5 秒后自动清空。

## API 设计

```
PUT /api/admin/instances/order
Body:     { "instance_ids": ["claude-x", "kimi-main", ...] }
Success:  200 { "ok": true }
422:      instance_ids 与库中实例集合不一致（缺失 / 未知 / 重复），detail 说明原因
401:      未登录（走 protected 路由，与其他管理端点一致）
```

校验错误文案：

- 重复：`"instance_ids 存在重复项"`
- 缺失/未知：`"instance_ids 必须与全部实例一致：缺少 [...]；未知 [...]"`（缺哪段带哪段）

**实现注意**：该路由必须声明在 `PUT /instances/{instance_id}` **之前**，否则 FastAPI 会把 `order` 当成 `instance_id` 解析（`order` 满足 `[A-Za-z0-9._-]+`），走到 update 逻辑返回 404。

## 数据层

`config_store.py` 新增：

```python
def reorder_instances(conn: sqlite3.Connection, instance_ids: list[str]) -> None:
    """按给定顺序把 sort_order 重编号为 0..n-1，单事务。

    校验 instance_ids 与 provider_instance 全表集合严格相等（不缺、不多、
    不重复），不一致抛 ValueError。成功则逐行 UPDATE sort_order 与
    updated_at，最后 _bump_version() 一次并 commit。
    """
```

- 逐行 `UPDATE ... SET sort_order = ?, updated_at = ? WHERE instance_id = ?`（n 通常个位数，无需批量 SQL 技巧）。
- 任一步失败整体 rollback（沿用模块内 `conn.rollback()` 惯例）。
- 新建实例路径不动：`create_instance` 仍写默认 999。

## 前端改动清单（admin.html）

1. **thead**：`<th>排序</th>` 删除，行首插入无文字手柄列。
2. **renderInstances() 行模板**：数字输入单元格换成手柄按钮（`data-handle-for="{id}"`），作为首个 `<td>`。
3. **删除**：`saveOrder()` 函数、`data-order-for` 事件接线。
4. **新增**：拖拽模块（指针 + 键盘 + 自动滚动，约 120 行）、`saveInstanceOrder()`（批量 PUT + 失败回滚）、`#instances-status` 内联状态行、视觉隐藏的 `aria-live` 区域、配套 CSS（`.drag-handle`、lifted/让位/落定状态、状态行）。
5. **弹窗**：删除「排序」label + `#modal-order` 输入框；`openInstanceModal()` 去掉对应赋值；`saveInstance()` 的 `order` 改为 `inst ? inst.order : 999`。

## 测试

### tests/test_admin_api.py（新增 TestReorderInstances）

- `test_reorder_persists_new_order`：造 3 个实例，PUT 逆序列表 → GET 返回逆序；直查 DB 确认 `sort_order` 为 0、1、2。
- `test_reorder_rejects_missing_ids` / `unknown_ids` / `duplicates`：各返回 422。
- `test_reorder_includes_disabled`：停用实例也在列表里时正常重排。
- `test_reorder_requires_auth`：未登录 401。
- 回归：现有 create/update 用例带 `order` 字段，API 未改，应全绿。

### tests/test_admin_page.py（静态断言）

- `test_numeric_order_inputs_removed`：admin.html 不含 `data-order-for`、`modal-order`。
- `test_drag_reorder_wired`：含 `drag-handle`、`data-handle-for`、`instances/order`、`aria-live`。

## 兼容性

- **数据库**：无迁移，`sort_order` 列复用。
- **配置导入导出**：`order` 继续在导出 JSON 中 round-trip，导入逻辑不变。
- **API**：`POST/PUT /instances` 载荷不变；新增端点纯增量。
- **公开面板**：排序依据不变（DB 层 `sort_order, instance_id`，运行时 `get_usage()` 再按 `(order, title, id)` 合并排序），两处消费的都是同一个 order 值，管理页拖拽结果直接生效。
- **存量数据**：首次拖拽前行为完全不变（含 999 并列按 ID 排）；首次拖拽后归一化为 0..n-1。
