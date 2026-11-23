# PLT 执行状态

追加不覆盖。每个模块只在其验收命令真实通过后写“完成”；无 PG/浏览器/客户资料时如实写“未验证”。

## PLT00 执行纪律、责任与计量基线  <状态：工具就绪待客户输入（本机门禁/模板/合成演练通过；真实工时、客户资料、责任人签认未提供）>

- 现状核实：HEAD `074c7ad1f80797f3a194632ebf2c4d224a3882c7`；工作区 120+ 项改动；迁移最高 308（PG 304/305/307/308 尚未在 PG16/17 应用验证）。PLT01–PLT04 已记录（PLT04-D 本地通过；E 工具就绪待客户），PLT05-A 本地通过（真实 T+ 未联调），PLT05-B 契约/解析器/PG/浏览器/手工证据通过；PLT05-C 返回差异接入统一待办与 handler/手工浏览器 e2e、独立 HTTP 成功路径通过（PG 303 未应用验证）；PLT05-D 六项缺口补强本机 SQLite/浏览器/HTTP/前端门禁通过（PG 304/305/307/308 与 RLS 用例未实跑），PLT05-E–PLT09 尚未执行。
- 当前联合候选：source **2411/`39f21b945a4da9914fb02d5b9fc753b2e02984fc19cdef6bbadcc0977888f374`**、migration **514/`046d9ac0baf39faf734145c9030b171e093263b749ba1f88786db436d8b76533`**；各模块仍保留其通过时的历史候选。
- 修改文件（新增）：
  - `scripts/opt_acceptance.py`：`release_blockers(results, *, require_pg)` 与 `--require-pg`；PG skipped/passed-with-skips/skipped_tests 均列 blocker，release status 置 failed。
  - `nexus_backend/tests/test_plt00_release_gate.py`。
  - `scripts/plt09_measurement.py`、`nexus_backend/tests/test_plt09_measurement.py`。
  - `scripts/plt09_synthetic_drill.py`、`docs/pilot/templates/measurement_synthetic_drill.csv/.json`、`docs/pilot/MEASUREMENT_SYNTHETIC_DRILL.md`。
  - `docs/pilot/templates/measurement_baseline.csv`。
  - `docs/pilot/SCORECARD.md`、`BASELINE_KIT.md`、`RESPONSIBILITY_MATRIX.md`、`CUSTOMER_INPUTS.md`、`METRICS_AUDIT.md`。
- 门禁红测与修复：`pytest tests/test_plt00_release_gate.py -q` 首次 3 failed（`AttributeError: ... has no attribute 'release_blockers'`），实现后 **4 passed/0.35s**；新增零用例用例确认 `_classify(0, "no tests ran in 0.01s") == "zero-tests"`，不会被当作 passed。
- 实际 release 门禁：
  ```
  env -u ACCOUNTING_TEST_POSTGRES_URL python scripts/opt_acceptance.py --require-pg \
    --out tmp_test/pilot/PLT00-D/release-pg-skip-100256
  ```
  退出码 **1**；`status=failed`；7 个 PG 命令全部 `skipped`；`release_blockers` 列出 OPT02/03/04/05/06/07/10；skip delta **8**；untracked migrations **8**。证据目录 `tmp_test/pilot/PLT00-D/release-pg-skip-100256/`。
- 计量模板：`measurement_baseline.csv` 为 5 公司 × 15 岗位 × 2 期间 = 150 行空值；`plt09_measurement.py` 区分空值/已知零，**3 passed/0.36s**。
- 包 00 定向组合：`test_plt00_release_gate.py + test_plt09_measurement.py + test_value_baseline.py` → **10 passed/13.46s**；覆盖 release gate、零用例、空白/已知零、跨期 cohort。
- 合成计量演练：`python scripts/plt09_synthetic_drill.py` → 15 行；`unknown_rows=0/partial_rows=0/complete_rows=15/measured_zeroes=113`。全部数字虚构，文件头与 JSON `notice` 明确“合成演练，禁止作为客户基线、人效或定编依据”；只证明空值/零/已采集可区分，不产生任何客户工时结论。
- 证据边界：`opt_acceptance.py` 的 source/migration 摘要与 run manifest 在同一次运行内生成，因此不会拼接不同指纹；命令零用例返回 `zero-tests` 并强制 failed；截图不是该命令的通过证据，未纳入门禁，避免把旧截图当作当前候选证据。
- 价值基线 cohort 修复：`mapped` 按 event_month 与 mapping_month、`returned/return_actions` 按 event_month 与 action_month；`cost.ai_model_cost_complete=cost_unknown_count==0`。`test_value_baseline.py` **3 passed/13.62s**，新增跨期用例确认 2026-09 事项的 2026-10 映射/退回不会混入 10 月队列。
- 未验证/外部输入：
  - 五家客户 15 项岗位真实工时、质量、业务量：全部空值，待客户/现场两期数据。
  - `RESPONSIBILITY_MATRIX.md` 责任人、签认均为空；出纳、非 review 办理人时间、集团合并等尚无系统落点。
  - 真实客户资料清单仅模板，未收到客户文件。
- 顺带发现：`pytest -k "value or metric or insight"` 为 **1 failed,78 passed,14 skipped**；唯一失败是既有 `test_stage7_pagination.py` 的“制单人不能复核本人版本”403，不是本包 cohort 改动，未修改。
- 未提交/推送/部署。
- 本轮结束候选：HEAD `c8da023777fff5c7eeb7311067cdcd7e8244e61b`、source **2465/`af4472d2277d6ca54d37a544f3017e61437fe59768ae2e924f0c4243d1613a24`**、migration **495/`7272abe6403b61632d5145ae1f30145ed09658f4466bc206d1fea65495e6f633`**。

## PLT01-A  <状态：完成>

- 现状核实：`rg -n '^## |^### ' docs/fsc_opt/OPT_STATUS.md` → 1274 行，OPT01–OPT10 共 39 个状态节；OPT04-B 有重复节（行 431、478）；OPT07-D 有多段历史节（行 1089/1104 被行 1114/1133 取代）；OPT11 无记录。
- 盘点结果：`docs/pilot/OPT_CARRYOVER.md`。逐项列出最后状态、缺失内容、缺口类型（代码/测试/PG/人工）与重复节。
- OPT08 A–E 代码核对：`src/money.ts`、`src/MoneyCell.tsx`、`src/account-picker.ts`、`src/AccountPicker.tsx` 均存在；`src/VoucherReviewer.tsx:364`、`src/InlineReviewDrawer.tsx:868` 接线 `onAddLine`；`src/JournalEntryGrid.tsx:205-213` 处理 Alt+Enter/Ctrl+Enter，删除后回焦在 `removeLine`；stage7/tax05 浏览器用例存在。
- 修改文件：`docs/pilot/OPT_CARRYOVER.md`（新增）。
- 验收命令与输出：
  ```
  rg -n '^## |^### ' docs/fsc_opt/OPT_STATUS.md
  （39 个状态节；OPT11 无记录）
  ```
  ```
  ls src/money.ts src/MoneyCell.tsx src/AccountPicker.tsx
  src/AccountPicker.tsx  src/MoneyCell.tsx  src/money.ts
  ```
- 仅合成验证的部分：无（本模块只读）。
- 需客户或人提供：无。
- 未做或未验证：未逐条重跑 OPT 原始命令；PLT01-B/C/D 负责。
- 顺带发现：OPT02-C、OPT06-D 的状态只写在 `###` 节，缺少统一的 `## ... <状态>` 标题，机器汇总时容易漏读；已在 carryover 中按最后记录取状态。

## PLT01-B / OPT01-C  <状态：完成>

- 现状核实：`app/tax/basis.py:124-125` 唯一金额取整入口为 `scale_by_basis_points`；`app/tax/workpaper.py:39`、`app/tax/estimate.py` 均委托该函数；`estimate.py` 五处显式 `ROUND_HALF_UP`。业务代码无 `// 10000` 金额截断。
- 修改文件：无（代码已符合，未为过测改实现）。
- 验收命令与输出：
  ```
  cd nexus_backend && PYTHONPATH=.. python -m pytest tests/test_tax_rounding_consistency.py tests/test_tax14_basis.py tests/test_tax_estimate.py -q
  12 passed in 11.09s
  ```
- 仅合成验证：是，税额/计税基础均为合成整数分。
- 需客户或人提供：无。
- 未做或未验证：`-k tax` 其余 10 个失败不在取整路径，已记入 PLT01-D 验收包；未在本条修改。
- 顺带发现：`TaxPolicyItemService.calculate` 也调用同一函数，未另造取整。

## PLT01-B / OPT05-E  <状态：完成（PG16/17 真实幂等发布；不改产品逻辑）>

- 现状核实：`CloseService.publish` 对 `status=published` 直接返回 `_view`，不重复发布报表/写审计；锁期内首次发布使用报表版本并在第二段事务 CAS 月结状态。
- 修改文件：新增 `nexus_backend/tests/test_opt05_close_publish_postgres.py`（只测试，不改 `publish`）。
- 测试构造：复用 `test_monthly_delivery_postgres` 的 `p0_case`，通过 `SubledgerService.set_control_applicability` 补齐当前子账适用性；`_reviewed_close` 只走真实服务建账、期初、报表复核、月结预检/复核，然后 `lock` → `publish` → 同版本重试。
- 断言：两次 `publish` 均为 `published`、`published_by` 不变、版本不变；`audit_events action='close.publish'` 恰 1 条；`report_runs.status='published'` 恰 1 条。
- 验收命令与输出（PG16）：
  ```
  python scripts/test_accounting_postgres_isolated.py --config tmp_test/fsc02-pg/opt05-pg16.json nexus_backend/tests/test_opt05_close_publish_postgres.py -q
  1 passed in 2.69s
  ```
- 验收命令与输出（PG17）：
  ```
  python scripts/test_accounting_postgres_isolated.py --config tmp_test/fsc02-pg/opt05-pg17.json nexus_backend/tests/test_opt05_close_publish_postgres.py -q
  1 passed in 2.52s
  ```
- 仅合成验证：是；新库、虚构事务所/客户/期初，无真实财务数据。
- 需客户或人提供：无（真实生产库恢复仍属 PLT01-D 外部门槛）。
- 未做或未验证：未测真实网络响应丢失/进程强杀后的 HTTP 重试；本轮验证的是产品发布边界在已提交 `published` 后的幂等路径。
- 顺带发现：`reports.publish` 不写 `report.publish` 审计（仅月结写 `close.publish`）；本轮未改，记录供 PLT01-D/审计边界复核，未把它当作通过条件。

## PLT01-B / OPT07-E  <状态：完成（缺条目阻断；不回退单一税率）>

- 现状核实：`TaxPolicyItemService.calculate` 无条目时 `missing` 包含所有有金额的计税基础，返回 409“缺少税费政策条目：…”，不使用档案 `vat_rate_bp` 作为默认。
- 修改文件：`nexus_backend/tests/test_opt07_tax_policy_items.py` 新增 `test_missing_all_policy_items_blocks_without_single_rate_default`；产品代码未改。
- 验收命令与输出：
  ```
  cd nexus_backend && PYTHONPATH=.. python -m pytest tests/test_opt07_tax_policy_items.py -q
  4 passed in 11.99s
  ```
- 仅合成验证：是；税务档案和计税基础均为合成数据。
- 需客户或人提供：真实税种、税率、优惠/附加税/预缴/印花税政策仍未提供；不推断行业政策。
- 未做或未验证：多税率按票面/业务归集、附加税/预缴/印花税、前端税费条目编辑仍为 OPT07-E 未完成部分（已记入 carryover）；本条只关闭“缺政策阻断/无默认值”。
- 顺带发现：底稿 `_policy_snapshot` 在无条目时仍走历史单一税率路径；本次阻断验证作用在 `TaxPolicyItemService.calculate`。本轮未改底稿兼容路径，避免破坏 N04 历史契约；该差异已列入 PLT01-D 待复核项。

## PLT01-B / OPT07-F 通用别名前端  <状态：完成>

- 现状核实：`AliasService.list` 已按公司/类型返回主体名称；API `GET /api/companies/{company_id}/aliases` 已接；`SubjectAliasPanel` 只管理 `counterparty`，展示生效/停用、来源、依据和生效期；`LedgerWorkspace` 已挂在运输主数据之后。
- 修改文件（本任务新增）：`nexus_backend/app/aliases.py`（list）、`nexus_backend/app/alias_router.py`（GET）、`src/SubjectAliasPanel.tsx`、`src/LedgerWorkspace.tsx`（挂载）、`src/subject-alias-panel.render.test.ts`、`e2e/opt07-subject-alias-ui.spec.ts`；样式复用 `src/ledger.css` 既有 `.ledger-alias-form`/`.ledger-alias-list`。
- 验收命令与输出：
  ```
  cd nexus_backend && PYTHONPATH=.. python -m pytest tests/test_opt07_subject_aliases.py -q
  5 passed in 14.28s
  ```
  ```
  cd nexus_backend && PYTHONPATH=.. python -m pytest tests/test_opt07_alias_http.py -q
  2 passed in 11.51s
  ```
  ```
  npx vitest run src/subject-alias-panel.render.test.ts
  1 passed
  ```
  ```
  npx playwright test e2e/opt07-subject-alias-ui.spec.ts --reporter=line
  1 passed (15.7s)
  ```
  ```
  npm run typecheck
  （通过）
  ```
- 浏览器证据：`artifacts/opt07-subject-alias-1366.png`、`artifacts/opt07-subject-alias-390.png`；390 无横向溢出；创建虚构客商→登记→显示→停用全链路。
- 仅合成验证：是；虚构客商“虚构客商界面”、别名“客商旧称-UI”。
- 需客户或人提供：无。
- 未做或未验证：前端只在 Chromium 1366/390 验证；Firefox/WebKit/读屏未验。
- 顺带发现：测试中 `selectOption` 不接受正则 label；`已停用` 需要用精确文本，已修正测试。

## PLT01-B / OPT08 金额守卫  <状态：完成>

- 现状核实：`src/money.test.ts` 含源码扫描守卫，遍历 `src/`，白名单只允许 `money.test.ts` 与 `visual-pages.test.ts`；禁止 `parseFloat(` 和 `Math.round(Number(...) * 100)` 的金额写法。
- 修改文件：无。
- 验收命令与输出：
  ```
  npx vitest run src/money.test.ts
  8 passed
  ```
- 仅合成验证：是；随机 BigInt 与静态源码扫描。
- 需客户或人提供：无。
- 未做或未验证：无（本守卫只覆盖 `src/` 金额写法，不替代服务端金额校验）。

## PLT01-B  <状态：完成>

- 五项本地遗留均已按原 OPT 验收命令通过：OPT01-C、OPT05-E（PG16/17）、OPT07-E 缺条目、OPT07-F 前端、OPT08 守卫。
- 未修改任何历史迁移/checksum；未提交/推送/部署。
- 仍需人工/远端：真实税务政策、真实 PG16 产品恢复、跨浏览器/读屏、`-k tax` 其余失败、完整同候选验收包 → PLT01-C/D 与外部门槛。

## PLT01-C / OPT09、OPT10  <状态：部分完成（本地通过；OPT10 PG16 产品恢复/多进程限流为外部门槛）>

- 现状核实：`docs/fsc_opt/OPT_STATUS.md` 已记录 OPT09 本地通过、OPT10 本地大部分通过；本轮在 PLT01-B 改动后重跑定向命令确认未回归。
- 修改文件：无（沿用 OPT09/10 已提交到工作区的实现与测试）。
- 验收命令与输出：
  ```
  cd nexus_backend && PYTHONPATH=.. python -m pytest tests/test_opt09_review_risk.py tests/test_batch_review.py tests/test_opt09_annual_matrix.py tests/test_opt10_backup_encryption.py tests/test_opt10_sensitive_redaction.py tests/test_opt10_rate_limit.py tests/test_opt10_rate_limit_app.py tests/test_admin_workspace.py -q
  58 passed, 2633 warnings in 42.13s
  ```
  ```
  npx playwright test e2e/opt09-review-queue.spec.ts e2e/opt09-annual-matrix.spec.ts e2e/opt10-admin-sensitive.spec.ts --reporter=line
  3 passed (19.5s)
  ```
- 仅合成验证：是；虚构公司、合成税号、合成备份/原件、隔离 PG16/17。
- 需客户或人提供：真实设备/读屏、多进程部署与共享计数、PG16 对应版本客户端、真实恢复演练窗口。
- 未做或未验证：PG16 产品 `restore()` 仍被本机 PG17 `pg_restore` 的 `transaction_timeout` 阻断；多进程限流仍是单进程近似；Firefox/WebKit 未跑。
- 顺带发现：`test_opt09_annual_matrix.py` 的 12 月右边界断言依赖 1366 容器；已在 e2e 中保留，未用截图代替断言。

