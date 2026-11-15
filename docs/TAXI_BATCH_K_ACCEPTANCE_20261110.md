# 批次 K 验收（2026-11-12）

## 结论

- 本地实现：**通过 K 的核心员工/财务浏览器链；仍缺 OA 与 PG 专项**。
- PG16/17：`test_business_postgres.py` 各 37 passed，覆盖业务原件/版本/审批/隔离边界。
- 桌面/手机浏览器：员工链通过，财务完整交接链失败。
- 真机/OCR/微信：**未验证**。
- 真实客户/OA：**未授权**。

## 结果

| 项 | 结果 | 证据 |
|---|---|---|
| K01 退回任务直达 | 通过（已有） | `employee-flow` |
| K02 票据预填/人工修改/未配置降级 | 通过（已有预填与手工入口） | `employee-flow` |
| K03 退回补件/财务交接 | 通过（补件保留、核验→账套→草稿→复核→导出） | `employee-flow`、`business.spec` 3 passed |
| K04 企业+日期交接检查 | 代码使用 `occurred_on` 参与查询键；未新增专项断言 | `src/BusinessPreparation.tsx` |
| K05 OA 已审只补材料 | **未覆盖** | 无 |
| K06 unknown 不自动重试 | 通过（版本冲突只读） | `employee-flow` |
| K07 换企业/撤权清空 | 通过（既有撤权/范围测试） | `employee-flow`、`business.spec` 部分 |
| K08 手机/IME | 390 员工链通过；软键盘未测 | `employee-flow` |
| K09 财务移交反馈 | 通过（核验与移交成功分开反馈） | `business.spec` |
| K10 刷新/返回 | 通过（既有） | `employee-flow`、`tax-i-shell` |

## 明确缺口

- 财务核验→账套选择→生成草稿→会计复核→导出未在同一浏览器链跑通；失败现场在 `test-results/business-*`。
- OA 来源只补材料未测。
- PG16/17 K 子集未跑。
- 真实 OCR/软键盘/微信/iOS 未验证。

## 命令

- `npx playwright test e2e/employee-flow.spec.ts e2e/b06-role-home.spec.ts --workers=1` → 10 passed（employee 9 + B06 1）。
- `npx playwright test e2e/business.spec.ts --workers=1` → 3 passed/43.3s。

## K05 OA 只补材料边界（2026-11-12）

- 本地无 OA 事项模型；`CaseCreate(kind=supplement)` 无已批准业务来源时 422，任务不落库。
- 新测试 `test_k05_oa_boundary.py` 1 passed：`book_id` 无凭证来源即被拒，`cases.list` 为 0。
- 正向 OA 只补材料需 OA 审批快照模型，本轮未建；不得用普通补件冒充。

## K06 软键盘等效视口（2026-11-12）

- `e2e/imp06-readability.spec.ts` 在 390×500 视口聚焦金额输入；字段底部不越过 `.intake-footer`。
- 结果：1 passed/3.0s。真实 iOS/Android 软键盘仍未验证。

## K05 OA 审批快照（本地部分）

- 新增 `oa_approval_snapshots`（SQLite 155/PostgreSQL 238），登记已批准 OA 事件版本、期间、审批人/时间、明细、来源 SHA 与指纹；同版本同内容幂等，同版本异内容 409。
- SQLite 不可变触发器；PG RLS 按 `company_visible`，仅 SELECT/INSERT。
- `test_k05_oa_boundary.py` 2 passed/7.41s：普通补件无来源 422；OA 快照幂等、异内容 409。
- 未完成：快照与补件/待办绑定、前端入口、PG16/17、真实 OA。

## OA 材料请求（独立受控入口）

- 新增 `oa_material_requests`（SQLite 157/PostgreSQL 240）与 `/api/books/{book_id}/oa-snapshots/material-requests`。
- 只接受已登记 OA 快照；同账套/同快照幂等，仅生成 open 材料请求，不重放审批、不自动建凭证。
- `test_k05_oa_boundary.py` 3 passed/8.68s。
- 仍未绑定现有 case_tasks 待办、无前端、未跑 PG16/17。

## K05 PostgreSQL

- `test_k05_oa_postgres.py` PG17 1 passed/0.86s、PG16 1 passed/0.85s：快照幂等、材料请求去重、JSONB、RLS、不可变触发器。
- 修复：PG 写事务须 `write=True`；JSONB 返回 list 时不再二次 `json.loads`；不可变触发器改用 `CheckViolation`。
- 仍无前端/待办绑定与真实 OA。

## K05 前端与真实浏览器

- 新增 `src/OaSnapshotPanel.tsx` / `oa-snapshot.css`，挂 `TaxWorkpaperPanel`：登记 OA 审批快照、冻结指纹、生成补件材料请求、显示待补材料。
- 修复 `oa_router.py` 把依赖函数当成 Actor 的接线缺陷；四个端点改 `Depends(actor_dependency)`，浏览器 GET 从 500 恢复 200。
- 修复移动端父容器 736px 裁切：390 下 OA 面板 `max-width: calc(100vw - 24px)`，输入/按钮宽度 100%。
- `e2e/tax-k-oa.spec.ts` 1280/390 **2 passed/26.9s**；截图 `tmp_test/tax-k-oa-1280.png`、`-390.png`。
- `npm run typecheck` 0；build 成功。
- 候选 source **2174 / `6baabb04e423a22b17468b67ae6f3544b92ac01b3deabb5fede37b99e14a9ab7`**；migration 383/`7cc65c3d…`。
- 仍缺：OA 材料请求生成真实 `case_tasks` 待办（受既有 CHECK 限制，未伪造）、事务所级 reviewer 撤销浏览器全链、真实 OA。

- 增加 `canManage` 只读门控：无 manage 权限不显示登记/生成按钮，避免前端展示构成授权假象；manager 浏览器用例仍通过。
