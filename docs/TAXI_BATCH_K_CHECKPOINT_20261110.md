# 批次 K 检查点（2026-11-12）

## 工程状态

- 本地实现：**K1/K2/K3/K6/K7/K9/K10 浏览器链通过；PG16/17 各 37 passed；仍缺 OA 只补材料、软键盘、真实 OCR**。
- PG16/17：未新增 K 专项。
- 浏览器：员工链、角色首页通过；财务完整交接链失败。
- 外部验收：不适用。

## 已完成/已回归

1. K1 员工待办与首屏：`employee-flow` 9 passed（1280/390：首屏新建、清单展开、票据预填核对、退回修改、补件撤权、版本冲突只读、自动保存一次）。
2. K02 票据预填与人工修改：`employee-flow` 票据预填用例 passed；未配置 OCR 时手工入口可用。
3. K03 退回补件：`employee-flow` 退回原因直达、补件保留原事项 passed。
4. K06 unknown/重复提交：既有 employee-flow 断言（版本冲突只能读取，不自动重试）passed。
5. K08 手机：390 员工用例 passed；未测软键盘。
6. K10 返回/刷新：`employee-flow` 回原事项、`tax-i-shell` 返回位置 passed。
7. 角色边界：`b06-role-home` passed；员工不显示会计导航。

## 失败与修复

- `e2e/business.spec.ts --grep 员工报销经过独立审批` 最初失败：`getByRole('status')` 同时匹配字段帮助与业务通知，strict mode violation；页面实际已显示“财务核验已通过”。
- 这是夹具选择器缺陷，不是产品悬挂；改 `page.locator('.business-notice')` 后完整链通过。
- `npx playwright test e2e/business.spec.ts --workers=1` → **3 passed/43.3s**（含财务交接→草稿→会计复核→导出、退回重提、窄屏）。

## 下一步

- 用真实 API 复现财务核验后的 `GET /business/events/{id}` / `GET /companies/{id}/books?period=...` 是否悬挂；检查是否请求被 abort 后加载态未清除。
- 增加 OA 已审事项夹具与“只补材料不重复审批”用例。
- 跑 PG16/17 授权/附件/版本/复核子集。
- 完成后再进入 L 的跨页交互；K 当前不得称收口。

## 2026-11-15 OA 快照检查点

- 新增 `nexus_backend/app/cases/oa_snapshots.py`、`oa_router.py`、SQLite 155/PostgreSQL 238；`GET/POST /api/books/{book_id}/oa-snapshots`。
- `test_k05_oa_boundary.py` 2 passed/7.41s；`import app.main` 通过。
- 快照未绑定补件/待办，未跑 PG16/17 与浏览器；不得写成 OA 正向链完成。
- 候选 source 2169/`a0053ce00a5e1837eaade1cf1afcb3aa8f0d7bd48de7f2a6ef4e006ac5a63e34`、migration 383/`7cc65c3d8e83145ccf10729216b697b046cac4a85a2ae86ab35d5f50af8758ec`。