## PLT01-D / 同候选验收包  <状态：完成（本地脚本与交接；PG 未配置为明确跳过）>

- 现状核实：仓库此前无 `scripts/opt_acceptance.py`；新增只读验收脚本与交接文档，不修改产品代码、数据库、迁移或历史 checksum。
- 修改文件（本任务新增）：`scripts/opt_acceptance.py`、`docs/pilot/OPT_ACCEPTANCE_HANDOFF.md`。
- 验收命令与输出：
  ```
  python scripts/opt_acceptance.py --sqlite tmp_test/pilot/PLT01-D/audit/accounting.db --out tmp_test/pilot/PLT01-D/run-12
  status: passed
  source: 2326 files, fb0d270984e070caa6c92010fb58fd7f2576acec4c8d3d4725f329648fe2a43c
  migration: 483 files, ac77fb05ab2718754c5a7872f6c2f17be2e514a90fdca967f72375be025fc6b3
  modified tracked migrations: none
  untracked migrations: 0
  audit chain: passed
  ```
  ```
  python scripts/opt_acceptance.py --sqlite tmp_test/pilot/PLT01-D/audit/accounting.db --out tmp_test/pilot/PLT01-D/run-13
  （同一稳定字段；source/migration/命令状态/边界一致）
  ```
- 汇总（run-12）：OPT01 1/1、OPT02 1/1（PG 跳过 1）、OPT03 1/1（PG 跳过 1，测试跳过 3）、OPT04 1/1（PG 跳过 1）、OPT05 1/1（PG 跳过 1）、OPT06 1/1（PG 跳过 1）、OPT07 1/1（PG 跳过 1）、OPT08 1/1、OPT09 1/1、OPT10 1/1（PG 跳过 1）。失败 0，零用例 0。
- 边界：已跟踪迁移修改无、未跟踪迁移 0；skip 标记 137 vs 基线 129（+8），新增均为隔离 PG `skipif`（OPT03/04/05/06 测试文件），无新增 `xfail`；审计链在空迁移 SQLite 库上 exit 0，仅证明验证器/空链，不等同有业务数据链。
- 重复性：run-12 与 run-13 排除 run_id/时间/耗时/tail 后稳定字段一致；`tmp_test/pilot/PLT01-D/repeatability.json`。
- 仅合成验证：是；PG 命令因未设置 `ACCOUNTING_TEST_POSTGRES_URL` 全部明确跳过，未计通过。
- 需客户或人提供：隔离 PG DSN 以执行 7 条 PG 命令；有业务审计数据的库以验证真实链；推送后 CI/分支保护。
- 未做或未验证：PG 子集、有数据审计链、远端 CI；脚本不是发布门禁替代品。
- 顺带发现：`src/` 侧 FE01 共享预算 `homeCss 145755 > 145000` 仍阻断 FE01；全后端 `-k tax` 仍有 10 failed（4 个 Starlette 0.35 环境不兼容、6 个真实夹具/产品问题），本包不宣称全后端全绿。

## PLT01 总状态  <状态：部分完成>

- 完成：PLT01-A 遗留盘点、PLT01-B 五项本地收尾、PLT01-D 同候选验收包。
- 部分完成：PLT01-C 的 OPT09/10 本地定向通过，但 PG16 产品恢复/多进程限流为外部门槛。
- 因此 PLT01 不能写“全部完成”；PLT02-A/B 已完成，见下节。

## PLT02-A 核算政策与正式出表门槛  <状态：完成（定向 + 广域 report/policy 全部通过；仅合成数据）>

- 现状核实：
  - `rg -n "small_enterprise|standard_kind|_standard_kind" nexus_backend/app src` → 唯一的报表生成默认回落原为 `nexus_backend/app/reports/service.py:84-90`（无 active 返回 `small_enterprise`），本轮已改为显式未核定。
  - 报表生成入口 `_template_for_book` 位于 `nexus_backend/app/reports/service.py:118-125`；政策创建表单默认值位于 `src/OpeningPreparationPanel.tsx:50`。
  - 盘点表见 `docs/pilot/PLT02_POLICY_FALLBACK.md`；利润映射与结账依赖 scope 的固定模板已列为范围外顺带发现。
- 失败测试及修改前输出：
  - `nexus_backend/tests/test_plt02_policy_gate.py::test_report_without_active_policy_is_unassessed`
    `Failed: DID NOT RAISE <class 'app.accounting.domain.AccountingError'>` —— 无政策仍生成小企业报表。
  - `nexus_backend/tests/test_plt02_policy_gate.py::test_report_with_active_policy_still_uses_its_standard`
    `KeyError: 'standard_kind'` —— 测试误用不存在的返回字段；改为断言 `report["report"]["template_id"] == "small_enterprise_basic"`，属于测试缺陷修复，不修改产品断言目标。
- 修改文件：
  - `nexus_backend/app/reports/service.py`：新增 `STANDARD_UNASSESSED = "unassessed"`；`_standard_kind` 无 active 返回哨兵；`_template_for_book` 遇哨兵抛 409“核算政策未核定，不能生成报表；请先批准当前期间适用的核算政策”；删除 `"small_enterprise"` 回落。
  - `nexus_backend/tests/test_plt02_policy_gate.py`：新增 2 个门槛测试。
  - `nexus_backend/tests/test_plt02_policy_coverage.py`：5 账套（active/draft/expired/future/none）覆盖盘点脚本，断言 4 个缺失且只读不改文件。
  - `scripts/opt_policy_coverage.py`：只读 SQLite 政策覆盖盘点，输出缺失账套 JSON。
  - `src/OpeningPreparationPanel.tsx`：准则/纳税人身份不再预选小企业/小规模，空值 + 必选占位，未选择时提交按钮禁用。
  - `nexus_backend/tests/test_business_api.py`：新增 `local_client()` 兼容 Starlette 0.35 的 loopback client scope。
  - `nexus_backend/tests/test_b05_api.py`、`test_opening_api.py`、`test_stage13_migration_api.py`：改用 `local_client`，断言不变。
  - `nexus_backend/tests/test_stage13_migration_workflow.py`、`test_stage13_target_catalog_admission.py`：修正过期测试 oracle——独立验收由 `prepare_http` 创建的事务所复核员执行，客户财务只读/提交；原先 9 处用 `c.finance` 独立 accept 的断言与 `test_stage13_migration_api` 的 403 契约冲突。业务断言（201/409/403）未放宽，旧 basis 仍断言 409。
  - `nexus_backend/tests/test_stage20_home.py`：`previous_profile` 对齐 `display_role` 契约与管理优先顺序，2 passed。
  - `docs/pilot/PLT02_POLICY_FALLBACK.md`：盘点与范围外硬编码。
  - 无迁移改动。
- 验收命令与输出：
  ```
  cd nexus_backend && PYTHONPATH=.. python -m pytest tests/test_plt02_policy_gate.py tests/test_plt02_policy_coverage.py -q --tb=short
  3 passed in 9.79s
  ```
  ```
  cd nexus_backend && PYTHONPATH=.. python -m pytest tests/test_plt02_policy_gate.py tests/test_b04_pilot.py::test_periodic_accrual_asset_closing_reports_and_close -q --tb=short
  3 passed in 14.87s
  ```
  ```
  python scripts/opt_policy_coverage.py --sqlite tmp_test/ag_all/test_local_preflight_rejection0/intelligence-workflow.db --as-of 2026-11-18
  missing_count: 2；只读，未补政策
  ```
  ```
  cd nexus_backend && python -m ruff check app/reports/service.py tests/test_plt02_policy_gate.py
  All checks passed!
  ```
  ```
  npm run typecheck
  （通过）
  ```
  ```
  npx prettier --check src/OpeningPreparationPanel.tsx
  All matched files use Prettier code style!
  ```
- 广域相关回归（本模块验收）：
  ```
  PYTHONPATH=. python -m pytest nexus_backend/tests -q -k "report or policy" --tb=short
  283 passed, 67 skipped, 6048 deselected in 339.99s
  ```
  - 从仓库根运行，`test_p01_r2_gate` 能找到 `.github/workflows/p01-recovery.yml`。
  - stage13 的 TestClient 兼容与独立复核人 oracle 修正后，原 4 个 setup error 与 9 个 finance accept 断言均已消失；0 failed、0 error。
  - 283 passed 包含现行报表生成路径；没有报表夹具因“无 active 政策”失败，也没有为通过而补造政策。
- 仅合成验证的部分：是；虚构账套、合成政策与报表。真实客户准则、真实批准政策与真实报表数据未验证。
- 需客户或人提供：每家账套经批准的准则/纳税人身份/生效期；企业会计准则账套的利润映射模板确认。
- 未做或未验证：政策创建 UI 未加自动化用例；`profit.py`/`assistant/delivery_details.py` 固定模板未改；正式/试算双模式、DB 发布约束留给 PLT02-B。
- 顺带发现：`nexus_backend/app/reports/profit.py` 利润映射固定 `small_enterprise_basic`；`nexus_backend/app/assistant/delivery_details.py:84` 结账依赖 scope 固定同一模板。两者不改变本模块报表生成数值，但企业准则场景需后续处理。

## PLT02-B 试算与正式两种出表模式  <状态：完成（后端模式/门禁；C 负责前端与导出呈现）>

- 现状核实：
  - `report_runs` 原无 `mode`，`publish` 只检查状态/版本/期间锁；`accounting_policies` 无批准人、合成标记、映射指纹。
  - `report_line_mappings` 已有版本表，可作为批准时的映射指纹来源；`delivery/monthly_package.py::create` 已只接受 `published` 报表。
- 失败测试及修改前输出：
  - `test_plt02_report_modes.py::...synthetic_policy...` 首跑无拒绝，实际生成正式报表；`test_plt02_report_modes_postgres.py` 首跑 `KeyError: 'mode'`，因测试把 `_view` 外层 `report` 当成扁平对象（测试缺陷，已修，不是产品缺陷）。
  - 旧 `test_plt02_policy_gate.py::test_report_without_active_policy_is_unassessed` 期望 409；本阶段改为 trial，旧断言被替换为“无政策只能试算”。
- 修改文件：
  - `nexus_backend/migrations/sqlite/207_plt02_report_modes.sql`、`postgres/292_plt02_report_modes.sql`：政策批准/合成/映射指纹字段；`report_runs` 模式、政策版本、批准人、映射指纹、试算原因；SQLite/PG 触发器拒绝 `published AND mode<>'formal'`。历史行 `mode` 默认 `formal` 兼容。
  - `nexus_backend/app/reports/mapping.py`：active 映射行排序后 sha256。
  - `nexus_backend/app/opening/models.py`、`service.py`、`router.py`：`PolicyCreate.is_synthetic`、`approve_policy`（manager、active、冻结映射指纹、审计）、`POST /api/opening/policies/{id}/approve`。
  - `nexus_backend/app/reports/service.py`：`_active_policy`/`_template_for_standard`/`_formal_reasons`；无政策显式小企业 trial；正式记录政策版本/批准人/映射指纹/模式/原因；`review`/`publish` 拒绝 trial；`_view` 增加 `mode_label` 与 `trial_reasons`。
  - `nexus_backend/tests/test_plt02_policy_gate.py`、`test_plt02_report_modes.py`、`test_plt02_report_modes_postgres.py`；以及 `test_posting_service.py`、`test_b04_pilot.py`、`p01_recovery_business.py`、`test_finance_continuation.py`、`test_monthly_delivery_prerequisite_postgres.py`、`test_opening_service.py`、`test_p02_formal_read_postgres.py`、`test_posting_api.py`、`test_review_policy_postgres.py`、`test_p0_close_upload_postgres.py` 中政策创建后补批准。
- 验收命令与输出：
  ```
  cd nexus_backend && PYTHONPATH=.. python -m pytest tests/test_plt02_policy_gate.py tests/test_plt02_report_modes.py tests/test_b04_pilot.py -q
  14 passed in 37.69s
  ```
  ```
  python scripts/test_accounting_postgres_isolated.py --config tmp_test/plt02b-pg16/unified.json nexus_backend/tests/test_plt02_report_modes_postgres.py -q
  1 passed in 2.73s
  ```
  ```
  python scripts/test_accounting_postgres_isolated.py --config tmp_test/plt02b-pg17/unified.json nexus_backend/tests/test_plt02_report_modes_postgres.py -q
  1 passed in 2.54s
  ```
  ```
  PYTHONPATH=. python -m pytest nexus_backend/tests -q -k "report or policy" --tb=short
  288 passed, 68 skipped, 6048 deselected in 350.50s (0:05:50)
  ```
  ```
  python -m ruff check ... && python -m ruff format --check ...
  All checks passed!；16 files already formatted
  ```
- PG 证据环境：PLT01-D 的 `restore` 演练把 `fsc02-pg/env17` 原库恢复到仅 `p01_admin` 有 schema 权限，导致后续 runtime 校验失败；已新建一次性 `ak01_local_ak05_plt02b_*` 数据库并重新应用 292，PG16 端口 25496、PG17 25498 各 1 passed。原库未改历史 checksum。
- 仅合成验证的部分：是；政策、批准人、映射、报表均为测试数据。真实政策批准人、真实客户准则、真实报表数据未验证。
- 需客户或人提供：每家账套政策批准人；客户确认的准则/模板/映射；真实报表勾稽依据。
- 未做或未验证：
  - `approve_policy` 暂允许同一 manager 创建并批准（合成测试）；D 阶段的“制单人≠批准人”独立批准流程尚未强制。
  - 试算的**导出文件名、文件页眉、界面标题区**未在本模块实现；`reports` 无独立导出接口，C 前端/交付需接 `mode_label` 和 `trial_reasons`，并在对外入口继续 409。
  - 月度包 `published_report.json` 的字段 pick 未包含 `mode`（只接受 published formal，当前无试算进入路径）；C 需补齐展示/文件命名。
  - 直接绕过服务写 `report_runs.status='published'` 的 PG/SQLite 触发器已测；发布表若未来拆分需重新加约束。
- 顺带发现：`test_p01_r2_gate.py` 依赖仓库根 cwd 的 `.github/workflows/p01-recovery.yml`；从 `nexus_backend` 运行会误报找不到文件。PLT01-D 的 `scripts/opt_policy_coverage.py` 只作只读盘点，不自动补政策。

## PLT02-C 试算/正式模式前端与政策批准入口  <状态：完成（前端展示、阻断与政策批准；无独立报表导出接口）>

- 现状核实：
  - `FormalClosePanel` 原只显示报表状态，未区分试算/正式；试算如果被当作正式候选，会在核验/预检/发布按钮处形成误导。
  - `OpeningPreparationPanel` 只列政策，没有独立批准动作；开账向导模板步骤完成后没有进入核算政策的入口。
  - 交付/月度包只接受 `status='published'` 报表；DB 触发器已禁止试算发布，因此试算没有进入月度包的合法路径。

- 失败测试及修改前输出：
  - 首条浏览器用例在“返回账簿→开账准备”找不到核算政策入口；修复 `SetupStepList` 后通过。
  - 政策列表“批准本政策”点击无响应：函数误用 `command()`，空“本次办理依据”时静默 return；拆出独立的 `approvePolicy` 后通过。
  - 试算提示原因显示“服务端未返回原因”：`ReportService.list` 返回原始 `report_runs` 行；改为解析 `trial_reasons_json` 并补 `mode_label` 后通过。

- 修改文件：
  - `src/formal-close-types.ts`、`src/FormalClosePanel.tsx`、`src/formal-close.css`：`ReportMode`、`mode_label`、`trial_reasons`；试算红色提示、逐条原因、报表下拉标示；核验/独立复核/preflight/publish 在试算下禁用。
  - `src/OpeningPreparationPanel.tsx`：政策 V/批准人/时间/合成标记/映射与批准时是否一致；未批准 active 政策可“批准本政策”；批准提示“已生成的试算报表不会因此转为正式”。
  - `src/OnboardingSetupWizard.tsx`：模板步骤完成后“查看核算政策”。
  - `nexus_backend/app/reports/service.py::list`：补 `mode_label` 并解析 `trial_reasons_json`。
  - `nexus_backend/app/opening/service.py::list_policies`：返回 `mapping_matches`。
  - `e2e/formal-fixtures.ts`、`e2e/formal-books-fixtures.ts`、`e2e/dcp01-close-actions.spec.ts`：合成政策创建后补批准。

