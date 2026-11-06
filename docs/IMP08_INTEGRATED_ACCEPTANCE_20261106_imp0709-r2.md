# IMP08 整合验收（2026-11-06 / imp0709-r2）

状态：本地后端/HTTP/UI 通过；PG16/17 仅无上期 smoke；完整动态授权与三类正向 PG 仍待补。真实 API 浏览器成功链已通过。

## 已有能力复用

- `app/assistant/analysis.py` 费用跨期、票据凭证差异
- `app/assistant/investigation.py` 交付三步只读调查
- `app/assistant/router.py` capability + 分析入口
- `src/AnalysisEvidencePanel.tsx`、`BookWorkspaceShell`、现有任务/接手/证据组件
- 既有 handoff schema、执行记录、ContextPack、分页与受限 PG 运行器

## 本轮增量

- 费用跨期 coverage 区分全量总额与当前明细页：`total_rows`、`detail_rows`、`detail_complete`、`has_more`；任一期分页即 partial。
- 票据差异总额改用全部 `voucher_sources` 聚合，当前页仅作明细；新增 `source_detail_total`、`voucher_credit`、`voucher_imbalanced`；借贷不平显式 partial + `voucher_imbalance`。
- 结构化 handoff：`reason_code`、`owner_role`、`source_ref`、`source_version`、`action_target`、`deadline=null`、`status=open`；不编造负责人/期限。
- UI 展示总额/明细行数、结构化 owner、使用 `action_target` 打开原记录；仍无写操作。
- 新增纯只读 `app/assistant/observation.py`：把既有分析/handoff 投影为 `would_handoff`/`would_notify=false`；撤权、预算耗尽、unknown、complete 分别安全处理；`ObservationLedger` 同 ID 幂等、异内容冲突。无业务写入、无外发。

## 证据

- `test_imp08_analysis.py` **13 passed / 29.06s**（新增“无本期记录不得声称有记录”）
- `test_imp08_observation.py` **6 passed / 0.11s**
- 组合 `test_imp08_analysis + test_imp08_observation + assistant_task_workflow + assistant_task_baseline` → **46 passed / 47.95s**
- JUnit：`tmp_test/imp-integrated/imp0709-20261106-r2/imp08-tests.xml`、`imp08-analysis-final.xml`
- PG17 smoke：**1 passed / 3.90s**（`ak05-pg-runs/2dec49cb…`）
- PG16 smoke：**1 passed / 2.54s**（`ak05-pg-runs/aa8819f5…`）
- 浏览器：`npx playwright test --config=playwright.imp08.config.ts` → **2 passed / 13.9s**：一条 mock 固定事实，一条真实登录/页面/真实 API（服务端开 `ACCOUNTING_ASSISTANT_INVESTIGATION=1`），两条均监听写请求为 0。
- 截图：`tmp_test/imp-integrated/imp0709-20261106-r2/imp08-analysis-real.png`
- 真实 API 缺陷修复：本期无该科目记录时，摘要原写“有记录”，已改为“无记录”；新测试固定该文案。

## 08H / 08S 对照（诚实状态）

- 08H-T01/T02/T05/T07/T08：类型/注入/跨企业/分页/零模型调用已有本地测试。
- 08H-T03：撤权后 fail-closed 有 SQLite 测试；PG 动态交错未做。
- 08H-T04/T06/T10/T11：租约/嵌套快照/迟到 UI/unknown 重发未在本轮形成独立动态证据。
- 08S-T01/T02/T03/T04/T08：结构化 handoff、来源/责任、无编造负责人有测试；真实任务卡投影与分页计数仍复用既有组件，未新增全站改造。
- 08S-T05/T06/T07：observation 只读幂等/冲突/撤权/预算/unknown 已有纯函数测试；不是生产订阅，无外发副作用。
- 08S-T09/T10：桌面 UI 接线 1 passed；手机与完整“发现→补件→返回→重查”链路未在本包新增。

## 未完成 / 外部

- 三类成功路径的 PG16/17 正向证据（当前只有无上期 partial smoke）。
- 浏览器真实 API 成功链已通过（2 passed）；动态撤权/旧租约交错仍未完成。
- 观察模式接入真实事件订阅/持久游标：本轮只做隔离纯投影，不接生产。
- 真实模型/通知/真人：pending。

## PG16/17 正向补验（2026-11-06）

- PG17：费用 `partial`，`current_total=268.10`、明细 1 行、`missing=[previous_period]`、fingerprint `6aca51fc…`；票据 `complete`，`source_total=268.10`、`difference=0.00`、明细 1 行、fingerprint `31a11ee6…`。
- PG16：费用 `partial`，`current_total=268.10`、明细 1 行、fingerprint `6ad178b3…`；票据 `complete`，`source_total=268.10`、`difference=0.00`、fingerprint `a01480ec…`。
- 数据来自现有真实 PG 库的已复核凭证；使用真实 app 连接与 Actor，不绕过 `AccessControl`。证据：`tmp_test/imp-integrated/imp0709-20261106-r2/imp08-pg-positive.json`、`imp08-pg-positive16.json`。
- 仍缺：运行中撤权/旧租约交错、手机真实成功链；不得把本结果写成动态权限并发已验。

## 撤权不缓存补测（2026-11-06）

- 新增 `test_revocation_is_not_cached_between_analyses`：先用 accountant 成功读取，删除其 `book_access` 后再调用，必须拒绝（403/404），不能复用上次授权；测试通过，IMP08 分析文件 **13 passed/35.99s**。
- 该测试覆盖“两次调用之间撤权”，不是同一调用内的时间竞争；旧租约/并发交错仍未做。

## 手机真实 API 成功链（2026-11-06）

- `tmp_test/imp-integrated/imp0709-20261106-r2/imp08-analysis-mobile.spec.ts` 使用 390×844、真实登录/页面/真实 API，打开只读差异核对并提交分析；结果区含 6602.02、总额/明细、证据摘要，PUT/PATCH/DELETE 为 0；**1 passed**。
- 该脚本放在候选扫描范围外的 `tmp_test/`，不改变最终候选；桌面真实 API 2 passed + mock 1 passed + 手机 1 passed，共 3 个 Playwright 用例（手机脚本另计）。

## 同一调用内撤权复核（2026-11-06）

- 新增 `test_revocation_after_initial_authorization_is_rechecked_inside_the_read`：初始授权通过后立即删除 `book_access`，分析事务内再次校验必须失败。
- 红测先 `DID NOT RAISE`；`analysis._expense_bridge` / `_invoice_voucher_diff` 在读取前后调用 `_recheck_book` 后通过。
- `test_imp08_analysis.py + test_imp08_observation.py` **20 passed / 38.57s**（`imp08-final-tests.xml`）。
- 仍未覆盖“旧租约在两次校验之间完成读取”的极端交错；不夸大。
