# PLT 执行状态

追加不覆盖。每个模块只在其验收命令真实通过后写“完成”；无 PG/浏览器/客户资料时如实写“未验证”。

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

## PLT03-C 直达与返回  <状态：部分完成（14类目标键/范围/版本/alert/失效页/review/approval/finance/handoff已办完信息/五身份待办链/业务与复核多层返回/队列滚动通过；已完成页查看入口、更深多层返回未验）>

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
  - 新增 `src/TaskUnavailableDialog.tsx` + `.task-unavailable-*` 样式：版本变化“待办内容已更新”可“打开最新内容”，403“权限已撤销”显示当前处理人，404 统一“待办不可见”且不泄露存在性，410“待办已办完”显示服务端给出的办理人/时间；`WorkspaceHome` 与 `FirmInbox` 均接入，原队列不清空。
  - 新增 `src/task-availability.ts`/`src/task-scope.ts`/`src/TaskUnavailableDialog.tsx`、`e2e/plt03-task-matrix.spec.ts`、`e2e/plt03-task-navigation.spec.ts`、`e2e/plt03-reviewer-chain.spec.ts`，并修改 `src/App.tsx`、`src/WorkspaceHome.tsx`、`src/FirmInbox.tsx`、`src/OperationsWorkbench.tsx`、`src/workspace-route.ts`、`src/workspace-types.ts`、`src/WorkspaceRouteContent.tsx`、`src/styles.css`、`e2e/business.spec.ts`、`e2e/stage8-cases.spec.ts`。
  - `nexus_backend/app/workspace/service.py::_review_completion`：仅在已办完 review 且当前账号仍有该账套复核范围时查询 `drafts.approved_by/approved_at/users.name`，返回 410“该待办已由 … 于 … 办理”；无权限/不存在仍是 404，不泄露存在性。SQLite 与 PG16/17 均已覆盖。
  - `nexus_backend/app/workspace/service.py::_business_completion`：对 approval/finance/handoff 的已完成阶段，按 `business_actions` 最新 approve/handoff 记录返回 410 办理人/时间；先按当前 role gates + 公司成员/事务所经理范围授权，再查动作，未知/无权仍 404。approval/finance/handoff 三类均已用各自完成事件断言 410；无审批权限的 accountant 仍 404。
  - `test_stage12_workspace.py::test_workspace_person_department_company_independent_approval`：原断言 404 改为 410，并断言无审批权限的 accountant 仍 404；该用例 **1 passed/9.31s**（红测先得 `assert 410 == 404`）；SQLite 整套 **19 passed/40.61s**。
  - `test_workspace_finance_and_handoff_completed_return_actor_and_time`：先 `approved(c)` 再断言 finance 410，再 `handoff(c,event)` 断言 handoff 410，均含办理人/时间；SQLite 整套 **19 passed/40.61s**。
  - PG 同一文件在 `tmp_test/plt02b-pg17|16` 新库各 **10 passed/23.07s、22.70s**，覆盖 410 查询的 JSONB/RLS/授权路径；approval/finance/handoff 三类完成态均已用同一实现独立验证。
- 失败测试及修改前输出：本项未保留“打开落到错误页”的 Playwright 红测；首次新用例失败仅因断言把 `object_type` 写成 `draft`，服务端实际为任务类型 `review`，修正断言后通过。此前 `src/task-scope.test.ts` 3 passed。
- 验收命令与输出：
  ```text
  npx vitest run src/task-scope.test.ts src/task-availability.test.ts
  6 passed
  ```
  ```text
  npx playwright test e2e/plt03-task-navigation.spec.ts --reporter=line
  2 passed (22.5s)（跨企业深链/返回筛选/旧版本对话框/403/410，手机端滚动恢复）
  截图：`tmp_test/plt03c-stale-dialog.png`、`tmp_test/plt03c-forbidden-dialog.png`、`tmp_test/plt03c-completed-dialog.png`
  ```
  ```text
  npx playwright test e2e/plt03-task-matrix.spec.ts --reporter=line
  1 passed (27.5s)（14 类待办逐类点击，均落到目标键，URL 均带 company/period/task_version；alert 带告警 ID）
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
  ✓ built in 3.75s；index JS 568.45 kB / gzip 176.26 kB，index CSS 101.46 kB / gzip 20.63 kB
  ```
- 未完成：
  - 14 类已逐类验证落点键与 company/period；`alert` 已带告警 ID 并在运行页定位；`export` 的投影 ref 是账套 id，任务本身就是“该账套的已审核凭证队列”，`book+filter=approved` 即精确目标，没有单独导出请求对象可打开。
  - 对象版本已写入 URL 并在打开前与列表比对；仍未提供“查看最新版本”对比页，只提示刷新后重开。
  - 403/404/版本变化已有独立对话框；`review` 的已办完任务在 410 中展示 `drafts.approved_by/approved_at` 的办理人和时间；其他类型仍以 404 统一“不存在/不可见”，未单独展示办理信息。
  - 队列筛选与手机端滚动位置均能恢复；恢复发生在列表重载完成之后（约 200ms），期间存在一次可见回跳。多层返回已覆盖“待办→复核抽屉→原件页签→分录页签→队列”“待办→业务事项→部门办理→返回待办”，均回到原队列并恢复滚动；月度导入-导出路径未逐层验证。
  - 1366/390 已跑员工、部门、财务、会计、复核人从真实待办进入并完成办理的链；14 类矩阵仍只验证落点键，不验证其余类型办理成功。
  - 员工与会计两条真实链已从待办卡片进入：`e2e/stage8-cases.spec.ts` 员工在 `/workspace/home` 打开“补充业务资料”、上传并提交；会计从同一列表打开待办、接受补件；办理后该 `data-task-key` 从列表消失。**1 passed/20.0s**。部门、财务两条链仍缺。
- 结论：范围切换、14 类目标键与 company/period/task_version、alert 告警定位、export 账套已审核队列、跨企业深链、返回筛选/滚动、旧版本“打开最新内容”、403/404 独立提示页和 review 410 已办完办理人/时间已就绪；员工、部门、财务、会计、复核人均已在 1366 与 390 从真实待办卡片进入并办理，复核链（原件页签→分录页签→确认→返回）、业务链（事项→部门办理→返回）均恢复原滚动位置。PLT03-C 仍不能标完成：review/approval/finance/handoff 的“他人已办完”已显示办理人/时间并保留 404 防探测，但月度导入-导出这类更深多层返回未逐层验证；目前 C 的剩余证据项是：已完成页没有直接查看原对象的入口，以及“待办→对象→原件→返回→返回”的完整月度路径未逐层验证。
- 当前候选（只读指纹）：HEAD `39067aacc1b640419ee80a794297b233fd13e546`，source **2444 / `da65176bd159969bb27ad2305db69a7685a67dbf029fde422fe1360674134725`**，migration **487 / `4964fda2103ea40dcd3f0cdb33239e4e07a66acfad1e98d4cc75548a4392b3bb`**；工作区未提交。

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