- 验收命令与输出：
  ```
  npm run typecheck
  （通过）
  ```
  ```
  npx vitest run src/formal-close-types.test.ts src/formal-close-render.test.ts
  Test Files  2 passed (2)
       Tests  18 passed (18)
  ```
  ```
  cd nexus_backend && PYTHONPATH=.. python -m pytest tests/test_plt02_policy_gate.py tests/test_plt02_report_modes.py tests/test_plt02_policy_coverage.py tests/test_b04_pilot.py -q
  16 passed in 33.32s
  ```
  ```
  npx playwright test e2e/plt02-report-modes.spec.ts --reporter=line
  1 passed (18.3s)
  ```
  ```
  npx playwright test e2e/dcp01-close-actions.spec.ts --reporter=line
  2 passed (25.3s)
  ```
  ```
  npm test -- --run
  Test Files  155 passed (155)
       Tests  1418 passed (1418)
  ```
  ```
  npm run lint
  0 errors, 4 existing warnings
  ```
  ```
  npm run format:check
  All matched files use Prettier code style!
  ```
  ```
  TMP=tmp_test/build-tmp TEMP=tmp_test/build-tmp npm run build
  ✓ built in 3.35s
  initial JS 567.46 kB / gzip 175.87 kB；CSS 99.95 kB / gzip 20.32 kB
  ```

- 浏览器证据：
  - 桌面试算：`tmp_test/plt02c-trial-1366.png`（报表模式“试算，不可对外”、三条原因、核验/预检禁用、生成正式报表禁用）。
  - 手机试算：`tmp_test/plt02c-trial-390.png`（390 宽，无横向溢出，原因逐条可见）。
  - 政策批准：`tmp_test/plt02c-policy-approved-1366.png`（合成/V2/批准人/映射一致、原试算仍不可发布）。
  - 用例还断言：批准后返回 390 仍为试算；API 复查旧报表仍 `mode='trial'`，不会因政策批准自动转正式。

- 仅合成验证的部分：是；政策、公司、报表均为测试夹具；真实客户政策、真实批准人和真实报表数据未验证。
- 需客户或人提供：客户确认的准则/模板/映射；真实政策批准人；真实报表勾稽与交付意见。
- 未做或未验证：
  - `approve_policy` 仍允许同一 manager 创建并批准；PLT02-D 才实现“制单人≠批准人”的强制独立批准。
  - 系统没有独立的报表导出接口，故“试算导出文件名/页眉”没有可接入口；月度包只接受正式 published，试算被服务与 DB 双层阻断。
  - 月度包 `published_report.json` 未显式带 `mode`；当前没有试算进入路径，C 未改包 schema。
  - 政策页 UI 自动化只覆盖批准主路径，未覆盖并发批准、撤权、过期政策。
- 顺带发现：`FormalClosePanel` 的试算提示依赖服务端 `trial_reasons`，以后新增 trial 原因时必须同时更新后端 reason 与解析测试；不得由前端自行推断正式性。

## PLT02-D 五家单位政策建档  <状态：工具就绪待客户输入（五家合成演练通过，真实客户政策未建档）>

- 现状核实：
  - `approve_policy` 允许创建人自批，不满足“制单人不等于批准人”。
  - 五家单位的准则、纳税人类型、科目表来源、报表格式、批准人均属于客户决策，当前没有客户书面依据。
  - 五家合成政策只能用于离线演练，不得标为客户已确认。

- 失败测试及修改前输出：
  - 自批场景没有被拒绝，属于审批分离缺口；先补测试再改服务。
  - 浏览器用例原尝试把创建人提升为 firm admin，被服务端 409 `所有者角色不可在普通授权入口变更` 拒绝；改为使用另一名 firm admin 的 `reviewer` 独立批准，未放宽权限。

- 修改文件：
  - `nexus_backend/app/opening/service.py::approve_policy`：`created_by == approved_by` 时 409“制单人与批准人必须不同，请由另一名事务所管理员批准”。
  - `nexus_backend/tests/policy_fixtures.py`：创建第二 firm admin 的 `independent_manager` 与 `approve_policy_independently`。
  - `nexus_backend/tests/test_plt02_report_modes.py`：新增自批拒绝用例。
  - `nexus_backend/tests/test_plt02_five_synthetic_policies.py`：五家合成政策均独立批准，且 `_formal_reasons` 含“核算政策为合成演练，不可正式出表”。
  - `e2e/formal-fixtures.ts`、`e2e/formal-books-fixtures.ts`、`e2e/dcp01-close-actions.spec.ts`、`e2e/plt02-report-modes.spec.ts`：批准人改为独立 reviewer；E2E 只提升 reviewer 为 firm admin，不改变创建人角色。
  - `docs/pilot/POLICY_INTAKE.md`：五家单位的客户确认字段、系统字段映射、禁止事项。

- 验收命令与输出：
  ```
  cd nexus_backend && PYTHONPATH=.. python -m pytest tests/test_plt02_report_modes.py tests/test_plt02_policy_gate.py tests/test_plt02_policy_coverage.py tests/test_plt02_five_synthetic_policies.py -q
  11 passed in 29.15s
  ```
  ```
  cd nexus_backend && PYTHONPATH=.. python -m pytest tests/test_posting_service.py -q
  6 passed in 21.15s
  ```
  ```
  npx playwright test e2e/plt02-report-modes.spec.ts --reporter=line
  1 passed (22.1s)
  ```
  ```
  npx playwright test e2e/dcp01-close-actions.spec.ts --reporter=line
  2 passed (27.0s)
  ```

- 浏览器证据：
  - `tmp_test/plt02c-policy-approved-1366.png`：政策 V/批准人/合成标记/映射一致；批准后说明旧试算不会转正式。
  - 五家合成政策只在后端测试夹具中生成，未写入真实/长期账套。

- 仅合成验证的部分：是。五家均为虚构标签；准则统一使用小企业仅用于试算路径验证，不代表客户适用性。
- 需客户或人提供：五家法人性质、适用准则及依据、纳税人类型、科目表文件、报表格式、客户批准人；真实批准人和生效期。
- 未做或未验证：
  - 五家真实政策均未建档；`POLICY_INTAKE.md` 保持空表待客户填写。
  - 独立批准只在本地合成身份验证；真实事务所第二管理员的开通/撤权流程未验证。
  - 医院等特殊行业的准则选择未做任何推断。
- 顺带发现：测试环境用 reviewer 提升为 firm admin 仅用于构造独立批准人；真实产品不应通过普通授权入口更改所有者角色（服务端已 409）。


## PLT02-E 报表逐行对平工具  <状态：工具就绪待客户输入（合成三类差异与 HTTP 接线通过；真实已确认报表未提供）>

- 现状核实：
  - 系统报表已有冻结 `content_json`，但没有参考报表 CSV 对平、逐行差异、解释类型与下钻接口。
  - PLT04-D 逐凭证下钻未完成，因此当前只能输出科目级/现金流量构成，不冒充凭证级。
  - 参考报表必须由客户财务负责人确认，不能用系统报表反推成“客户已确认”。

- 失败测试及修改前输出：
  - 新增 `test_plt02_reference_reconciliation.py` 首轮覆盖 1 分舍入差、重分类、参考行缺失、参考有而系统无；实现后 4 passed。
  - HTTP 路由测试首轮没有覆盖入口，后补 `test_reconciliation_routes_are_wired`；修复测试 token 在应用启动后才存在、报表夹具缺期初绑定等问题，5 passed/15.27s。

- 修改文件：
  - `nexus_backend/app/reports/reconciliation.py`：CSV 解析、标准行名/别名、整数分比较、逐行状态、科目级/现金流量下钻。
  - `nexus_backend/app/reports/service.py`：`reconcile` 只读比较、`explain_difference` 追加解释、`reconciliation_notes` 按 reference hash 查询；解释写审计。
  - `nexus_backend/app/reports/models.py`、`router.py`：`ReportReconcile`、`ReportDifferenceNote`、三个路由。
  - `nexus_backend/migrations/sqlite/208_plt02_reconciliation_notes.sql`、`postgres/293_plt02_reconciliation_notes.sql`：append-only 解释表，SQLite 触发器与 PG RLS/触发器；未改历史迁移。
  - `nexus_backend/tests/test_plt02_reference_reconciliation.py`、`test_plt02_reference_reconciliation_postgres.py`。
  - `docs/pilot/PLT02_RECONCILIATION_CSV.md`、`docs/pilot/samples/plt02_reference_2026-10.csv`。

- 验收命令与输出：
  ```
  cd nexus_backend && PYTHONPATH=.. python -m pytest tests/test_plt02_reference_reconciliation.py tests/test_plt02_report_modes.py tests/test_plt02_policy_gate.py -q
  14 passed in 35.53s
  ```
  ```
  # 并发任务提交后 HEAD 变更，重跑同一组命令：
  # HEAD 39067aacc1b640419ee80a794297b233fd13e546
  14 passed, 2585 warnings in 39.39s
  ```
  ```
  PATH="/c/Users/Fei/AppData/Local/Temp/nexus-stage21-pg17/pgsql/bin:$PATH" PYTHONPATH=. python scripts/test_accounting_postgres_isolated.py --config tmp_test/plt02b-pg17/unified.json nexus_backend/tests/test_plt02_reference_reconciliation_postgres.py -q
  1 passed in 3.10s
  ```
  ```
  ... --config tmp_test/plt02b-pg16/unified.json ...
  1 passed in 2.96s
  ```
  ```
  cd nexus_backend && ruff check app/reports/reconciliation.py app/reports/service.py app/reports/router.py app/reports/models.py app/opening/service.py tests/test_plt02_reference_reconciliation.py tests/test_plt02_reference_reconciliation_postgres.py tests/policy_fixtures.py
  All checks passed!
  ```

- 合成演练：
  - CSV 样例故意制造：负债合计 +1 分、利润 -1.00 元重分类、期末现金参考缺失；工具输出 2 个 `amount_different`、1 个 `missing_reference`、9 个 `matched`。
  - 三类差异均登记解释并写 `report.reconciliation.explain` 审计；报表保持 `draft`，不触发复核/发布/导出。
  - 参考有而系统无的单行测试返回 `missing_system`。

- HTTP 证据（真实本地应用、SQLite）：
  - `POST /api/reports/{id}/reconcile` → 200，counts/rows/drilldown/reference_sha256。
  - `POST /api/reports/{id}/reconciliation-notes` → 201；`GET ...?reference_sha256=` → 200。
  - 解释 UPDATE/DELETE 被 SQLite 触发器拒绝；PG 由 runtime 无 UPDATE 权限或触发器拒绝，测试接受两种拒绝路径。

- 仅合成验证的部分：是。参考 CSV 由测试生成，非客户原件。
- 需客户或人提供：客户财务负责人已确认的报表原件/CSV、客户行次与标准行名映射、差异解释依据。
- 未做或未验证：
  - 没有独立前端对平页面，当前入口为 HTTP API；真实客户文件未导入。
  - 下钻仅科目级/现金流量构成，未到逐凭证；待 PLT04-D。
  - 未处理超过 512KB、非 UTF-8、Excel 公式、合并单元格等客户文件。
  - 未做客户确认人签署/双人复核；当前解释绑定登录复核人。

## 包 01 / PLT02 五法人政策及出表（本轮复核）  <状态：本地通过（五家合成政策/试算正式边界/参考报表对平）；五户客户签认与上期签字报表待客户>

- 现状核实：PLT02-A–E 已有实现；本轮没有重建模块，按正常入口复跑政策门禁、试算/正式阻断、五家合成政策和参考报表对平。
- 实际命令：
  ```text
  cd nexus_backend && PYTHONPATH=.. python -m pytest \
    tests/test_plt02_report_modes.py tests/test_plt02_policy_gate.py \
    tests/test_plt02_reference_reconciliation.py tests/test_plt02_five_synthetic_policies.py -q
  15 passed, 2617 warnings in 40.11s
  ```
  ```text
  npx playwright test e2e/plt02-report-modes.spec.ts --reporter=line
  1 passed (24.3s)
  ```
- 覆盖：无政策/草稿/过期/被取代 → 未核定并阻断；合成政策只能试算；未批准/映射变化/未分类现金流阻断正式；正式包引用批准政策；五家合成政策演练；参考报表 1 分舍入、重分类、缺行三类差异及解释审计；桌面/手机试算提示与按钮阻断。
- 五张待签认卡：`docs/pilot/POLICY_INTAKE.md`；上期签字报表逐行对平记录模板：`docs/pilot/PLT02_RECONCILIATION_CSV.md`。
- 未验/外部：真实五户准则、纳税人类型、科目表/报表格式、批准人、已确认上期报表与差异解释；医院未被代选准则。
- 未改历史迁移；本轮只追加/复核，未提交/推送/部署。

## PLT03-A 待办来源盘点  <状态：完成（只读盘点）>

- 现状核实：读取 `src/FirmInbox.tsx`、`src/workspace-types.ts::taskDestination`、`src/App.tsx::openWorkspaceTask`、`nexus_backend/app/workspace/service.py` 的 14 个 `TaskKind` 分支，以及 `FirmInbox` 的 `/delivery/followups` 独立 feed。
- 盘点结果：`docs/pilot/TASK_INVENTORY.md`。14 类 `TaskKind` + 1 类交付跟进，逐项记录来源、落点、链接定位、权限与完成条件。
- 统计：只到模块/账套级 **2 类**（`export` 丢 `ref`、`alert` 丢 `ref`）；无链接 **0 类**；14 类都不带期间与对象版本；4 类（case/review/ocr/proposal）未显式切单位；14 类都没有服务端 `available_action` 枚举。
- 修改文件：`docs/pilot/TASK_INVENTORY.md`（新增），`docs/pilot/PLT_STATUS.md`（本状态节）。未改产品代码。
- 验收命令与输出：
  ```text
  rg -n "KINDS =|branch\\(|taskDestination|openWorkspaceTask|delivery/followups" \
    nexus_backend/app/workspace/service.py src/App.tsx src/workspace-types.ts src/FirmInbox.tsx
  ```
  输出确认 14 个 task branch、`taskDestination` 的 switch、`openWorkspaceTask` 的目标分支和 `/delivery/followups` feed 均存在；清单行数与 `TaskKind` 数量一致（14+1）。
- 失败测试：不适用（只读盘点，无行为修改）。
- 仅合成验证的部分：盘点基于源码，未跑真实用户路径；实际点击行为留给 PLT03-C 的 Playwright 验证。
- 需客户或人提供：无（PLT03-A 只读）。
- 未做或未验证：未用四种真实岗位走查上述 15 类入口；未确认旧链接/撤权/已完成状态的目标页面（PLT03-C）；未提供「可办理/仅可查看/无权查看」字段（PLT03-B）。
- 顺带发现：`export` 和 `alert` 只落到账套筛选/企业运行页，属于 PLT03-C 必须修正的“只到模块首页”类型；`delivery followup` 有期间但没有 `available_action`，不能与 14 类任务混算。

## PLT03-B 服务端任务封套  <状态：完成（SQLite/HTTP/PG16/PG17 通过；仅合成身份）>

- 现状核实：`WorkspaceService.list/get` 的 14 个 branch 只返回 `node/bucket/action_label/responsible`，没有可用动作、期间、对象版本；前端 `WorkspaceTask` 也没有这些字段。
- 失败测试及修改前输出：先补契约断言后，临时还原 `nexus_backend/app/workspace/service.py` 到 HEAD 运行：
  ```text
  cd nexus_backend && PYTHONPATH=.. pytest tests/test_stage12_workspace.py -q --tb=short
  4 failed, 13 passed in 38.48s
  KeyError: 'period'（4 个用例）
  ```
  失败原因是原实现没有封套，不是夹具错误；随后恢复实现重跑。
- 修改文件：
  - `nexus_backend/app/workspace/service.py`：新增 `_decorate_task_envelopes`，按 `books.period`/`business_events.payload.period` 批量补期间；每行补 `period`、`object_type`、`object_id`、`object_version`（`updated_at`）、`available_action`（可操作 `can_act`、等待 `read_only`）、`business_action`、`current_handler`、`alternate_actions`、`amount_impact`。无权限行仍不返回，因此不生成 `no_access` 行。
  - `src/workspace-types.ts`：`WorkspaceTask` 增加上述可选字段；本模块未改前端动作权限。
  - `nexus_backend/tests/test_stage12_workspace.py`：等待态 `read_only`、审批人 `can_act`/`object_version`/`object_type`、review 撤销后 `period=2026-10`/`can_act`/`business_action=action_label`。
  - `test_stage8_cases_api.py`、`test_stage12_workspace_api.py`：改用 `local_client`，不改断言；消除本机 Starlette 0.35 `TestClient(client=...)` 环境错误。
  - `nexus_backend/tests/test_stage12_workspace_postgres.py`：补 PG 期间/动作/对象断言；批量封套仍为固定 3 条语句，不含 N+1。
