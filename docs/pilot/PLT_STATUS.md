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
- 因此 PLT01 不能写“全部完成”；PLT02 已按顺序开始，见下节。

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