- 验收命令与输出：
  ```text
  cd nexus_backend && PYTHONPATH=.. python -m pytest tests/test_stage12_workspace.py -q
  17 passed, 1290 warnings in 39.58s
  ```
  ```text
  cd nexus_backend && PYTHONPATH=.. python -m pytest tests/test_stage12_workspace_api.py tests/test_stage13_migration_workflow.py -q
  26 passed, 35355 warnings in 74.81s
  ```
  ```text
  cd nexus_backend && PYTHONPATH=.. python -m pytest tests/test_stage13_migration_workspace.py -q
  11 passed in 11.35s
  ```
  ```text
  # PG17 / PG16，各自建库后测试
  PATH="/c/Users/Fei/AppData/Local/Temp/nexus-stage21-pg17/pgsql/bin:$PATH" PYTHONPATH=. \
    python scripts/test_accounting_postgres_isolated.py --config tmp_test/plt02b-pg17/unified.json \
    nexus_backend/tests/test_stage12_workspace_postgres.py -q
  9 passed in 22.40s

  ... --config tmp_test/plt02b-pg16/unified.json ...
  9 passed in 22.74s
  ```
  ```text
  cd nexus_backend && python -m ruff check app/workspace/service.py tests/test_stage12_workspace.py
  All checks passed!
  ```
  ```text
  npm run typecheck
  tsc -p tsconfig.app.json --noEmit（退出 0）
  ```
- 仅合成验证的部分：是。四种身份/撤权/暂停均来自本地合成用户；未用真实客户。
- 需客户或人提供：无（服务端语义可由权限规则与合成身份验证）。
- 未做或未验证：
  - `no_access` 只在“行本应存在但无权查看”时有意义；当前查询先授权过滤，故不返回占位行，未伪造。
  - `alternate_actions` 暂为空数组；替代动作的候选生成留给 PLT03-D。
  - `export`/`alert` 仍缺目标账套/期间精确落点，属 PLT03-C。
  - 未移交的 `case` 没有账套期间，`period` 保持 `null`，不把发生月份伪造成账套期间。
  - 未做真实浏览器中的封套展示与旧链接/撤权/完成态跳转。
- 顺带发现：`test_stage13_migration_workspace.py` 的 branch 级 exact-key 断言直查 `workspace_branch`，不经 service 装饰；保持原断言并通过，不扩宽为“任意新增字段”。

## PLT03-C 直达与返回  <状态：本地完成（14类目标键/范围/版本/alert/失效页/review/业务/case 410办理人时间/completed直达/五身份待办链/业务与复核多层返回/月度进入复核后返回原月份/队列滚动通过；独立出纳身份待客户核定，见 `PLT03_ROLE_HANDOFF.md`）>

- 现状核实：`WorkspaceTask` 已由 PLT03-B 提供 `company_id/book_id/period/object_*`；`taskDestination` 14 个分支已带对象 id；`openWorkspaceTask` 在打开业务/复核/导出/交付时分别设置目标状态，但此前不统一设置期间，也不提示跨单位切换。
- 修改文件：
  - 新增 `src/task-scope.ts`：纯函数 `taskScope(task,current)` 返回目标 `companyId/period/switched/notice`；任务无期间时返回空串，不猜测。
  - 新增 `src/task-availability.ts`：把打开前的 403/404 映射为可区分的“无权/不存在或已办完”提示；不把 404 一律当网络错误。
  - `src/App.tsx::openWorkspaceTask`：在校验目标后统一设置 `workCompany/workPeriod`；跨单位或期间变化时 `setNotice({kind:'success', text:'已切换到 …'})`，再进入 review/business/case/export/delivery 等原分支。
  - 新增 `src/task-scope.test.ts`：同单位期间不变、跨单位/期间给出准确提示、任务无期间不伪造。
  - 新增 `e2e/plt03-task-navigation.spec.ts`：两企业各一本账、各一条复核待办；点击跨企业待办后核对 drawer 企业与期间、URL `company/period/review_book/review_draft`、顶栏范围、返回列表后 `review` 筛选与原待办仍在；再模拟“列表版本旧、服务端对象版本新”，断言停在原队列并显示“待办内容已更新，请刷新列表后重新打开。”。同文件新增手机端测试：从第 7 条待办进入复核后返回，恢复原企业/期间与滚动位置（原 1492，断言 >1392）。
  - 新增 `e2e/plt03-task-matrix.spec.ts`：用同一已授权身份和 14 条合成待办，逐类点击并断言落到目标键：`intake`、`event`、`case`、`material`、`review_draft`、`job`、`proposal`、`filter=approved`、`delivery`、`/workspace/operations?alert=…`、`migration`；同时断言 URL 均带 `company/period/task_version`。**1 passed/27.5s**。
  - `src/workspace-types.ts::taskDestination` 的 alert 分支改带 `task.ref`（告警 ID）和 `companyId`；`OperationsWorkbench` 新增 `initialAlertId`，加载后滚动并高亮目标告警，`workspace-route.ts` 新增 `alert` 查询键。
  - `src/App.tsx::openWorkspaceTask` 新增 `go(view)`：`navigate` 后恢复 `scope.companyId/period`，修复原 `navigate()` 清空范围导致 `case/ocr/export/alert` URL 不带单位/期间。复核待办记录打开前 `scrollY/company/period`；返回时恢复原范围并最多重试 40×50ms 等待列表高度恢复后 `scrollTo`。
  - `src/workspace-route.ts` 新增 `taskVersion:'task_version'`，`App` 持久化并恢复 `taskVersion`；14 类 URL 均带 `company/period/task_version`，恢复后仍以服务端重读为准。
  - `src/WorkspaceHome.tsx`/`src/FirmInbox.tsx`：打开前重新取任务后同时比对 `kind/ref/company_id/book_id/object_version`；版本变化不进入旧对象，也不再清空原队列。仅 401/403/404 撤下列表。
  - 新增 `src/task-availability.ts::taskOpenErrorMessage`：404 统一说明“待办对象不存在、已办完或当前账号不可见”，403 说明“已无权访问”，其余错误保持服务端信息；`task-availability.test.ts` **3 passed**。
  - 新增 `src/TaskUnavailableDialog.tsx` + `.task-unavailable-*` 样式：版本变化“待办内容已更新”可“打开最新内容”，403“权限已撤销”显示当前处理人，404 统一“待办不可见”且不泄露存在性，410“待办已办完”显示服务端给出的办理人/时间，并提供“查看处理记录”直达原对象；`WorkspaceHome` 与 `FirmInbox` 均接入，原队列不清空。
  - 新增 `src/task-availability.ts`/`src/task-scope.ts`/`src/TaskUnavailableDialog.tsx`、`e2e/plt03-task-matrix.spec.ts`、`e2e/plt03-task-navigation.spec.ts`、`e2e/plt03-reviewer-chain.spec.ts`，并修改 `src/App.tsx`、`src/WorkspaceHome.tsx`、`src/FirmInbox.tsx`、`src/OperationsWorkbench.tsx`、`src/workspace-route.ts`、`src/workspace-types.ts`、`src/WorkspaceRouteContent.tsx`、`src/styles.css`、`e2e/business.spec.ts`、`e2e/stage8-cases.spec.ts`。
  - `nexus_backend/app/workspace/service.py::_review_completion`：仅在已办完 review 且当前账号仍有该账套复核范围时查询 `drafts.approved_by/approved_at/users.name`，返回 410“该待办已由 … 于 … 办理”；无权限/不存在仍是 404，不泄露存在性。SQLite 与 PG16/17 均已覆盖。
  - `nexus_backend/app/workspace/service.py::_business_completion`：对 approval/finance/handoff 的已完成阶段，按 `business_actions` 最新 approve/handoff 记录返回 410 办理人/时间；先按当前 role gates + 公司成员/事务所经理范围授权，再查动作，未知/无权仍 404。approval/finance/handoff 三类均已用各自完成事件断言 410；无审批权限的 accountant 仍 404。
  - `test_stage12_workspace.py::test_workspace_person_department_company_independent_approval`：原断言 404 改为 410，并断言无审批权限的 accountant 仍 404；该用例 **1 passed/9.31s**（红测先得 `assert 410 == 404`）；SQLite 整套 **19 passed/40.61s**。
  - `test_workspace_finance_and_handoff_completed_return_actor_and_time`：先 `approved(c)` 再断言 finance 410，再 `handoff(c,event)` 断言 handoff 410，均含办理人/时间；SQLite 整套 **20 passed/40.73s**。
  - `nexus_backend/app/workspace/service.py::_case_completion`：先走 `CasesService._case` 的现有可见性，仅 `state='closed'` 时取最新 `case_history action='close'` 与 `users.name`，返回 410 办理人/时间；不存在/无权仍 404。SQLite 新增 closed case 410；PG16/17 各新增 1 条，各 **11 passed/23.54s、23.76s**。
  - `WorkspaceRouteContent.tsx::onViewCompleted` 增加 case 分支并修正顺序：先 `navigate('cases')` 再设 company/period/initialCaseId；approval 分支同样先 `navigate('business')` 再设 event，否则被 `navigate()` 清空。review 分支用 `openMonthlyTarget` 一次设置 book/company/period。
  - PG 同一文件在 `tmp_test/plt02b-pg17|16` 新库各 **10 passed/23.07s、22.70s**，覆盖 410 查询的 JSONB/RLS/授权路径；approval/finance/handoff 三类完成态均已用同一实现独立验证。
- 失败测试及修改前输出：本项未保留“打开落到错误页”的 Playwright 红测；首次新用例失败仅因断言把 `object_type` 写成 `draft`，服务端实际为任务类型 `review`，修正断言后通过。此前 `src/task-scope.test.ts` 3 passed。
- 验收命令与输出：
  ```text
  npx vitest run src/task-scope.test.ts src/task-availability.test.ts
  6 passed
  ```
  ```text
  npx playwright test e2e/plt03-task-navigation.spec.ts --reporter=line
  4 passed (22.1s)（跨企业深链/返回筛选/旧版本对话框/403/410/completed 查看处理记录直达原账簿；新增 case 与 approval completed 直达 case/业务事项；手机端滚动恢复）
  截图：`tmp_test/plt03c-stale-dialog.png`、`tmp_test/plt03c-forbidden-dialog.png`、`tmp_test/plt03c-completed-dialog.png`
  ```text
  npx playwright test e2e/plt03-task-navigation.spec.ts --reporter=line
  4 passed (22.1s)（completed 按钮打开 accounting，book/company 精确等于待办 target；case 打开 `/workspace/cases?case=…`；approval 打开 `/workspace/business?event=…`）
  ```
  ```
  ```text
  npx playwright test e2e/plt03-task-matrix.spec.ts --reporter=line
  1 passed (27.5s)（14 类待办逐类点击，均落到目标键，URL 均带 company/period/task_version；alert 带告警 ID）
  ```
  ```text
  npx playwright test e2e/plt03-monthly-return.spec.ts --reporter=line
  1 passed (14.3s)（月度客户行→复核/账簿→“返回客户月度进度”，月份仍为 2026-10，客户行恢复）
  ```
  ```text
  python scripts/test_accounting_postgres_isolated.py --config tmp_test/plt02b-pg17/unified.json nexus_backend/tests/test_stage12_workspace_postgres.py::test_pg_completed_review_returns_410_with_actor_and_time -q
  1 passed (3.93s)

  python scripts/test_accounting_postgres_isolated.py --config tmp_test/plt02b-pg16/unified.json nexus_backend/tests/test_stage12_workspace_postgres.py::test_pg_completed_review_returns_410_with_actor_and_time -q
  1 passed (3.90s)
  ```
  ```text
  npx playwright test e2e/stage12-workspace.spec.ts --reporter=line
  3 passed (25.0s)（撤权后断言统一“待办不可见”，不探测对象存在性）
  ```
  ```text
  npx playwright test e2e/stage8-cases.spec.ts --reporter=line
  1 passed (20.0s)（员工/会计从真实待办进入、办理，任务由业务事实关闭）
  ```
  ```text
  npx playwright test e2e/business.spec.ts -g "部门、财务、会计从各自待办卡片|390 视口部门" --reporter=line
  2 passed (36.2s)（1366/390；员工提交→部门待办→部门办理→返回待办恢复滚动→财务待办→会计待办）
  ```
  ```text
  npx playwright test e2e/plt03-reviewer-chain.spec.ts --reporter=line
  2 passed (19.8s)（1366/390；复核人从真实待办进入→原件页签→分录页签→勾选独立复核→确认审核→返回原队列并恢复滚动）
  截图：`tmp_test/plt03c-reviewer-chain-1366.png`、`tmp_test/plt03c-reviewer-chain-390.png`
  ```
  ```text
  npm run typecheck
  tsc -p tsconfig.app.json --noEmit（退出 0）
  ```
  ```text
  npm test -- --run
  Test Files 157 passed (157) / Tests 1424 passed (1424)
  ```
  ```text
  npm run lint
  0 errors, 4 warnings（既有未使用 eslint-disable）
  ```
  ```text
  TMP=tmp_test/build-tmp TEMP=tmp_test/build-tmp npm run build
  ✓ built in 3.41s；index JS 568.77 kB / gzip 176.37 kB，index CSS 101.46 kB / gzip 20.63 kB
  ```
- 未完成：
  - 14 类已逐类验证落点键与 company/period；`alert` 已带告警 ID 并在运行页定位；`export` 的投影 ref 是账套 id，任务本身就是“该账套的已审核凭证队列”，`book+filter=approved` 即精确目标，没有单独导出请求对象可打开。
  - 对象版本已写入 URL 并在打开前与列表比对；仍未提供“查看最新版本”对比页，只提示刷新后重开。
  - 403/404/版本变化已有独立对话框；`review` 的 410 展示 `drafts.approved_by/approved_at`，approval/finance/handoff 的 410 展示 `business_actions` 的办理人和时间；对象不存在与无权仍统一 404，不泄露存在性。
  - 410 的“查看处理记录”已接回原对象：review 通过 `openMonthlyTarget` 同时设置 company/period/book、`setBookId` 与 `initialize`，避免 navigate 后默认账套覆盖任务账套；event 带 company/period/event 进入业务事项；`e2e/plt03-task-navigation.spec.ts` 断言 book 等于待办 `target.bookId`、company 等于 `target.companyId`，**2 passed/19.7s**。
  - 队列筛选与手机端滚动位置均能恢复；恢复发生在列表重载完成之后（约 200ms），期间存在一次可见回跳。多层返回已覆盖“待办→复核抽屉→原件页签→分录页签→队列”“待办→业务事项→部门办理→返回待办”，均回到原队列并恢复滚动；月度导入-导出路径未逐层验证。
  - 1366/390 已跑员工、部门、企业财务、会计、复核人从真实待办进入并完成办理的链；仓库没有独立“出纳”身份，企业财务是否等同出纳需客户确认，本项不把企业财务直接写成出纳。其余 9 类矩阵只验证落点键，不验证办理成功。
  - 五条真实链均从待办卡片进入：员工/会计 `e2e/stage8-cases.spec.ts` **1 passed/20.0s**；部门/财务/会计 `e2e/business.spec.ts` **2 passed/36.2s**（1366/390）；复核人 `e2e/plt03-reviewer-chain.spec.ts` **2 passed/19.8s**（1366/390）。
- 结论：范围切换、14 类目标键与 company/period/task_version、alert 告警定位、export 账套已审核队列、跨企业深链、返回筛选/滚动、旧版本“打开最新内容”、403/404/410 独立提示页、review/approval/finance/handoff/case 办理人/时间、completed 直达 review/case/审批原对象均已就绪；员工、部门、财务、会计、复核人均已在 1366 与 390 从真实待办卡片进入并办理，复核链（原件页签→分录页签→确认→返回）、业务链（事项→部门办理→返回）、月度链（客户行→复核/账簿→返回客户月度进度）均回到原对象/原月份。PLT03-C 仍不能标完成：没有独立出纳身份可验收；“原件”为复核抽屉页签，不是独立页面，未另造多层返回。
- 当前候选（只读指纹）：HEAD `c8da023777fff5c7eeb7311067cdcd7e8244e61b`，source **2445 / `21e1413daf00c45540d22b8153f65d5e0cfde943c6f2842fd33da17dc2f5afa5`**，migration **487 / `4964fda2103ea40dcd3f0cdb33239e4e07a66acfad1e98d4cc75548a4392b3bb`**；工作区未提交。

## 包 02 / PLT03 待办、手机和岗位入口（本轮复核）  <状态：本地通过（五类岗位办理与返回/14类落点/失效页/版本拦截/滚动恢复）；出纳职责待客户核定>

- 现状核实：PLT03-A–D 已有实现；未重建模块，按正常待办入口复跑员工、部门、财务、会计、复核人链路。
- 实际命令：
  ```text
  npx playwright test e2e/stage8-cases.spec.ts --reporter=line
  1 passed (20.0s)
  ```
  ```text
  npx playwright test e2e/business.spec.ts --reporter=line
  2 passed (36.2s)   # 1366 + 390
  ```
  ```text
  npx playwright test e2e/plt03-task-navigation.spec.ts e2e/plt03-task-matrix.spec.ts e2e/plt03-reviewer-chain.spec.ts --reporter=line
  7 passed (43.4s)
  ```
- 夹具修正：`plt03-task-navigation` 不再把“当前有 7 条复核待办”当契约；同批其他用例办理后剩余 5 条时，按当前行数滚动并断言返回前后行数一致，避免跨用例状态导致假失败/放宽业务断言。
- 覆盖：员工补件、部门审批、财务核验移交、会计接受补件、复核人原件/分录/确认；14 类待办目标键；`company/period/task_version/alert`；旧版本、403、404、410；业务与复核返回原队列和滚动位置；手机 390。
- 出纳：系统无独立出纳身份/付款入口，按 `PLT03_ROLE_HANDOFF.md` 显式标记“待客户核定、待授权联测”，不以企业财务代替。
- 未验：无真实出纳、银行付款、真机/读屏、Firefox/WebKit 的人工验收；不把合成身份当现场岗位验收。
- 未提交/推送/部署。

## PLT03-D "仅查看"不计为"已办理"  <状态：完成（服务端事实驱动，合成身份）>

- 现状核实：`FirmInbox.openTask` 只 `GET /api/workspace/tasks/{kind}/{ref}` 校验类型/企业/账套/对象版本，不存在“打开即完成”写操作；`WorkspaceService.list` 的待办只由业务表当前状态投影，不保存“已读/已打开”；通知中心只按 `client_message_reads` 记消息已读，不改变业务待办。
- 修改文件：`nexus_backend/tests/test_stage12_workspace.py` 新增 `test_workspace_view_does_not_complete_task`；不改产品逻辑。
- 失败测试及修改前输出：本项未造“打开即完成”的产品失败；新增用例先验证当前行为，若未来引入打开写状态会导致计数变化而失败。
- 验收命令与输出：
  ```text
  cd nexus_backend && PYTHONPATH=.. python -m pytest tests/test_stage12_workspace.py::test_workspace_view_does_not_complete_task -q
  1 passed, 1290 warnings in 9.20s
  ```
  ```text
  cd nexus_backend && PYTHONPATH=.. python -m pytest tests/test_stage12_workspace.py -q
  18 passed, 1290 warnings in 40.64s
  ```
- 用例内容：部门审批待办 `get` 前后 `pagination.total` 都为 1、任务 ref 不变；真实 `approve_department` 后审批人待办总数变为 0。
- 仅合成验证：单一企业/一条审批待办；未覆盖四身份全量任务、通知消息与待办混合统计。
- 需客户或人提供：四类岗位真人各走一遍，确认是否存在“点进去不知道下一步”的环节。
- 未做/未验证：未改或重算运营看板“已处理数量/处理率”的独立口径；未做消息通知到手机待办的实际路径；未验证通知类已读与业务待办在同一页的视觉区分。
- 顺带发现：`client_message_reads` 只影响消息未读数，业务待办不因消息已读消失；未发现打开即完成隐藏写操作。

## PLT04-A 出租车计费、结算到报表全链  <状态：完成（只读代码/合成测试盘点；未生成真实运行 trace）>

- 现状核实：已读 `nexus_backend/app/tax/fees.py`、`sources.py`、`posting/service.py`、`reports/profit.py`、`reports/service.py`、`src/TaxSourcePanel.tsx`、`TaxFeeBatchPanel.tsx`、`TaxFPanel.tsx` 及 080/142/147/163/164/166 迁移。
- 修改文件：`docs/pilot/TAXI_CHAIN.md` 新建；未改产品代码、未新增迁移、未提交。
- 链路：
  - `tax_source_files.rows_json`（adopted 行，`source_row_number` 含表头偏移）→ `TaxFeeService.generate` → `tax_fee_batches` / `tax_fee_batch_rows` / `sources(external_id=tax-fee:<batch_id>)` / `drafts` / `draft_lines` / `draft_line_sources` / `draft_line_auxiliaries`。
  - `TaxFeeService.review`：独立复核、政策/来源/运输身份重验、`_approve_in_transaction`、`_materialize_receivables`；`fee_receivable_items.batch_row_id` 保留行来源。
  - `fee_receivable_allocations`/`fee_receivable_refunds` 只 FK `sources(id,book_id)`，不回 `bank_statement_lines`；核销撤销只追加撤销字段。
  - `PostingService.post_draft` 生成 `vouchers`/`voucher_lines`，`voucher_lines.source_ref = sources.id`，`voucher_sources(source_kind='draft', source_id=draft_id)`。
  - `ReportService.create` 写 `report_runs.content_json`；`ProfitDrilldownService._rows` 按期间实时查 `voucher_lines`，利润下钻不用报表快照。
- 断点清单（`TAXI_CHAIN.md` 第 5 节）：B1 规费复核后无产品内“发布/记账”入口；B2 凭证行到来源行需 `line_no→draft_line_sources.ordinal` 人工映射，行拆分可错配；B3 银行核销无法精确回银行流水行；B4 利润下钻用实时凭证而非报表快照；B5 drill items 截断 1000 条；B6 结算单/银行流水/原始行多处靠金额/日期/编号人工匹配；B7 来源行无深链；B8 批次/应收/凭证无统一链路视图。
- 验收命令与输出：本项为只读盘点，无新增可执行用例；引用既有合成夹具 `test_tax06_fees.py` 的 `_masters/_source` 作为后续 trace 路径，未把未执行 trace 写成通过。
- 仅合成验证：文档中的 L1 为合成示例（净额 90.00 元），未用客户真实规费、银行回单、T+ 回执。
- 未做/未验证：未运行新的链式 trace；未验证 B1 的替代入口（若存在其他通用凭证记账入口，尚未实测）；未验证真实银行流水、真实客户原件、真实申报。
- 顺带发现：`tax_fee_batches.status` 只有 draft/reviewed/invalidated，不记录 posted；规费批次与凭证之间只能经 draft 关联。

## PLT04-B 计费差异与处理  <状态：本地验收通过（合成身份/合成银行流水；六类差异、差异凭证、处理人独立复核、前端登记与处理、月结预检、PG 并发与真实锁期 guard 均通过）>

- 现状核实：现有 `fee_receivable_allocations` 只有全额/部分核销，没有差异登记；`accounting_policies` 没有差异原因白名单；`b04_close.review/lock` 原来不检查规费差异。
- 修改文件：
  - `nexus_backend/migrations/sqlite/209_plt04_fee_variances.sql`、`nexus_backend/migrations/postgres/294_plt04_fee_variances.sql`。
  - `nexus_backend/app/opening/models.py`、`nexus_backend/app/opening/service.py`。
  - `nexus_backend/app/tax/variances.py`、`nexus_backend/app/tax/router.py`、`nexus_backend/app/b04_close/service.py`、`nexus_backend/app/main.py`。
  - `nexus_backend/tests/test_plt04_fee_variances.py`、`test_plt04_fee_variances_api.py`、`test_plt04_fee_variances_postgres.py`。
  - `nexus_backend/migrations/sqlite/210_plt04_variance_processing.sql`、`nexus_backend/migrations/postgres/295_plt04_variance_processing.sql`。
  - `nexus_backend/migrations/sqlite/211_plt04_close_variance_check.sql`、`nexus_backend/migrations/postgres/296_plt04_close_variance_check.sql`。
  - `nexus_backend/migrations/sqlite/212_plt04_variance_advisory_close.sql`、`nexus_backend/migrations/postgres/297_plt04_variance_advisory_close.sql`。
  - `src/FeeVariancePanel.tsx`、`src/fee-variance.css`、`src/fee-variance-panel.render.test.ts`、`src/TaxWorkpaperPanel.tsx`。
- 已实现：
  - 六类差异 `underpaid/overpaid/unmatched/cross_period/many_to_one/one_to_many`；表只追加身份字段，禁止删除。
  - 差异原因来自 active 核算政策 `fee_variance_reasons_json`；无配置时只能 `unresolved`。`unresolved` 不能处理，必须补证据；未知原因 409。
  - `FeeVarianceService.record/list/handle/reverse`，幂等键同请求复用、异内容 409；处理/撤销写审计。
  - 月结权威预检新增 `fee_variance_pending` 检查；存在 pending 差异时 `blocked`，并在复核/锁期再次调用 `_assert_no_open_fee_variances`，409，不自动生成凭证。
  - HTTP：`GET/POST /api/tax/books/{book_id}/fee-variances`、`POST /api/tax/fee-variances/{id}/handle|reverse`；`main.py` 注册 `fee_variances`。
  - PG 294/296 已实测：政策/差异表、RLS book scope、identity 不可改、禁删；`_reasons` 同时接受 TEXT `[]` 与 PG JSONB list；预检契约 7→8 的函数替换与触发已在新库验证。预检检查契约从 7 项扩到 8 项，旧 `preflight_passed` 运行在升级后需重新预检，不能沿用旧 checks。
  - 迁移 210/295 增加 `processing_started_at/by`；`FeeVarianceService.start` 供处理人认领，`_view.state` 在 pending+已认领时投影为 `processing`，底层 `status` 仍为 pending 以继续阻断月结。
  - `FeeVarianceService.reasons` 与 `GET /api/tax/books/{book_id}/fee-variance-reasons` 返回 active 政策原因及 `unresolved` 默认值。
  - 前端 `FeeVariancePanel` 列出差异/差额/状态/原因/证据，支持登记差异、认领、按政策原因和证据处理、撤销；登记表单金额经 `parseYuanToCents`，处理展示经 `formatCents(BigInt(...))`；409/422 只显示服务端消息，不自动重试；移动端原因/证据单列。
  - 政策原因可携带借贷科目；配置原因缺失科目或借贷相同 409。`handle` 以差额绝对值写银行 `sources`、`drafts` 和平衡的两行 `draft_lines`，记录 `draft_id` 与审计 `fee.variance.draft`。
  - 复核时若 `draft_id` 对应的差异 `handled_by` 是当前复核人，403“差异处理人不能复核自己生成的凭证”；另一名有复核权限的会计可独立复核。
  - 六类差异 `underpaid/overpaid/unmatched/cross_period/many_to_one/one_to_many` 各有参数化用例：登记→处理→生成凭证→处理人自审 403→会计独立复核通过。另测：差额恰好 5.00 时不自动归类为手续费；同一差异重复处理 409；关期 guard 返回 403。
- 验收命令与输出：
  ```text
  cd nexus_backend && PYTHONPATH=.. python -m pytest tests/test_plt04_fee_variances.py tests/test_plt04_fee_variances_api.py tests/test_b04_pilot.py tests/test_stage12_workspace.py -q
  39 passed, 2618 warnings in 76.61s；b04 预检单例另 1 passed/9.54s（含真实锁期后差异登记 409）
  ```
  ```text
  # PG17 / PG16（新库已应用到 297，latest handle 凭证逻辑已重跑）
  ACCOUNTING_ADMIN_DATABASE_URL=<local-admin-url> python scripts/migrate_accounting.py migrate
  # 迁移完成，最高版本 297（两库各一次）
  python scripts/test_accounting_postgres_isolated.py --config tmp_test/plt02b-pg17/unified.json nexus_backend/tests/test_plt04_fee_variances_postgres.py -q
  2 passed, 1 warning in 8.10s
  python scripts/test_accounting_postgres_isolated.py --config tmp_test/plt02b-pg16/unified.json nexus_backend/tests/test_plt04_fee_variances_postgres.py -q
  2 passed, 1 warning in 5.47s
  ```
  ```text
  cd nexus_backend && python -m ruff check app/tax/variances.py app/tax/router.py app/b04_close/service.py app/opening/models.py app/opening/service.py tests/test_plt04_fee_variances*.py
  All checks passed!
  # SQLite 211/212 已由全部新建测试库应用；PG 296/297 已在两套新库应用并重跑差异测试。
  ```
  ```text
  npm test -- --run
  158 files / 1425 tests passed
  TMP=tmp_test/build-tmp TEMP=tmp_test/build-tmp npm run build
  build passed: index 568.77 kB / gzip 176.35 kB; CSS 101.46 kB / gzip 20.64 kB
  ```

  ```text
  npx playwright test e2e/plt04-fee-variances.spec.ts --reporter=line
  2 passed (31.7s)
  # 1366×768 / 390×844：页面登记无对应应收 5.00 → 认领 → 选择政策原因
  # “银行手续费”（借 1002 / 贷 6602.02）+ 证据 → 保存处理 → 已处理并生成草稿；
  # 再点“打开差异草稿”进入“原件与凭证，一起核对”；无 pageerror。
  ```
- 仅合成验证：单一企业/单一账套；未使用客户真实规费、银行回单或审批政策。
- 需客户或人提供：五家客户对“差异原因”的正式政策、可接受的证据类型、审批人。
- 已补前端草稿入口：处理完成的差异行显示“打开差异草稿”，经 `DeliveryWorkbench → TaxWorkpaperPanel → FeeVariancePanel` 回调打开现有同屏复核抽屉；e2e 在 1366/390 点击后断言“原件与凭证，一起核对”，**2 passed/31.7s**。
- 未做/未验证：政策提示的“登记→预检→复核→锁期”专项 e2e 未做（B 验收不强制）；`processing` 仅为工作流投影（DB status 仍 pending）；审计查询页未做；跨期到账已在真实锁期后验证 409；`periods.status` 不是关账锁期来源，实际锁期读 `internal_period_locks`，本次已修正服务层 guard。
- 新增已验：差额恰好 5.00 不自动归类为手续费；同一差异重复处理 409；PG16/17 两线程同时处理同一差异均为 1 成功 + 1 冲突，且只生成一张 draft。
- 政策提示：允许政策原因 `code="unresolved"` + `blocks_close=false`；`_fee_variance_check` 返回 `pass + advisory=true`，不阻断 `_assert_no_open_fee_variances`。新增迁移 212/297 替换 SQLite trigger 与 PG `rr_close_checks_valid`，让 DB 守卫接受政策提示；两套 PG 库已迁移到 297，`test_plt04_fee_variances_postgres` 各 2 passed（8.10s/5.47s）。服务级政策用例 `test_policy_unresolved_reason_can_make_pending_advisory` 通过；未做专项 e2e 验证“登记→预检→复核→锁期”全链，因此不宣称政策提示全链通过。
- 顺带发现：历史 `accounting_policies` 行新增 `fee_variance_reasons_json DEFAULT '[]'`；既有测试创建政策后行为不变。
- 当前候选：HEAD `c8da023777fff5c7eeb7311067cdcd7e8244e61b`、source **2461/`f91346e5a9a627dee30b6c70cc3b0c3eefa7e79107d93394d39b82e0231eb301`**、migration **495/`7272abe6403b61632d5145ae1f30145ed09658f4466bc206d1fea65495e6f633`**。

## PLT04-C 未匹配流水的责任与待办  <状态：完成（合成身份/合成银行流水；待办直达、主管未分配视图、同屏事实/候选、转交与撤权、桌面/手机浏览器通过）>

- 现状核实：pending `fee_receivable_variances` 已能作为独立 `fee_variance` 工作台待办；处理人未指派时只对账套经理可见，指派后仅目标处理人可见；无编辑权限的会计看不到任务。
- 修改文件：
  - `nexus_backend/app/workspace/service.py`：`KINDS` 与 `_task_gates` 增加 `fee_variance`；投影分支与直接查询分支输出 `processing_started_by`、`ABS(difference_cents)`、期间；`_decorate_task_envelopes` 增加 `assignee_id`、`bank_source` 和最多 5 条开放应收候选。
  - `nexus_backend/app/workspace/projection.py`：新增 `fee_variance` 投影，`ref` 指向差异 ID，`state='pending'`，投影响应来源事实。
  - `nexus_backend/app/tax/variances.py`：`list` 返回流水事实与候选应收；新增 `assignees`（仅账套 editor）与 `assign`（仅 manager、仅 pending、不得转给本人、目标必须有 editor 权限，只更新处理人与时间并审计 `fee.variance.assign`，不授予权限）。
  - `nexus_backend/app/tax/router.py`：`GET /books/{book_id}/fee-variance-assignees`、`POST /fee-variances/{id}/assign`。
  - `src/workspace-types.ts`、`src/App.tsx`、`src/WorkspaceRouteContent.tsx`、`src/DeliveryWorkbench.tsx`、`src/TaxWorkpaperPanel.tsx`：新增 `fee_variance` 任务类型与直达税费底稿的目标参数；目标差异行滚动并高亮。
  - `src/FeeVariancePanel.tsx`、`src/fee-variance.css`：同屏显示流水日期/摘要/对方/金额、候选应收、当前处理人；新增“转交处理”选择人员与原因，成功刷新。
  - 新/改测试：`nexus_backend/tests/test_plt04_fee_variances.py`、`test_plt04_fee_variances_api.py`、`e2e/plt04-fee-variances.spec.ts`。
- 失败测试与修复：`WorkspaceService.list` 返回键实际为 `tasks` 而非 `items`；`branch()` 默认 `event_id` 引用 `e.id` 导致 SQL 错；`fee_receivable_variances` 无 `updated_at`，改用 `processed/handled/created` 的显式时间；转交后页面仍显示点击前的“未分配”，改为转交成功后重读任务；`status` 在转交后为 `processing` 但仍需允许继续选择原因，前端条件从 `pending` 放宽为 `pending|processing`（仅界面可用性，不改变服务端 pending 状态）。
- 验收命令与输出：
  ```text
  cd nexus_backend && PYTHONPATH=.. python -m pytest tests/test_plt04_fee_variances.py tests/test_plt04_fee_variances_api.py tests/test_b04_pilot.py tests/test_stage12_workspace.py -q
  41 passed, 2630 warnings in 98.47s
  ```
  ```text
  npx playwright test e2e/plt04-fee-variances.spec.ts --reporter=line
  2 passed (54.3s)
  # 1366×768 / 390×844：从“核对到账差额”待办进入税费底稿，目标行高亮；
  # 转交财务后显示当前处理人；保存政策原因与证据后生成差异草稿。
  截图：`tmp_test/plt04c-task-card-1366.png`、`plt04c-task-card-390.png`、`plt04b-fee-variance-1366.png`、`plt04b-fee-variance-390.png`
  ```
  ```text
  npm test -- --run
  158 files / 1425 tests passed
  npm run typecheck / lint / format:check
  passed（lint 0 error / 6 unused-disable warnings）
  TMP=tmp_test/build-tmp TEMP=tmp_test/build-tmp npm run build
  build passed: index 569.51 kB / gzip 176.54 kB; CSS 101.46 kB / gzip 20.63 kB
  ```
- 仅合成验证：是。单一合成企业/账套、合成流水与候选应收；未使用客户真实流水、银行回单或客户正式差异政策。
- 需客户或人提供：五家客户真实“未匹配/差额”样本、正式差异原因与证据政策的签字确认；真实设备/用户走查。
- 未做或未验证：候选应收当前只读展示，不支持把候选直接转为核销；转交并发/双人竞争未做专项测试；审计查询页未做。
- 当前候选：HEAD `c8da023777fff5c7eeb7311067cdcd7e8244e61b`、source **2461/`436ed8ec1fadd9a272c321a4204fae9b6ba2b20870a7eb6923c7d0186d3c8a17`**、migration **495/`7272abe6403b61632d5145ae1f30145ed09658f4466bc206d1fea65495e6f633`**。

## PLT08 助手办理卡片（结算差额离线实验）  <状态：本地验收通过（离线回放协议/合成数据；真实模型效果、真实会计计时和真实客户资料未验）>

- 现状核实：助手原有 `expense_bridge`、`invoice_voucher_diff` 两类分析，无平台结算与银行到账差额；调查工具只有 voucher/close/delivery 三类。本包新增确定性 `settlement_difference`，不新增模型自主工具。
- 修改文件：
  - `nexus_backend/app/assistant/analysis.py`：`KINDS` 加 `settlement_difference`；新增 `_source_row` 和 `_settlement_difference`。金额使用整数分；无手续费为 `partial/unresolved` + `missing=["fee_evidence"]`，等额手续费为 `complete/fee_evidence`，不等为 `partial/fee_evidence_mismatch`，跨期为 `partial/cross_period_evidence`；返回 handoff、`action_target`、coverage、source version 与 fingerprint。
  - `nexus_backend/app/assistant/router.py`：透传 `settlement_source_id/bank_source_id/fee_source_id`。
  - `src/analysis-result.ts`、`src/AnalysisEvidencePanel.tsx`：新增“平台结算与银行到账差额”核对类型，展示两笔金额、差额、原因、缺件、来源名称/日期/版本/原件引用和原记录链接。
- 验收命令与输出：
  ```text
  cd nexus_backend && PYTHONPATH=.. python -m pytest tests/test_assistant_settlement_difference.py -q
  7 passed, 1 warning in 16.52s
  # 无手续费 350.00 + 会计 handoff；等额手续费归因；340.00 mismatch；
  # 跨期同额手续费不得作为本期依据；跨企业 404 不泄露；换版 409；撤权 403。
  ```
  ```text
  npx playwright test e2e/plt08-settlement-difference.spec.ts --reporter=line
  1 passed (17.9s)
  # 真实本地导入：平台结算 100,000.00、银行到账 99,650.00、手续费 350.00；
  # partial 状态显示 350.00、待核对、缺手续费、来源版本、会计 handoff；
  # 补齐手续费后显示“已由手续费明细 350.00 元解释”，链接打开正确账套/来源。
  ```
  ```text
  npm test -- --run
  158 files / 1425 tests passed
  npx tsc -p tsconfig.app.json --noEmit
  passed
  TMP=tmp_test/build-tmp TEMP=tmp_test/build-tmp npm run build
  passed: index 569.51 kB / gzip 176.53 kB; CSS 101.46 kB / gzip 20.63 kB
  ```
- 截图：`tmp_test/plt08-settlement-difference-1366.png`、`tmp_test/plt08-settlement-difference-partial-1366.png`、`tmp_test/plt08-settlement-difference-390.png`。
- 场景结论：
  - 只有结算单和到账：显示 100,000.00、99,650.00、差额 350.00，原因待核对；明确缺手续费/扣款明细，handoff 给会计，可从卡片打开本账套原记录。
  - 有本笔、已核验的 350.00 手续费：归因为手续费，引用正确来源和版本；不再显示“原因未知”。
  - 跨期材料：不同月份的同额手续费只给 `cross_period_evidence`，不能解释本期差额。
  - 另一法人同额手续费：跨企业来源直接 404，不出现在 facts/来源/链接；企业隔离仍由服务端查询和权限决定。
  - 换版/撤权：换版 409、撤权 403；迟到内容不恢复。
- A/B/C 离线对照：`tmp_test/pilot/PLT08-ABC/settlement-difference/REPORT.md`，使用同一 5 个封存样本和固定脚本，非真实模型。A 错误归因 3/5、来源正确 2/5、3 次重复无效修改，并因跨企业泄漏/撤权缓存被硬否决；B 1/5、4/5、1；C 0/5、5/5、0。未选中候选，人工核查分钟数待测。结论仅为“回放协议已验证”，不能证明改良历史提高模型提议质量，也不能证明会计省时。
- RSI：`docs/pilot/assistant_rsi/settlement_difference/round-01.json`，事前冻结“跨期同额手续费”问题/根因/改法，事后记录 `cross_period_fee_same_amount` 修复、7 passed、保留决定；未使用验证集标签或逐样本反馈。
- 未做/未验证：真实模型、真实 OCR/银行/平台回单、真实会计计时、真实客户签认；跨浏览器只跑 Chromium；A/B/C 使用固定脚本而非模型。
- 当前候选：HEAD `c8da023777fff5c7eeb7311067cdcd7e8244e61b`、source **2468/`0365aa84fe59388cd60396774153ba822f72666776a1f7008ed3ee03c235be49`**、migration **495/`7272abe6403b61632d5145ae1f30145ed09658f4466bc206d1fea65495e6f633`**；未提交/推送/部署。

## PLT04-D 报表下钻到原始行  <状态：本地验收通过（合成身份/凭证；快照、科目层、反向入口、银行导入与非导入来源原件、PG16/17、1366/390 浏览器上传/下载均通过；真实客户原件与生产规模未验）>

- 现状核实：`reports/profit.overview` 已返回 `items_count`、`items[]`（含 voucher/line/source/evidence）以及 `checks.unmapped_line_count`/`mixed_voucher_contributions`；`TaxFPanel` 误用 `item_count`/`unmapped_count`/`mixed_voucher_count`，凭证行与提示为空。
- 修改：`src/TaxFPanel.tsx` 改用正确字段；补充凭证行 `voucher_id/voucher_no/voucher_date/line_no/source_ref`；选中利润行时以 `sum(items.net_cents)` 与上层 `amount_cents` 比对，不一致显示 `role=alert`。
- 红绿：`e2e/tax-f.spec.ts` 新增“凭证行 1”“未映射 4 行”“混合凭证 0 张”断言；修字段前实际为空串，修复后通过。复核人税表面板此前因只有 book reviewer 权限、无 company membership 而显示“所属单位待确认”；在 `e2e/formal-fixtures.ts` 为复核人增加 company `admin` 测试夹具后，**1366/390 两测均通过，2 passed/1.4m**。产品独立复核仍使用独立身份，未放宽服务端复核校验。
- 全量前端 `npm test -- --run`：**158 files / 1425 passed**。
- 反向入口：新增 `ProfitDrilldownService.impacts` 与 `GET /api/reports/profit/impacts`，按凭证 SQL 聚合其贡献的报表行；`TaxFPanel` 凭证行提供“影响报表行”，显示行名与金额。`test_tax15_profit.py` **3 passed/13.19s**；`e2e/tax-f.spec.ts` 先因点击第一行“管理费用”（0 凭证行）找不到按钮，改为点击“营业收入”行后 **2 passed/1.4m**。
- 原件：`ProfitDrilldownService.overview` 通过 `bank_import_source_files → bank_import_files` 解析每个利润凭证行的最新导入原件，返回 `original_file_id/original_url`；UI“来源”列在存在绑定时显示“打开原件”，否则显示证据摘要/“无原件绑定”。这是导入原件，不是任意 source 的通用原件层。`test_tax15_profit.py` 新增绑定断言，**3 passed/13.27s**。
- 快照：`ProfitDrilldownService.overview(..., report_run_id=...)` 读取 `report_runs.content_json`，按报表利润项目重算各行冻结金额，返回 `snapshot.report_run_id/version/status/content_hash/period/changed_line_codes`，每行 `live_amount_cents/snapshot_amount_cents/snapshot_changed`；UI 新增“下钻口径”下拉（实时/报表 Vn），快照行显示冻结金额与“实时 …”差异。`test_tax15_profit.py` 新增“报表后新增收入”用例，**4 passed/18.29s**；`e2e/tax-f.spec.ts` 选择报表 V1 并断言“金额冻结于报表 V… / 与当前账簿一致”，**2 passed/1.5m**。
- 科目层：`ProfitDrilldownService.overview` 每行返回 `accounts[]`（科目编码/名称/金额/凭证行数），UI 显示“利润行 · 科目层”汇总；`test_tax15_profit.py` 断言营业收入=6001/500000，**4 passed/17.24s**；`e2e/tax-f.spec.ts` 断言科目层含 6001/主营业务收入/¥1,000.00，**2 passed/1.5m**。
- 非导入原件：`source_original` 复用两段式上传意图；SQLite 213/PG 298 增加 `source_originals`、purpose 与绑定候选/预期 CASE；`TaxSourceAdapter.attach_original` 校验企业/账套/`review` 权限，`uploads.consume` 事务内登记，404/409/撤权边界由上传绑定层提供；`ProfitDrilldownService` 返回 `original_url`。`test_plt04_source_originals.py` SQLite 服务/HTTP/权限 **3 passed/14.16s**；SQLite 模式 `3 passed,1 skipped`（非 reviewer 与跨账套均为不可探测 404，不泄露对象是否存在）；`test_plt04_fee_variances_api.py` **1 passed**；合并 `test_source_originals + fee_variances + tax15_profit` **18 passed/52.26s**；ruff 通过。
- PG 实测：PG17 `test_plt04_fee_variances_postgres.py` **2 passed/7.91s**，迁移最高 **298/285**，purpose CHECK 含 `source_original`；PG16 source original **1 passed/8.93s**，PG17 source original **1 passed/4.34s**。临时库 `ak01_local_ak05_plt04_src`，测试后未删（后续环境收尾处理）。
- 浏览器实测：`e2e/plt04-d-source-original.spec.ts` 用真实文件上传→绑定→下载，1366/390 **2 passed/40.9s**；`TaxFPanel` 的“打开原件”经 `apiBlob` 带 Bearer/公司头取回后下载，文件名 `来源原件-<voucher_no>.pdf`；截图 `tmp_test/plt04-source-original-1366.png`、`-390.png`。`npm run typecheck`、`prettier --check src/TaxFPanel.tsx e2e/plt04-d-source-original.spec.ts` 通过；全量前端 `158 files/1425 passed/4.09s`（仅有既有 useLayoutEffect/ExperimentalWarning）。
- D 本地通过时候选：HEAD `c8da023777fff5c7eeb7311067cdcd7e8244e61b`，source **2474/`51de959d82daa7e678a926dbf63ab2a626456df608611e8f0c7dbebefad508bb`**、migration **497/`79c5b5cf96bff35b1a4c13110a890d91a2ea4f5ff10e8c3aa7c8e10895d9f2db`**；未提交/推送/部署。剩余外部门槛：真实客户原件、生产文件存储/规模、真实模型/通知。

## PLT04-E 真实月份走查工具包  <状态：工具就绪待客户输入（清单、只读校验、交接模板与合成演练通过；真实客户样本/授权/签字未提供）>

- 交付：`docs/pilot/TAXI_WALKTHROUGH.md`（客户授权样本、脱敏、六步责任人/记录/判据、通过标准、禁止用合成数据填真实记录）；`docs/pilot/templates/taxi_walkthrough_handoff.csv`（每次交接一行，含“是否需要开发人员解释”“是否需要改库”两列）。
- 只读校验：`scripts/validate_taxi_walkthrough.py` 复用 `app.import_wizard.parser` 的 CSV/XLSX 大小、编码、表头、日期和金额规则，检查交易编号/摘要/金额或收支列/本期日期/金额格式/零金额提示；不建库、不写对象存储。
- 合成演练命令与退出：
  ```
  PYTHONIOENCODING=utf-8 python scripts/validate_taxi_walkthrough.py \
    --period 2026-10 --json tmp_test/plt04e/taxi-synthetic.csv
  ```
  `status=passed`、`rows=2`、`total_cents=9965000`、`errors=[]`、`warnings=[]`；输出明确“只读预检，不入库、不替代客户确认或真实导入”。
- 红绿：`nexus_backend/tests/test_plt04_walkthrough.py` 先红（文件不存在/路径错误），实现后 **3 passed/1.08s**：正常样本、跨期+坏金额被拒绝、缺交易编号/摘要被拒绝。
- 未验证：真实一个月结算单/银行流水、客户确认的差异样本、五家签认、现场耗时/求助/离开 Excel 记录；因此不得写“真实走查通过”。走查通过标准要求正常款+客户确认差异各一次、会计能从利润表到原始结算行/从未匹配流水到处理人、全程不改库且不靠开发解释。
- E 工具加入后的当前候选：source **2474/`51de959d82daa7e678a926dbf63ab2a626456df608611e8f0c7dbebefad508bb`**、migration **497/`79c5b5cf96bff35b1a4c13110a890d91a2ea4f5ff10e8c3aa7c8e10895d9f2db`**；未提交/推送/部署。

## PLT05-A 系统分工与三态模型  <状态：本地验收通过（SQLite、PG16、PG17、1366/390 浏览器；真实 T+ 未联调）>

- 现状核实：政策无“正式账所在系统”字段，报表无法区分本系统/外部系统正式账；`integrations.service` 只有单一 outbox `state`，`exported` 容易被读成已接收/已过账。
- 修改：
  - 前向迁移 214/299：`accounting_policies`、`report_runs` 增 `formal_book_system`（unassessed/local/external，默认 unassessed）与 `formal_book_system_name`；未改历史 checksum。
  - 政策：external 必须有≥2 字名称，非 external 禁止名称；报表生成时冻结两字段；unassessed 追加正式原因“正式账所在系统未核定”，继续只能试算。
  - 三态：`_external_state` 从 artifacts/receipts 分别计算 `statuses.exported/received/posted`（时间、依据、登记信息），另给 `result_summary`、`partial_failure`、`result_unknown`；overview/get_outbox 都返回，overview 另返回 `formal_book` 政策快照。
  - 前端：政策入口选择本系统/外部系统/未核定；报表标题区注明“正式账以 X 为准；不代表对方接收或过账”；集成工作台用三个独立 badge，未知显示“尚无对方结果，不自动重发”，部分失败提示逐笔核对。
- 红测/修复：`test_stage10_integrations.py` 的旧断言假设 outbox 无三态，现改为按 id/state 比较并断言三状态均空；`test_plt02_report_modes_postgres.py` 删除对过期 `c.policy["approved_by"]` 的比较；`PolicyCreate` 校验测试补必填字段并匹配真实中文错误；e2e local 文案按实际“外部交付仍需单独核验收货、导入与过账结果”修正；`formal_book_system: 'local'` 的转义错误在 4 个 fixture 修正。
- 后端：
  ```
  cd nexus_backend && PYTHONPATH=.. python -m pytest -q \
    tests/test_plt05_external_states.py \
    tests/test_plt02_report_modes.py \
    tests/test_stage10_integrations.py
  ```
  **18 passed/33.31s**；其中新文件 `test_plt05_external_states.py` **5 passed/13.44s**（仅导出无接收/过账、artifact 后 result_unknown、接收后 received 且 posted 空、list/detail 一致、无政策 unassessed、external 冻结、PolicyCreate 名称校验）。
- PG：
  ```
  export ACCOUNTING_TEST_POSTGRES_URL=$(python -c "import json;print(json.load(open('tmp_test/plt04-pg17-private.json'))['admin'])")
  export ACCOUNTING_TEST_DATABASE_URL=$(python -c "import json;print(json.load(open('tmp_test/plt04-pg17-private.json'))['app'])")
  export ACCOUNTING_TEST_WORKER_DATABASE_URL=$(python -c "import json;print(json.load(open('tmp_test/plt04-pg17-private.json'))['worker'])")
  export ACCOUNTING_PROJECTION_DATABASE_URL=$(python -c "import json;print(json.load(open('tmp_test/plt04-pg17-private.json'))['projection'])")
  export ACCOUNTING_AK05_ISOLATED_TARGET=1
  cd nexus_backend && PYTHONPATH=.. python -m pytest -q \
    tests/test_plt02_report_modes_postgres.py \
    tests/test_stage10_integrations_postgres.py
  ```
  PG17 report **1 passed/3.85s**、integrations **6 passed/9.08s**；PG16 report **1 passed/3.51s**；299 已在两库应用，最高 **299/285**，无历史 checksum 改动。
- 浏览器：
  ```
  npx playwright test e2e/plt05-formal-book.spec.ts
  npx playwright test e2e/stage10-integrations.spec.ts
  ```
  正式账 **2 passed/22.6s**；local 1366 断言本系统正式提示，external 1366 断言“正式账以 畅捷通 T+ 为准”“不代表对方已接收或过账”；另截 390。截图 `tmp_test/plt05-formal-book-1366.png`、`tmp_test/plt05-formal-book-390.png`、`tmp_test/plt05-formal-book-external-1366.png`。
- 导出/回执三态 UI：`stage10-integrations.spec.ts` **1 passed/21.2s**；导出后显示“已导出·日期 / 未接收 / 未过账”，登记回执后 `await load()` 刷新为“对方已接收·日期 / 未过账”，并断言页面不存在“对方已过账”。截图 `artifacts/stage10-financial-bridge.png`。这是本轮修复：此前只显示回执记录，outbox 三态要重新进入页面才刷新。
- 门禁：
  ```
  npm run typecheck
  npm run lint
  npm run format:check
  npm test -- --run
  npm run build
  ```
  typecheck 通过；lint **0 errors/6 existing warnings**；format:check 通过；前端 **158 files/1425 passed/4.60s**；build 通过（`index` 569.51 kB/gzip 176.55 kB，3.75s）。`test:frontend-budget` 仍报告既有 `initialGzip` 与 `homeCss 145755>145000` 违规（脚本退出 0），属 FE01 未选向/未授权修预算的已知外部门槛。
- 未验证/边界：真实 T+ 联调、集团汇总/助手回答/月结预检尚未接入三态显示（当前不显示，因而不会误标为已接收/过账）；PLT05-B 才能登记返回文件并逐笔核对。
- 候选：HEAD `c8da023777fff5c7eeb7311067cdcd7e8244e61b`，source **2478/`de56cb250199885f6f8191be509c477ab362e81c18700a67cea34597f0e8163c`**、migration **499/`e1e2b1150cb69472f69a4345f81408d491f681449a84fd3a5ab5a00450abf8b2`**；未提交/推送/部署。

## PLT05-B 返回结果的导入与逐笔核对  <状态：进行中（SQLite/PG16/PG17/浏览器导入、差异处置、手工证据原件与 UI 通过；返回差异任务投影服务已就绪，Workspace 待办与 handler/手工浏览器 e2e、真实 T+ 未完成）>

- 契约：新增 `docs/pilot/ROUNDTRIP_CONTRACT.md`，明确这是待真实 T+ 确认的假设契约；八列 `批次标识,凭证标识,对方凭证号,结果,失败原因,对方过账日期,金额,科目`，其中金额/科目用于判断 mismatch，缺失时不得判一致；`missing/extra/amount_mismatch/account_mismatch` 是差异而非成功。
- 红测：先建 `test_plt05_roundtrip_contract.py`，导入 `app.integrations.roundtrip` 失败（模块不存在）确认为红；随后实现 `nexus_backend/app/integrations/roundtrip.py`，只做解析/匹配，不写库、不冒充真实 T+。
- 绿测：`cd nexus_backend && PYTHONPATH=.. python -m pytest -q tests/test_plt05_roundtrip_contract.py` → **5 passed/0.37s**：
  - 全部 accepted 且有逐笔过账日期 -> received/posted；
  - 缺一笔 + 多一笔 -> partial、missing/extra、不 received；
  - 金额/科目与冻结清单不一致 -> amount_mismatch/account_mismatch；
  - 凭证标识重复或批次标识不符 -> 422；
  - 全部接收但缺过账日期 -> received 且 posted=false。
- 解析规则：UTF-8/BOM、≤2MB、1–10000 行、连续/非空、公式前缀拒绝、日期 ISO、金额两位小数、拒绝重复标识、按冻结凭证清单补 missing 行；返回 `counts/received/posted/status`。`ruff` 通过。
- 已落盘并验证（SQLite）：sqlite/215、postgres/300 返回文件与逐行差异表；`FinancialBridgeService.record_return/list_returns` 校验 artifact/冻结包、同批次同 hash 幂等、异内容 409、按 package vouchers 生成 exported、写 import/rows、审计 `bridge.return.import`；`_external_state` 已合并旧回执与新返回文件的 received/posted 与差异。
- 服务测试：`test_plt05_return_import.py` 3 passed（全部 accepted 且过账、金额 mismatch=partial/未接收、错批次 422）；与 `test_stage10_integrations.py`、`test_plt05_external_states.py` 合并 **14 passed/19.12s**；`ruff` 通过，`import app.main` 成功。
- PG：`test_stage10_integrations_postgres.py` 在 PG17 **8 passed/13.09s**、PG16 **8 passed/13.01s**；300/301 在双库应用；新增 mismatch→handle 用例。
- 前端：`IntegrationWorkbench` 增“返回文件逐笔核对”上传、批次/接受/差异/接收/过账摘要与逐笔明细；`typecheck`、Prettier、前端 **158/1425 passed/4.06s**；真实浏览器 `e2e/stage10-integrations.spec.ts` **1 passed/21.4s**（1366/390 上传返回文件→已接收已过账→展开逐笔明细，截图 `artifacts/stage10-financial-bridge.png`、`-390.png`）。
- 手工证据：`BridgeManualReturn`/`record_manual_return`/`POST /artifacts/{id}/returns/manual` 已接；证据<5、未接收先过账、错批次 422，同批次同 hash 幂等、异内容 409；写一条透明 `MANUAL` 整批登记行（不伪造逐笔凭证），`test_plt05_return_import.py` **5 passed/10.90s**，与 stage10/external_states **16 passed/20.37s**。手工 UI 与独立 HTTP 用例未做。
- 差异处置：`BridgeReturnRowHandle` + `handle_return_row` + `POST /artifacts/{id}/returns/{import_id}/rows/{voucher_code}/handle`；原因≥3、证据≥5，accepted/duplicate 409，同内容幂等、异内容 409，写 `bridge_return_row_handlers` 与审计；SQLite 216/PG 301，PG16/17 **各 8 passed/13.0s**；`test_plt05_return_import.py` **6 passed/11.64s**。
- UI：返回文件逐笔表新增“处置”列与内联表单（已解决/忽略、原因、证据、保存/取消）；“客户软件无文件时”折叠区可手工登记接收/过账、证据引用和备注。
- 前端复验：`npm test -- --run` **158 files/1425 passed/3.96s**；typecheck、Prettier、build 通过；handler/手工 UI 尚无浏览器 e2e。
- 证据原件：`bridge_return_evidence`（SQLite 217/PG 302）+ `POST/GET /returns/{import_id}/evidence` + 手工表单文件上传已接；证据 ≤2MB、PDF/PNG/JPEG 魔数，绑定 import/book/firm 权限；服务测试含错魔数 422/重复 409，`test_plt05_return_import.py` **9 passed/12.87s**；PG17/16 证据往返 **各 1 passed**（2.77/2.71s）。
- 仍未做：PLT03-B 差异待办映射、handler/手工 UI 浏览器 e2e、真实 T+ 契约；返回文件/证据 API 无独立 HTTP 用例。
- 接续（PLT05-C）：本节“仍未做”中的 PLT03-B 差异待办映射、handler/手工 UI 浏览器 e2e、独立 HTTP 用例均已完成，见下节；真实 T+ 仍未验证。
- 当前候选：HEAD `c8da023777fff5c7eeb7311067cdcd7e8244e61b`，source **2487/`98986251c5fe425b7536a5ecf8cb181b73977d96b4504460f1f23f14131cf3e6`**、migration **505/`969cc011de43c5bea4619ace590182623126522618c1524acce0735ed1d275b2`**；未提交/推送/部署。

## PLT05-C 返回差异接入统一待办  <状态：进行中（SQLite 待办/处置/手工回执/浏览器与 HTTP 成功路径通过；PG 303 未应用验证、真实 T+ 未验证）>

- 目标与口径：把 `FinancialBridgeService.list_return_tasks` 投影为 Workspace 待办 `kind='bridge_return'`，`ref='{import_id}:{voucher_code}'`（与 `GET /returns/tasks` 的 `object_id` 同一值，UI 不需要再解析）；仅对具备复核能力的人可见——待办过滤用 `review_books`（`src/App.tsx` 与 handler 写路径同一个 capability `"review"`），无复核权的人既看不到待办也无法处置；差异处置后该行从待办消失。
- 后端（`nexus_backend`）：
  - `app/workspace/service.py`：`KINDS` 增 `bridge_return`；`_task_gates` 增 `"bridge_return": reviewer`；`_query`（实时）与 `_projected_query`（投影）各增一个分支，均 `FROM bridge_return_imports i JOIN bridge_return_rows r` + `LEFT JOIN bridge_return_row_handlers h`，过滤 `h.import_id IS NULL AND r.status NOT IN ('accepted','duplicate')` 且 `EXISTS(review_books)`；状态词映射为中文标题（金额不一致/科目不一致/对方拒绝/对方未找到凭证/对方缺少凭证/对方多出凭证），金额为 `ABS(actual-expected)`。
  - `app/workspace/projection.py`：增 `bridge_return` 投影 INSERT（22 列与既有语句一致），供投影干净时直接读物化表。
  - `nexus_backend/migrations/sqlite/218_plt05_return_projection.sql`：为 `bridge_return_imports`、`bridge_return_row_handlers` 建 `AFTER INSERT` 置脏触发器（`workspace_projection_state.revision+1`）。
  - `nexus_backend/migrations/postgres/303_plt05_return_projection.sql`：**不复用** `workspace_projection_bump()`（117 号迁移给它加了来源表白名单，非白名单表会直接 `RAISE EXCEPTION 'Untrusted workspace projection trigger source'`），另建 `accounting.bridge_return_projection_bump()`（SECURITY DEFINER，校验 schema/AFTER/INSERT/表名/`TG_RELID`/`firm_clients` 关系），并 `REVOKE ALL ... FROM PUBLIC, accounting_app, accounting_worker`；两表各建 `AFTER INSERT` 触发器。
  - `app/integrations/service.py`：`list_return_tasks` SQL 增 `JOIN bridge_export_artifacts a` 并返回 `outbox_id`，供 UI 从待办直接定位冻结交付包；`app/integrations/router.py` 的 `GET /returns/tasks` 保留。
- 前端（`src`）：
  - 类型/文案：`workspace-types.ts` 的 `TaskKind` 增 `bridge_return`、`taskDestination` 返回 `{type:'bridge_return', id: task.ref, bookId: task.book_id}`；`task-labels.ts`、`task-language.ts`、`task-presentation.ts`（details/actionStates/capability/actionNext）、`workspace-experience.ts` 补映射与“筛选待办类型”选项（仅 `can_review`）。
  - 导航：`App.tsx` 新增 `integrationFocus` state（session 重置与 `navigate()` 都清空），`openWorkspaceTask` 的 bridge_return 分支走 `go('accounting')` → `setBookId` → `setIntegrationFocus(target.id)` → `setRouteView('integration')` → `initialize`；`WorkspaceRouteContent.tsx` 把 `integrationFocus` 透传给 `IntegrationWorkbench`。
  - `IntegrationWorkbench.tsx`：新增 `focus` prop；`loadArtifact` 抽成可复用的 `refreshArtifact`；`openFocusTask(ref)` 先 `GET returns/tasks?limit=200` 按 `object_id` 定位，再加载对应 outbox 并预置处置表单，找不到时提示“该差异已处置或不在当前待办中”；差异行在预置处置表单时自动展开。
  - 修复真实缺口：手工登记区的 `manualEvidence` 此前没有任何文件输入赋值，按钮 `!manualEvidence` 导致浏览器里永久禁用；已补“证据原件” `input[type=file]`（PDF/PNG/JPEG）。
- 测试与验收（本机真实输出）：
  - 后端：`cd nexus_backend && PYTHONPATH=.. python -m pytest -q tests/test_plt05_return_import.py tests/test_plt05_return_workspace.py tests/test_plt05_return_http.py` → **14 passed/19.56s**。
  - 新增 `tests/test_plt05_return_workspace.py`（3 条）：待办可见/字段/金额/期间/责任人 → 处置后消失；`refresh_company` 后 live 与投影逐行相等、处置后投影置脏；无复核权（`viewer`）看不到。
  - 新增 `tests/test_plt05_return_http.py`（2 条，独立 HTTP 成功路径）：`GET /returns/tasks` 含 `outbox_id` → `POST .../handle` 200 且再查为空 → 同参数重复 POST 幂等；`POST /returns/manual` 201 → `POST evidence` 201 → `GET evidence` 200（字节、`application/pdf`、`Content-Disposition`、`no-store`）→ 重复 attach 409。
  - 浏览器 e2e：`npx playwright test e2e/stage10-integrations.spec.ts --reporter=line` → **3 passed/29.5s**。新增两条：① 返回差异出现在 `/workspace/home` → 点卡片 → 落在财务软件接入 → 内联处置 → 卡片消失；② 浏览器选择证据原件 → 手工登记 → “已接收 · 已过账” → API 证据可下载。截图 `artifacts/plt05-return-task-handled.png`、`artifacts/plt05-return-manual.png`。
  - 前端门禁：`npm run typecheck` 通过；`npm test -- --run` **158 files/1425 passed/3.67s**；`npm run build` 通过（Windows esbuild 曾因系统 TEMP 目录 “Access is denied” 失败，把 `TEMP`/`TMP` 指到工作区 `tmp_build` 后 3.32s 成功；`index` 570.67 kB/gzip 176.89 kB 为既有体积提示）；Prettier 通过。
  - 回归：`tests/test_workspace_projection.py tests/test_stage22_workspace_reads.py tests/test_stage12_workspace.py` → **34 passed/1 failed/1 skipped**；唯一失败 `test_projection_matches_live_for_export_and_delivery` 是既有 maker-checker 失败（`c.ledger.approve` 抛“制单人不能独立审核本人创建版本”，发生在任何 workspace 查询之前），已把本包改动 stash 后在 HEAD 基线复跑同样失败，属预存基线问题，未修改。
- 未验证/边界：无 PG 环境，**migration 303 与两个触发器未在 PG16/PG17 应用或实跑**（仅代码审阅）；真实 T+ 契约仍未联调；PLT05-B 的 `test_plt05_evidence_http.py` 仍是 404/422 fail-closed 用例，未改成成功路径（成功路径在本包 `test_plt05_return_http.py`）。
- 候选：HEAD `074c7ad1f80797f3a194632ebf2c4d224a3882c7`；source **2398/`506953a34707d942bf4dd2475afba9d67271bba2204fc3ae042014f37f710094`**、migration **507/`7595c03157f7b07afa2de8b4b1e71b46d55c3a09eaf6214abf68e346712058c6`**。HEAD 提交已含本包主体（19 文件，+827/−36；含迁移 218/303、两份新测试、e2e 与前端接线）；提交后仍有 `e2e/stage10-integrations.spec.ts`（展开断言/本次操作依据/文件选择器修正）与 `src/workspace-experience.ts`（筛选选项）两处修改未提交；未推送、未部署。

## PLT05-D 六项缺口补强（P0-1/P0-2/P0-3/P1-4/P1-5/P1-6）  <状态：进行中（本机 SQLite/浏览器/HTTP/前端门禁通过；PG 304/305/307/308 与 RLS 用例未在 PG16/17 实跑）>

- 来源与口径：按用户给出的六项缺口清单逐项补强，先写失败测试再改实现；未修改既有迁移，新增前向迁移 SQLite 219/220、PostgreSQL 304/305/307。PG 环境不可用，涉及 PG 的策略/列/触发器只做代码审阅，不写“完成”。
- P0-1 回执原件账套隔离（已完成本机验证）：
  - `nexus_backend/migrations/postgres/304_plt05_return_evidence_book_scope.sql`：删除 `bridge_return_evidence`、`bridge_return_row_handlers` 的 firm-only 策略，改为读/写两条策略，条件为 `accounting.book_allowed(book_id[,TRUE])` + `EXISTS(SELECT 1 FROM bridge_return_imports i WHERE i.id=import_id AND i.book_id=book_id AND i.company_id=company_id AND i.firm_id=accounting.actor_firm())`；同所跨账套、跨法人、撤权均被拒。
  - `nexus_backend/app/integrations/service.py` 的 `attach_evidence` 改用写权限（`_scope(..., True)`），并先做 base64/魔数校验再查账套，避免非法输入把 422 变成 404。
  - 新增 `nexus_backend/tests/test_plt05_return_rls_postgres.py`：`c.unassigned`（firm reviewer、无账套）读到 0 行、`c.reviewer` 读到 1 行、无账套 INSERT handler 抛 `InsufficientPrivilege`、删除 `book_access` 后立即 0 行；无 PG 时 skip。
- P0-2 外部过账误确认（已完成本机验证）：
  - `record_return` 校验返回文件批次标识等于冻结交付批次（否则 422“批次标识与冻结交付批次不一致，拒绝登记”）。
  - 新增 `bridge_return_imports.evidence_verified`（SQLite 219、PostgreSQL 305，PG 需 `GRANT UPDATE(evidence_verified)`）：文件回执为 1，手工回执只有附证据原件才为 1；`attach_evidence` 写入证据后置 1。
  - 返回行无金额或无科目时只确认“接收”，`posted` 要求每行有对方过账日期、金额与科目；手工登记“已过账”必须填写对方过账日期，`_return_view` 同时返回 `posted_declared`/`evidence_verified`，`_external_state` 只有两者皆真才显示已过账。
  - 修复手工回执把 `evidence_ref` 存成证据字节、备注为空时 reason 写入 `None`、过账日期用当天等缺陷；下载头改 RFC 5987（ASCII fallback + `filename*=UTF-8''…`），`客户回执.pdf` 不再 500。
  - 前端 `IntegrationWorkbench` 手工区增“对方过账日期”输入并纳入禁用条件，证据上传后重取 returns 以即时反映核验状态；e2e 手工用例补填过账日期。
  - 测试：`test_plt05_return_import.py` 12 passed；`test_plt05_return_http.py` 用中文文件名断言 `filename*=UTF-8''%E5%AE%A2%E6%88%B7%E5%9B%9E%E6%89%A7.pdf` 与 `evidence_verified=True`。
- P0-3 出租车差额归因（已完成本机验证）：
  - 新增 `fee_source_relations`（SQLite 220 / PostgreSQL 307）：把结算单、银行到账、手续费凭据及其版本、金额、差额绑定成一条经核对的关系，`relation_sha256` 防换版；PG 开 RLS（`book_allowed` 读/写）并挂 `batch2_company_write_gate`。`fee_receivable_variances` 增 `relation_id`。
  - 新增 `app/tax/relations.py`：`SourceRelationService.verify/verify_in/find`，校验来源属于本账套、settlement/fee 为业务单据、bank 为银行流水、期间一致、金额与差额一致、证据非空；同摘要幂等，换版 409“来源关系已变化，请重新核对”。
  - `app/assistant/analysis.py`：结算单/手续费必须是业务单据、结算/到账必须属于当前期间；手续费金额与差额一致但无已核对关系时返回 `partial/unverified_source_relation`（不再宣称“已由手续费解释”），关系不含手续费时 `fee_relation_mismatch`，版本不符 409。
  - `app/tax/variances.py`：登记时校验 `bank_source_id` 是本账套银行流水且金额等于 `actual_cents`；同时给结算单/手续费时强制建立关系，并要求关系差额与登记差额一致。
  - 测试：`test_assistant_settlement_difference.py` 9 passed（含同额无关手续费、换版 409）；`test_plt04_fee_variances.py` 15 passed（含非银行来源 422、金额不符 422、关系差额不一致 422、关系绑定成功）。
- P1-4 报表差异下钻指错依据（已完成本机验证）：
  - `reconciliation.py` 的 `system_by_key["section"]` 由中文表名改为 `group`；新增 `_profit_item_section`（优先冻结的 income/expense，旧内容按科目前缀 6001/6051/6111/6301 与 6401/6402/6403/6601/6602/6603/6711/6801 回退）；`_drilldown` 的利润收入/费用按归属下钻，缺明细回退合计，未知 section 返回空，不再全部落到现金流。
  - `reports/service.py` 新增 `_assert_mapping_current`：正式报表的 `mapping_fingerprint` 与当前科目映射不一致时，`validate/reconcile/explain_difference/review/publish` 均抛 409“科目到报表行次的映射已变化，请重新生成报表”；解释写库经 `_report(..., write=True)` 绑定报表、账套与复核权限。
  - `test_plt02_reference_reconciliation.py` 8 passed（利润收入/费用下钻科目、映射变更后 reconcile/explain/review 409、employee 无复核权被拒、解释记录 report/book/firm 绑定）。
- P1-5 待办定位与返回（已完成本机验证）：
  - `src/FeeVariancePanel.tsx`：目标不在当前期间时显示 `role=status` 提示并保留人工核查入口；目标存在时提示“已定位到待办中的这笔差异”，`requestAnimationFrame` 后 `scrollIntoView` + `focus`，目标 `li` 加 `tabIndex={-1}`；`src/fee-variance.css` 增 `.fee-variance-target`。
  - 新增 `e2e/plt04-fee-variance-target.spec.ts`：真实 API 建 2026-10 书与规费差异待办，桌面 1280×900 与手机 390×844 均验证目标行可见/高亮、URL 保留 `period=2026-10` 与 `company=`、点“返回待办”回到 `/workspace/home` 且卡片仍在。
- P1-6 发布证据（已完成本机验证）：
  - `../.venv/Scripts/python.exe -m ruff check app tests` → All checks passed；`npm run lint`、`npm run typecheck` 通过；`npm run test` → 158 files/1425 passed/3.97s；`npm run build`（TEMP/TMP 指到工作区 `tmp_build`）→ built in 3.65s。
  - 重建后 `npm run test:frontend-budget` → violations: []（initialJs 566,623/570,000；initialGzip 175,537/176,000；homeCss 144,715/145,000）；此前超限数字属旧 dist，不据此改预算。
  - `tests/test_migration_structure.py tests/test_migration_compatibility.py tests/test_release_migration_history.py` → 16 passed/7.96s。边界：219/304/305 的 compatibility 摘要由外部并发进程登记在 `nexus_backend/tests/migration_structure_debt.json`（本包未改该文件）；306/308 为外部并发批次迁移，不属于本包。
- 联合验收（本机真实输出）：
  - `pytest tests/test_plt05_return_import.py tests/test_plt05_return_workspace.py tests/test_plt05_return_http.py tests/test_plt04_fee_variances.py tests/test_assistant_settlement_difference.py tests/test_plt02_reference_reconciliation.py -q` → **49 passed/89.12s**。
  - `npx playwright test e2e/stage10-integrations.spec.ts e2e/plt04-fee-variance-target.spec.ts --reporter=line` → **4 passed/50.2s**（返回差异待办处置、手工登记、原集成底座、规费差异桌面+手机）。
- 未验证/边界：无 PG 环境，**304/305/307（及外部 308）未在 PG16/17 应用或实跑，RLS 用例仅 skip**；真实 T+ 契约仍未联调；P1-4 的旧正式报表失效按“拒绝继续复核/对账/解释”处理，未回写报表状态列；PLT05-D 前端增量已由重建后的预算门禁覆盖，但后续新增代码仍受同一预算约束。
- 候选：HEAD `074c7ad1f80797f3a194632ebf2c4d224a3882c7`；source **2411/`39f21b945a4da9914fb02d5b9fc753b2e02984fc19cdef6bbadcc0977888f374`**、migration **514/`046d9ac0baf39faf734145c9030b171e093263b749ba1f88786db436d8b76533`**。本包改动与既有 120+ 项工作区改动混存，未提交/推送/部署。
- PLT08 调查助手防幻觉与 60 样本留出评测（已完成本机验证，Batch 09-1.2 / Step 5b）：
  - 新增 `nexus_backend/app/assistant/answer_contract.py`：六段固定结构（结论 / 依据 / 差额或数字 / 未检查的范围 / 由谁处理 / 办理入口），空段显式填「无」；`guard_narrative` 只接受服务端已核验的金额与对象标识，命中未获证据支持的归因词（手续费/服务费/银行扣费/汇兑损失/坏账/舞弊/挪用）时整段改写成「存在差异，原因未确认」，并记 `plt08_attribution_rewritten`；模型散文里的金额或 ID 与证据不符则丢弃散文并记 `plt08_unknown_amount` / `plt08_unknown_reference`；无依据时 `plt08_abstained` 弃答，模型多写一句不能把弃答升级成结论。`has_attribution_claim` 只扫结论段并用「这是/属于/原因是…手续费」这类断定句式，避免把「缺手续费凭据」这种缺件说明误判成归因。
  - 新增 `nexus_backend/app/assistant/paths.py`：三类确定调查路径——「本月为什么不能交付」（阻断项严格等于月结权威预检 `close["blockers"]`，不多不少；无预检记录/交付包缺失进「未检查的范围」，已批准企业规则以 `rule` 对象进「依据」并带版本）、「这笔收入为什么与回款不一致」（结算单/银行到账/差额/手续费凭据四组数字 + 来源版本 + 未确认原因）、「这个数字来自哪里」（逐笔凭证构成，跨期凭证与超限明细显式降级为 `partial`，结论写明「其中 N 张不在本期」）。三类共用 `BookReader`（真实库）与 `SampleReader`（样本）两个读取端口，评测与线上走同一套组装逻辑。
  - 新增 `nexus_backend/app/assistant/memory.py` + 双库迁移 PG `326` / SQLite `233`（`assistant_answers`、`assistant_preferences`、`assistant_rule_proposals`）：个人偏好只在本人名下、可删除且删除留审计；企业规则必须另一人批准（提议人自审 403「不能批准自己提议的企业规则」）、payload 拒绝任何金额/阈值/费率字段（422「企业规则不能包含金额、阈值或费率字段」）、按单位隔离、撤销或过期立即失效、引用时在「依据」写明规则 ID 与版本；「当前事实」不落库，每次重读来源。回答快照与规则版本双库都加禁改禁删触发器。
  - 新增 `nexus_backend/app/assistant/answer_store.py` + `answer_router.py`（`/api/assistant` 下 `answers` 与 `preferences`、`rules` 端点）：回答落库时记录依赖对象及版本，读取时逐项重算；任一依据换版即标 `invalid` 并隐藏旧结论（返回 `visible=false` +「结论已失效并隐藏，请重新调查」+ `reinvestigate`），撤权直接 403「当前账号已无权查看该账套的调查回答」且不泄露原结论与金额；同问题同指纹复用不重复计费，结果未知记 `cost_state="unknown"` 且**不自动重试**；调查全程零写业务数据。
  - 有界动作卡：`nexus_backend/app/assistant/actions.py` 新增 `review_queue_pull`（拉起高风险复核清单）与 `missing_evidence_draft`（生成缺件催办草稿），沿用「不可变预览 + 64 位指纹 + 显式确认 + 请求键幂等 + 30 分钟过期」，确认只写回执（`created=0`、`exceptions=0`），不审批、不发送、不授予权限；预览与回执落在双库新迁移 PG `327` / SQLite `234` 的 `assistant_card_plans`/`assistant_card_receipts`（RLS + `company_write_table_guard` + 禁改禁删触发器），旧 `assistant_action_plans` 的三类校验证明保持原样未重写。
  - PLT08-E 留出集：`nexus_backend/tests/evals/plt08_holdout/dataset.json` 64 条封存样本（归因正确/missing_evidence_refusal/cross_period_refusal/cross_entity_leak 各 16 条），文件头 `_notice` 注明全部为合成数据、`synthetic=true`、与开发样本分目录；`tools/assistant_replay/plt08.py` 为只读可重复评测器，A/B/C 三组各自独立生成候选、用 `sha256(group:sample)` 盲标签统一计分，输出 5 项独立指标、不设达标线、不合成总分、拒绝覆盖已存在目录。
  - 本机真实指标（`tmp_test/pilot/PLT08-E/dev-run-6/report.json`）：三组 事实正确率 1.0 / 引用准确率 1.0 / 漏报阻断率 0.0 / 阻断项完整列出率 1.0 / 应弃答而未弃答率 0.0 / 单次有效诊断回放成本 2.2188；B 组规则依据引用 15 条（A/C 为 0），C 组叙述被改写 12 条、残留归因断定 0 条；模型与网络调用恒 0。B 与 A 的五项指标相同，是因为企业规则按设计只增加「依据」引用、不影响金额与判断，不人为造差异。
  - 门禁真实输出：`pytest test_plt08_full_suite.py test_plt08_bounded_actions.py test_stage15_assistant_actions.py test_migration_structure.py test_pg_sql_hygiene.py test_write_scope_baseline.py test_stage25_assistant_efficiency.py test_stage19_request_reads.py test_workflow_engine_versioning.py -q` → **120 passed / 157.28s**；`check_migration_idempotency.py` exit 0（326/327 与 324/325 同列「需专项检查：dynamic_ddl」）；`check_secrets.py` → `no secrets found`；`ruff check` All checks passed；`ruff format --check nexus_backend/app services/api_gateway scripts` → 478 files already formatted。
  - 未验证/边界：无 PostgreSQL 环境，326/327 未在 PG16/17 应用，RLS 与卡片触发器只做了静态卫生检查；留出样本与实现由同一作者编写，高分只说明二者自洽、不构成泛化证据；真实模型仍待接入评测（本轮所有 AI 环节为确定性桩与回放）；前端未新增 PLT08 面板，本次交付为服务端契约与评测闭环。

## PLT07 收口（Batch 10-1.1，2026-11-23）

- 状态：**完成（服务端纯函数与场景测试）**。
- 三段式队列：`app/workspace/queue_lanes.py` 按 `Asia/Shanghai` 划分「今天必须办 / 即将逾期 / 等待别人（含无截止日 / 计划内）」五段，互不重复、排序可测，不使用模型打分（`plt07-lanes-v1`）。
- 四类积压归因 + 诚实 ETA：按当前真实处理方归入业务部门 / 共享中心 / 外部系统 / 未分配；同类样本 < 5 时 `eta_minutes=None` 且显示「样本不足」。
- 受控转派：复用 `app/handoff/service.py::HandoffService.candidates/assign`——候选人只含已有账套权限者，`assignee == 制单人` 直接 409，无权限者 403「指派不会授予权限」，转派写 `handoff.assign` 审计并 supersede 旧行。
- 场景证据：`nexus_backend/tests/test_plt07_peak_season.py` 6 例（两家同日结账互不重复、复核人请假且代班授权按时间生效、代班人在 `management_review_separation` 下不可自审、银行流水迟到只阻断本家、积压归因与 ETA 诚实、集团汇总缺报点名 + 固定标注 + 非管理拒访）。
- 未验证：真实客户在月末高峰的实际队列数据、真实代班审批流程；本阶段只用合成企业。

## PLT09 反作弊判定（Batch 10-1.3，2026-11-23）

- 状态：**工具就绪，未采集真实数据**。
- `scripts/plt09_measurement.py` 新增 `Aggregate`、`coverage()`、`unknown_as_zero()`、`evaluate()`：把 `docs/pilot/SCORECARD.md` 七条反作弊规则逐条落地——复核积压上升、事后差错上升、转移给业务人员时间上升（按每对象时间比较，避免业务量放大误判）、职责范围口径不一致、未记录外部援助、覆盖不足或未采集当零、分母变更未披露。
- 任一条成立：`verdict="不能判定为成功"` 并逐条给出原因；`reduction_reportable` 恒为 `False`，函数从不计算减员比例、节省工时或节省金额。
- 证据：`nexus_backend/tests/test_plt09_measurement.py` 13 例（3 例原空白台账 + 10 例反作弊），含「干净期间也只能提交人工复核」「不输出节省/减员结论」。
- 未验证：真实双月数据的判定结果；七条规则的阈值（覆盖下限 0.8）需客户确认。
