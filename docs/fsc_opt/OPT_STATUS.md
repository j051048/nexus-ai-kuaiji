# OPT 执行状态

本文件按 `docs/fsc_opt/OPT00_MASTER.md` 第 4 节格式追加；只记录实际执行过的命令与输出。

## OPT01-A  <状态：完成>

- 现状核实：`grep -n -E "_cents +INTEGER" nexus_backend/migrations/postgres/*.sql` → 仍有 14 个原始 `INTEGER` 定义；按迁移顺序核对，`173_n02_tax_snapshot_values.sql` 已扩 169 的 9 列、`237_taxi_fee_authority.sql` 已扩 225 的 `amount_total_cents`，最终仍剩 `fee_receivable_allocations.amount_cents`、`fee_receivable_allocations.bank_amount_cents`、`fee_receivable_refunds.amount_cents`、`fee_receivable_refunds.bank_amount_cents` 四个 32 位列。
- 失败测试：`nexus_backend/tests/test_migration_money_types.py::test_every_cents_column_is_finally_bigint`，修改前输出：`1 failed in 0.46s`，`AssertionError: PostgreSQL *_cents columns still 32-bit: {'fee_receivable_allocations.amount_cents': 'INTEGER', 'fee_receivable_allocations.bank_amount_cents': 'INTEGER', 'fee_receivable_refunds.amount_cents': 'INTEGER', 'fee_receivable_refunds.bank_amount_cents': 'INTEGER'}`。
- 修改文件：
  - 新增 `nexus_backend/migrations/postgres/260_fee_receivable_money_bigint.sql`：将上述 4 列 `ALTER COLUMN ... TYPE BIGINT`，未改历史迁移。
  - 新增 `nexus_backend/migrations/sqlite/175_fee_receivable_money_bigint.sql`：SQLite `INTEGER` 已为 64 位，保留迁移编号对齐的空操作。
  - 新增 `nexus_backend/tests/test_migration_money_types.py`：按迁移顺序计算 `*_cents` 最终类型，防止后续新表再次落成 32 位。
  - `nexus_backend/tests/test_tax06_fees_postgres.py`：新增 `test_pg_money_columns_accept_three_billion_cents`，在真实 PG 中断言 4 列 `information_schema` 类型为 `bigint`，并实际写入 3,000,000,000 分到两张表。
- 验收命令与输出：
  ```
  cd nexus_backend && PYTHONPATH=. pytest -q tests/test_migration_money_types.py tests/test_tax06_fees.py
  ......................                                                   [100%]
  22 passed in 45.00s
  ```
  ```
  python scripts/test_accounting_postgres_isolated.py --config tmp_test/fsc02-pg/env17/unified.json nexus_backend/tests/test_tax06_fees_postgres.py -q
  AK05 PostgreSQL artifacts: .../tmp_test/ak05-pg-runs/141600df62c444778de892cd1c593e4c
  .........                                                                [100%]
  9 passed in 11.46s
  ```
  ```
  python scripts/test_accounting_postgres_isolated.py --config tmp_test/fsc02-pg/env16/unified.json nexus_backend/tests/test_tax06_fees_postgres.py -q
  AK05 PostgreSQL artifacts: .../tmp_test/ak05-pg-runs/c841bfc0020541d295daeb3fcc98ddec
  .........                                                                [100%]
  9 passed in 11.77s
  ```
  迁移在首次运行（该库迁移账本尚无 260）时应用，随后在已迁移库上重复运行同一 PG 用例（artifact `dc812078...`）仍为 `1 passed`；未改写历史迁移。
- 未做或未验证：无（本模块范围内）。
- 顺带发现：工作区仍有 FSC 未提交改动；这些不属于 OPT01-A，未计入本模块结论，也未提交。

## OPT01-B  <状态：已被修复无需改动>

- 现状核实：`git diff --stat -- nexus_backend/migrations/sqlite/162_fsc_company_profiles.sql` → 无输出；当前文件与 `git show HEAD:` 内容一致。逐项核对三处约束均存在：`created_by TEXT NOT NULL REFERENCES users(id)`、`confirmed_by TEXT REFERENCES users(id)`、`FOREIGN KEY(company_id,firm_id) REFERENCES firm_clients(firm_id,company_id)`。因此没有可还原的删除，也不再改该已提交迁移。
- 失败测试：本模块要求“还原被删外键”，实际未被删；按 OPT00 第 1 节“已被别人修好就记录证据并跳过，不重做”，不再制造失败测试。
- 修改文件：无。
- 验收命令与输出：
  ```
  cd nexus_backend && PYTHONPATH=. pytest -q tests/test_fsc01_profiles.py
  .                                                                        [100%]
  1 passed, 2413 warnings in 7.21s
  ```
- 未做或未验证：未改 162；未新增迁移。
- 顺带发现：
  - PostgreSQL `246_fsc_company_profiles.sql` 已有 `created_by`/`confirmed_by` 用户外键，且 `FOREIGN KEY(firm_id,company_id)` 列序正确。
  - SQLite 162 的三处约束虽在，但复合外键写作 `(company_id,firm_id)`，实际按 SQLite 列配对是 `company_id→firm_id`；既有未提交前向迁移 `173_fsc_profile_firm_client_fk.sql` 重建表修正为 `(firm_id,company_id)`，未改 162。该问题已在 FSC01 工作中修复并有回归，故仅记录。
  - 当前工作区仍有大量 FSC/OPT01-A 未提交改动，不属本模块。

## OPT01-C  <状态：部分完成>

- 现状核实：`rg -n "def _ratio|// 10000|quantize" nexus_backend/app/accounting/invariants.py nexus_backend/app/tax/{workpaper,basis,estimate}.py` → `workpaper._ratio` 已四舍五入，`basis.generate` 两处 `// 10000` 向下截断，`estimate_vat` 五处 `quantize(Decimal("0.01"))` 未指定舍入；其余 `quantize` 仅 `review_policy.py` 的比率，不属金额。
- 失败测试：`nexus_backend/tests/test_tax_rounding_consistency.py::test_three_tax_paths_round_half_up`，修改前输出：
  ```
  assert row["tax_cents"] == 5
  E       assert 4 == 5
  1 failed in 4.86s
  ```
  同一用例在修复 basis 后暴露出 `estimate_vat` 的 0.045→0.04，改为显式 `ROUND_HALF_UP` 后得到 0.05。
- 修改文件：
  - `nexus_backend/app/accounting/invariants.py`：新增 `scale_by_basis_points(cents, rate_bp)`，整数分×基点、四舍五入，负数按绝对值舍入后还原符号，拒绝非整数与负费率。
  - `nexus_backend/app/tax/workpaper.py`：`_ratio` 改为调用该函数，保留原私有入口。
  - `nexus_backend/app/tax/basis.py`：税额与附加税两处截断改为该函数。
  - `nexus_backend/app/tax/estimate.py`：五处金额 `quantize` 全部显式 `rounding=ROUND_HALF_UP`。
  - 新增 `nexus_backend/tests/test_tax_rounding_consistency.py`：覆盖 150/250 分×300bp、负数、basis 服务、estimate 服务。
- 验收命令与输出：
  ```
  cd nexus_backend && PYTHONPATH='..;.' pytest -q \
    tests/test_n04_tax_contract.py tests/test_tax14_basis.py \
    tests/test_tax_estimate.py tests/test_tax_rounding_consistency.py
  24 passed, 1619 warnings in 30.15s
  ```
  ```
  cd nexus_backend && PYTHONPATH='..;.' pytest -q \
    tests/test_tax_estimate.py tests/test_tax14_basis.py tests/test_tax04_sources.py
  28 passed in 27.27s
  ```
  ```
  cd nexus_backend && PYTHONPATH='..;.' pytest -q -k tax
  10 failed, 220 passed, 84 skipped, 5948 deselected in 334.22s
  ```
  全量 `-k tax` 的 10 个失败均不在本次取整路径，未改其期望值或跳过：
  1. `test_tax01_pilot.py::test_http_pilot_boundary_contract`、`test_tax03_transport.py::test_http_transport_overview_and_subject_creation`、`test_tax_api.py` 两个用例：本机 Starlette 0.35 `TestClient` 不支持 `client=` 参数。
  2. `test_tax_f_recovery.py::test_lease_basis_and_mapping_survive_full_restore`：`approved_version` 期望 0、实际 1，租赁/恢复夹具与当前实现不一致。
  3. `test_taxi_ad_acceptance_repair.py`、`test_taxi_ad_backend_continuity.py`、`test_taxi_ad_recovery.py`：桥梁辅助映射/运输关系/辅助值夹具问题。
  4. `test_taxi_source_input_limits.py` 两个用例：源行上限/布尔版本夹具未复现拒绝。
  上述失败需另立模块处理；本模块不通过删除断言或跳过使其变绿。
- 未做或未验证：未修改上述 10 个失败；未跑 PG 侧税额用例；未批量改历史测试期望值。
- 顺带发现：
  - `app/accounting/review_policy.py` 的 `quantize(Decimal("0.0001"))` 是人员占比，不是金额，未改。
  - `workpaper._snapshot` 的附加税以已四舍五入的税额为基数，保持原有“税基→税额→附加税”顺序，仅统一舍入方式。

### 2026-11-17 对齐 CI 依赖复核（仍部分完成）

- 现状核实：使用 `.venv`（FastAPI 0.133.1 / Starlette 1.8.0 / httpx 0.28.1）重跑，排除默认 Python 的 Starlette 0.35 `TestClient(client=...)` 环境假失败。
- 验收命令与输出：
  ```
  cd nexus_backend && PYTHONPATH=. ../.venv/Scripts/python.exe -m pytest -q     tests/test_n04_tax_contract.py tests/test_tax14_basis.py     tests/test_tax_estimate.py tests/test_tax_rounding_consistency.py
  24 passed, 1 warning in 29.08s
  ```
  ```
  cd nexus_backend && PYTHONPATH='..;.' ../.venv/Scripts/python.exe -m pytest -q -k tax
  6 failed, 231 passed, 87 skipped, 6022 deselected, 2 warnings in 318.07s
  ```
  失败：
  1. `test_tax_f_recovery.py::test_lease_basis_and_mapping_survive_full_restore`
  2. `test_taxi_ad_acceptance_repair.py::test_frozen_adapter_cannot_present_stale_fee_as_current`
  3. `test_taxi_ad_backend_continuity.py::test_fee_draft_carries_real_driver_and_vehicle_auxiliaries`
  4. `test_taxi_ad_recovery.py::test_populated_taxi_source_review_export_unknown_and_hold_survive_restore`
  5. `test_taxi_source_input_limits.py::test_malformed_rows_cannot_bypass_source_row_limit`
  6. `test_taxi_source_input_limits.py::test_boolean_cannot_be_used_as_source_version`
- 结论：4 个 Starlette 环境假失败已消除；剩余 6 个均不在取整路径，需另立模块处理。OPT01-C 仍为部分完成，不删除断言/不跳过。

## OPT02-A  <状态：完成>

- 现状核实（`gh` 未使用；直接读四个工作流并在本地按步骤复现）：

  | 工作流 | 作业 | 失败/风险步骤 | 本地复现 |
  | --- | --- | --- | --- |
  | Accounting foundation | verify | `npm run format:check`（57 个文件） | 是，已修 |
  | Accounting foundation | verify | `npm test`（17 个旧断言失败） | 是，已修 |
  | Accounting foundation | verify | `python -m pytest nexus_backend/tests -q`（本地默认 Starlette 0.35 与 CI lock 不一致） | 是，任务 venv 对齐后仅剩 1 个既有权限用例失败 |
  | Accounting foundation | verify | `npm run build`（Windows 默认 TMP 的 esbuild ACL） | 是，任务 TMP 通过 |
  | Accounting foundation | e2e | 只安装 Chromium；未在本机跑完整 e2e | 未完整复现 |
  | QA fault security / browsers | browsers | 三引擎 Chromium/Firefox/WebKit；本机未跑 | 未完整复现 |
  | P01 joint recovery | joint-recovery | PG16/17 + Playwright；本机未跑本工作流 | 未完整复现 |

- 失败原因：前端格式与旧测试断言、后端依赖版本不一致、Windows 临时目录 ACL；均非业务逻辑回归。
- 修改文件：仅修 OPT02-A 列出的测试断言与 `journal-grid.css` 字号，未改业务逻辑。
- 验收命令与输出：见 OPT02-B/C。
- 未做或未验证：e2e、P01 工作流未完整本地重放；本机默认 Python 环境不满足 CI lock，使用任务 venv。
- 顺带发现：仓库根即 Vite 应用，没有 `apps/web`；`ci.yml` 不存在，实际为 `accounting.yml`、`qa-reliability.yml`、`p01-recovery.yml`、`local-quick.yml`。

## OPT02-B  <状态：完成>

- 现状核实：`npm run format:check` 原报 57 个文件；`npm test` 原为 17 failed / 1386 passed。
- 失败测试：旧断言依赖 `.strong` 岗位名、`岗位说明与权限`、nav 不含 `insights`、employee section 数 4、monthly 客户名全局计数、journal-grid 11px。
- 修改文件：上述测试改为当前 UI 契约；`src/journal-grid.css` 字号 11px→12px；仅格式化清单内文件。
- 验收命令与输出：
  ```
  npm run format:check
  All matched files use Prettier code style!
  npm run typecheck
  tsc -p tsconfig.app.json --noEmit  (exit 0)
  npm run lint
  ✖ 4 problems (0 errors, 4 warnings)
  npm test
  Test Files  152 passed (152)
       Tests  1403 passed (1403)
  TMP/TEMP=tmp_test/tmp-build npm run build
  ✓ built in 4.24s
  index 566.19 kB │ gzip: 175.42 kB
  ```
- 未做或未验证：4 个 eslint 警告为未使用 eslint-disable 指令，未用 disable 注释掩盖。
- 顺带发现：默认 TMP 下 esbuild `Access is denied` 是 Windows ACL 环境问题，任务内 TMP 可复现通过。

## OPT02-C  <状态：部分完成>

- 现状核实：CI lock `ruff==0.17.0`，本机默认 `ruff 0.15.12`；0.15.12 的 `format --check` 报 53 个文件，其中含 CRLF 假阳性。用 0.17.0 在 LF 归一化后确认仍有 53 个真实格式问题，至少 `tax/basis.py`、`tax/costs.py`、`tax/leases.py`、`tax/pilot.py`。
- 失败测试：`ruff check --select F401,F811 nexus_backend/app` 原报 40 个（39 F401、1 F811）。
- 修改文件：
  - `ruff.toml`：`select` 追加 `F401`、`F811`；`nexus_backend/tests/**` 用 per-file-ignores 排除并注释“测试代码允许 pytest fixture/兼容导入”。
  - 用 ruff 0.17.0 `format nexus_backend/app` 格式化 53 个文件。
  - `ruff check --fix nexus_backend/app` 删除 39 个未使用导入/重复定义。
- 验收命令与输出：
  ```
  tmp_test/opt02-ruff-venv/Scripts/ruff.exe check nexus_backend/app
  All checks passed!
  tmp_test/opt02-ruff-venv/Scripts/ruff.exe format --check nexus_backend/app
  352 files already formatted
  ```
  ```
  cd nexus_backend && PYTHONPATH=. python -m pytest -q \
    tests/test_n04_tax_contract.py tests/test_tax14_basis.py tests/test_tax_estimate.py \
    tests/test_tax_rounding_consistency.py tests/test_fsc05_evidence.py \
    tests/test_fsc04_imports.py tests/test_tax06_fees.py
  51 passed, 1619 warnings in 81.88s
  ```
- 未做或未验证：完整 backend 套件未全绿。默认 Python 环境下 `TestClient(..., client=...)` 因 Starlette 0.35 报 4 个 TypeError；改用任务 venv 对齐 lock（FastAPI 0.133.1 / Starlette 1.8.0 / httpx 0.28.1）后，首个 fail-fast 运行 `1 failed, 64 passed in 58.19s`，失败为既有 `test_admin_derived_permissions.py::test_explicit_company_finance_manager_can_create_period_without_granting_existing_books`（`grant_book_member(role="reviewer")` 被 firm reviewer 规则拒绝），不属于本模块格式/静态范围；未修改该业务规则或删断言。随后启动完整 no-fail-fast 运行，约 70 分钟后仅到 12%，因本机测试总耗时不可接受而中止，未取得全量结果；不把部分运行写成全绿。
- 顺带发现：pip 通过当前代理安装 `ruff==0.17.0` 长时间无输出，改用 PyPI 官方 wheel 本地 `--no-index` 安装成功；该 venv 只用于验证，不提交。

## OPT02-D  <状态：完成>

- 现状核实：`ast` 扫描 `nexus_backend/app` 中 `transaction(write=True)` 且无 `write_scope` 的调用，按 `文件::函数` 归类，存量 225 个。
- 失败测试：新增 `nexus_backend/tests/test_write_scope_baseline.py`，含扫描器自测与基线子集断言；临时新增 `app/_opt02_probe.py::probe` 后输出：
  ```
  E       AssertionError: New write transaction must declare write_scope. New entries: app/_opt02_probe.py::probe
  E       assert not ['app/_opt02_probe.py::probe']
  1 failed, 1 passed in 1.62s
  ```
  探针已删除，复跑 `2 passed in 1.59s`。
- 修改文件：
  - 新增 `nexus_backend/tests/test_write_scope_baseline.py`。
  - 新增 `nexus_backend/tests/write_scope_baseline.txt`（头部写明用途/只减不增/更新规则；225 条 `app/...::函数`）。
- 验收命令与输出：
  ```
  cd nexus_backend && PYTHONPATH=.. python -m pytest -q tests/test_write_scope_baseline.py
  2 passed in 1.59s
  ```
- 未做或未验证：存量调用未加锁（按模块约定先不动）；基线只防新增。
- 顺带发现：无。


## OPT02-E  <状态：部分完成（本地可执行部分完成；CI/runner 待人工）>

- 现状核实：
  - `npx playwright install --dry-run` 显示所需 Chromium 1208、Firefox 1509、WebKit 2248、FFmpeg、Winldd 均已有本地安装目录。
  - 使用 `playwright.qa.config.ts` 的三引擎 project 直接跑 `e2e/qa-accessibility.spec.ts`。
  - PG 隔离准备复用本任务 owned env16/env17（`tmp_test/fsc02-pg/env16|17/unified.json`），不触碰其他任务集群。
- 失败测试：首次三引擎运行 `3 failed, 6 passed (5.1m)`，三引擎均只在 employee 的 `.oa-material-empty`（“当前没有待补材料请求。”）报 axe color-contrast：前景 `#78847f`、背景 `#ffffff`、对比度 **3.88**，要求 4.5。
- 修改文件：`src/oa-material-requests.css` 将空态与提示文字从 `#78847f` 改为 `#5f6e68`（12px normal 文本，暗色提高对比度）；未改布局或业务。
- 验收命令与输出：
  ```
  npx playwright test e2e/qa-accessibility.spec.ts --config=playwright.qa.config.ts --project=chromium --grep employee
  1 passed (12.4s)
  ```
  ```
  npx playwright test e2e/qa-accessibility.spec.ts --config=playwright.qa.config.ts \
    --project=chromium --project=firefox --project=webkit
  9 passed (29.8s)
  ```
  ```
  python scripts/test_accounting_postgres_isolated.py --config tmp_test/fsc02-pg/env16/unified.json \
    nexus_backend/tests/test_fsc05_evidence_postgres.py -q
  1 passed in 0.99s   # artifact 19b2b1036f6f481abdaff3454fde30bf
  python scripts/test_accounting_postgres_isolated.py --config tmp_test/fsc02-pg/env17/unified.json \
    nexus_backend/tests/test_fsc05_evidence_postgres.py -q
  1 passed in 0.90s   # artifact 87c5026f382144e1aca48a938dae1b26
  ```
  ```
  npm run format:check  # All matched files use Prettier code style!
  npm test              # 152 files / 1403 tests passed
  ```
- 未做或未验证（需人工/CI）：
  - 未推送，无法验证四个 GitHub 工作流在同一提交上全绿。
  - 未在 GitHub 启用 main 分支保护/必需检查，未改分支+MR 流程。
  - `npx playwright install --with-deps` 是 Linux CI 步骤；Windows 本机用已有浏览器目录验证，未复现 apt 依赖安装。
  - P01 joint recovery 的 CI runner/隔离 PG 全链未在本地完整重放。
- 顺带发现：首次浏览器失败是真实可访问性缺陷，不是测试误报；修复后三引擎通过。

### OPT02 候选更新（2026-11-17）

- HEAD：`767f7cdf7c717c98d3516a0e31b53bd8cd9ae1a9`
- source：`2272 / 7d18e9f3910705ba8f24d7f26c59e98782d53458a8c7ca8656ae25ac3fbebb71`
- migration：`421 / 424c03e3cc4550ae1545edeaf884cdbd4636f8008a7dd3517d58812fbe6713d8`
- 说明：该指纹在 Prettier/ruff 格式化、F401/F811 清理、可访问性 CSS 修复之后重算；工作区仍未提交。

### OPT02-C 对齐 CI 依赖复核（2026-11-17，仍部分完成）

- 现状核实：使用既有 `.venv`（FastAPI 0.133.1 / Starlette 1.8.0 / httpx 0.28.1）执行完整 backend fail-fast 验收；该环境已消除默认 Python 的 Starlette 0.35 `TestClient(client=...)` 环境假失败。
- 验收命令与输出：
  ```
  cd nexus_backend && PYTHONPATH='..;.' ../.venv/Scripts/python.exe -m pytest -q -x --ignore-glob='*_postgres.py'
  1 failed, 64 passed, 2 warnings in 49.66s
  ```
  失败：
  `test_admin_derived_permissions.py::test_explicit_company_finance_manager_can_create_period_without_granting_existing_books[sqlite]`
  在 `DirectoryService.grant_book_member` 的 `request.role == "reviewer"` 分支抛出 403：正式复核只能授予事务所复核人员。
- 结论：仍为 **部分完成**。格式/静态检查已完成；完整 backend 套件被既有“公司财务授予客户复核人”旧权限契约测试阻断，属于独立权限测试迁移，不在 OPT02-C 内删改断言或放宽产品规则。
- 未做或未验证：未改动 `test_admin_derived_permissions.py`，未放宽 reviewer 授予规则，未取得完整 backend 全绿。

### OPT02-C 未完成说明（2026-11-17）

- 尝试把 `test_admin_derived_permissions.py` 的客户财务 reviewer 夹具改为 editor，并改写“公司财务建期”用例；运行后 5 个既有用例继续失败，证明该文件是按旧权限契约编写的，不能用局部夹具替换修复。
- 已把该测试文件完整还原到 HEAD，未保留实验性改动；产品规则未改。
- 结论：OPT02-C 的格式/静态/导入检查完成，但 backend 全量套件仍受既有权限契约测试和运行时长阻塞；C 保持“部分完成”，需另立权限测试迁移模块处理，不在 OPT02 内删改旧断言。

## OPT03-A  <状态：完成>

- 现状核实：
  - `audit_events`：SQLite 在 `migrations/sqlite/001_initial.sql`；PG 在 `migrations/postgres/001_accounting.sql`，业务角色原本仅 `SELECT,INSERT`，但没有表属主改删保护。
  - 全仓 `UPDATE/DELETE/TRUNCATE audit_events` 只有历史 SQLite `064_admin_workspace.sql` 的回填（早于本迁移）和测试中的“篡改应被拒绝”断言；应用代码无改删路径。
- 失败测试：
  - 新增 `nexus_backend/tests/test_opt03_audit_immutability.py`。
  - SQLite 修改前：`2 failed`，两条均为 `DID NOT RAISE sqlite3.IntegrityError`（UPDATE/DELETE 成功）。
  - PG 用例首次因 `postgres` fixture 未导入报 setup error；修 fixture 依赖后实际验证表属主改删被新触发器拒绝。
- 修改文件：
  - 新增 `nexus_backend/migrations/postgres/261_audit_events_append_only.sql`：`audit_events_append_only_guard()`（SECURITY DEFINER + 固定 search_path）、`BEFORE UPDATE OR DELETE` 行触发器、`BEFORE TRUNCATE` 语句触发器，`23514`。
  - 新增 `nexus_backend/migrations/sqlite/176_audit_events_append_only.sql`：`BEFORE UPDATE`/`BEFORE DELETE` 触发器，`RAISE(ABORT,'audit_events are append-only')`。
  - 新增测试覆盖：SQLite UPDATE/DELETE；PG 表属主 UPDATE/DELETE/TRUNCATE；原行业务角色权限拒绝保持不变。
- 验收命令与输出：
  ```
  cd nexus_backend && PYTHONPATH=. pytest -q tests/test_opt03_audit_immutability.py
  2 passed, 3 skipped (PG DSN 未设置)
  ```
  ```
  python scripts/test_accounting_postgres_isolated.py --config tmp_test/fsc02-pg/env16/unified.json \
    nexus_backend/tests/test_opt03_audit_immutability.py -q
  5 passed in 5.95s   # artifact 9739af7ba2934d33ac309c9f89f356ef
  python scripts/test_accounting_postgres_isolated.py --config tmp_test/fsc02-pg/env17/unified.json \
    nexus_backend/tests/test_opt03_audit_immutability.py -q
  5 passed in 5.77s   # artifact 4edf32957af449478653b212f0d31d50
  ```
  `git diff --check` 通过。
- 未做或未验证：OPT03-B/C 尚未做。默认 Python 环境 `TestClient(client=...)` 的 3 个既有错误仍是环境版本不一致，不作为本模块失败。
- 顺带发现：`test_accounting.py` 3 个 HTTP 用例在默认 Starlette 0.35 下 setup error；任务 venv（Starlette 1.8）下才可运行。

## OPT03-B  <状态：完成>

- 现状核实：`audit_events` 无 `prev_hash/row_hash`；应用层无统一哈希链，改删只能靠 A 的触发器。要求按事务所分链、并发不分叉、可独立重算。
- 失败测试：
  - 新增 `nexus_backend/tests/test_opt03_audit_chain.py`：3 条同事务所记录逐条重算；篡改中间行报 `row-hash-mismatch`；删除中间行报 `prev-hash-mismatch`；已有历史行回填后仍可校验。
  - 首次收集失败：`ModuleNotFoundError: No module named 'scripts'`，原因仅是测试未把仓库根加入 `sys.path`，不是产品缺陷；修正后运行。
  - SQLite 修改前没有链列/触发器，校验函数无 `prev_hash` 可读；实现后通过。
- 修改文件：
  - 新增 `nexus_backend/migrations/postgres/262_audit_hash_chain.sql`：`audit_events` 增 `prev_hash/row_hash`；`accounting.audit_event_hash(...)` 规范化哈希；按事务所顺序回填；`audit_events_chain_guard()` 在 `BEFORE INSERT` 用 `pg_advisory_xact_lock(hashtext('accounting.audit_events:'||firm_id))` 锁链尾，取前一行 hash 再计算。
  - 新增 `nexus_backend/migrations/sqlite/177_audit_hash_chain.sql`：增列；DROP 176 的 UPDATE 守卫后递归 CTE 回填；INSERT 后计算；重建 UPDATE 守卫只允许一次性数据库回填，其他 UPDATE/DELETE 仍拒绝。
  - 新增 `scripts/verify_audit_chain.py`：只读校验 SQLite/PG；只输出首个断点主键、事务所前 8 位、原因、前后摘要前 8 位；退出码 0/1/2。
  - 新增 `nexus_backend/tests/test_opt03_audit_chain_postgres.py`：正常链、篡改内容、删除中间行、双线程并发写入后仍可重算。
- 验收命令与输出：
  ```
  cd nexus_backend && PYTHONPATH=..:. pytest -q tests/test_opt03_audit_chain.py
  3 passed
  ```
  ```
  python scripts/test_accounting_postgres_isolated.py --config tmp_test/fsc02-pg/env17/unified.json nexus_backend/tests/test_opt03_audit_chain_postgres.py -q
  AK05 PostgreSQL artifacts: .../tmp_test/ak05-pg-runs/b1cfd544060f491183405f3c3ea01655
  3 passed in 1.54s
  ```
  ```
  python scripts/test_accounting_postgres_isolated.py --config tmp_test/fsc02-pg/env16/unified.json nexus_backend/tests/test_opt03_audit_chain_postgres.py -q
  AK05 PostgreSQL artifacts: .../tmp_test/ak05-pg-runs/e250c5888dde4873a65dd734fd403458
  3 passed in 1.49s
  ```
  `ruff check`/`ruff format --check` 仅检查新增脚本与测试，通过；`git diff --check` 通过。
- 未做或未验证：OPT03-C 读取/下载留痕未做；链尾表方案未采用（选择 PG 咨询锁并在迁移注释说明）；未做恢复后校验与跨事务所并发独立吞吐压测。
- 顺带发现：SQLite 已在 176 限制改删，177 必须临时解除 UPDATE 守卫才能回填；回填只允许 `OLD.row_hash IS NULL` 的一次性更新，避免以后借回填绕过。

### OPT03 候选更新（2026-11-17）

- HEAD：`767f7cdf7c717c98d3516a0e31b53bd8cd9ae1a9`
- source：`2280 / 9fb6087ac77ac2b81c6e8baa8bd8271d406fbcbfc502ff99595ff7bf0a667ddc`
- migration：`425 / a2f0b783051cd50a5a200096ec52482d176418264a2136d2ee15fd15acf40b6b`
- 说明：包含 OPT03-A/B 新增迁移、脚本和测试；工作区仍未提交，不能把 FSC/OPT02 的既有改动单独计为本模块成果。

## OPT03-C  <状态：完成>

- 现状核实：附件下载、月包下载、回款模板导出原先均不写审计；审计表已有 A 的不可改删和 B 的哈希链，但读取/下载对象没有留痕。C 要求下载对象、版本/摘要、主体和事务一致性可追溯，且审计失败不能返回内容。
- 失败测试：
  - 新增 `nexus_backend/tests/test_opt03_download_audit.py`：附件成功下载后应有 `attachment.download`；无权下载不得写审计；审计写入抛错时下载必须失败且无审计行。
  - 修改前附件下载不写审计，第一条断言失败（`len(rows)==0`）。
  - 月包测试原先只断言 ZIP 返回，未断言 `export.download`；模板测试原先未断言 `receipt_template` 审计。
- 修改文件：
  - 新增 `nexus_backend/app/accounting/audit.py::write_download_audit`：与下载同一事务写 `audit_events`，只记录对象 ID、类型、版本、SHA-256、大小等非内容摘要。
  - `nexus_backend/app/intake/service.py::download` 改为写事务，在授权和原件读取成功后写 `attachment.download`；审计失败回滚、不返回字节。
  - `nexus_backend/app/delivery/monthly_package.py::download` 在写事务内重新授权并写 `export.download`（monthly_package/version/sha256/size_bytes）。
  - `nexus_backend/app/delivery/service.py::template` 在写事务内以 `read` 能力重新授权并写 `export.download`（receipt_template/format/status/sha256/size_bytes）；修正能力名 `view`→`read` 导致的 `KeyError`。
  - 未发现独立“报表下载”路由；月包内的 `report.html` 走 `export.download` 留痕。
- 验收命令与输出：
  ```
  cd nexus_backend && PYTHONPATH=..:. pytest -q \
    tests/test_opt03_audit_immutability.py tests/test_opt03_audit_chain.py \
    tests/test_opt03_download_audit.py \
    tests/test_monthly_delivery_package.py::test_frozen_zip_hashes_scope_and_restart \
    tests/test_intelligence_workflow.py::test_same_customer_invoice_export_delivery_and_close
  9 passed, 3 skipped
  ```
  ```
  python scripts/test_accounting_postgres_isolated.py --config tmp_test/fsc02-pg/env17/unified.json \
    nexus_backend/tests/test_monthly_delivery_postgres.py::test_pg_freeze_download_and_independent_exact_hash_acknowledgement -q
  1 passed in 3.54s   # artifact fcbb5c7bbe3d4bcb96bc78cb57331a3c
  python scripts/test_accounting_postgres_isolated.py --config tmp_test/fsc02-pg/env16/unified.json \
    nexus_backend/tests/test_monthly_delivery_postgres.py::test_pg_freeze_download_and_independent_exact_hash_acknowledgement -q
  1 passed in 3.44s   # artifact 1ec16e28ae9443bba1a38c021cb047f6
  ```
  `ruff check`、`ruff format --check`（新增/改动的下载相关文件）、`git diff --check` 通过。
- 未做或未验证：月包/模板路径未单独做“审计写入抛错”的故障注入，仅附件路径有 fail-closed 测试；报表无独立下载路由，不能声称报表直链已覆盖；未跑全量 backend。
- 顺带发现：AccessControl 的能力键是 `read`，不是 `view`；月包与模板都必须先在写事务内重授权，不能沿用写文件前的只读结果。

### OPT03 候选更新（2026-11-17，含 C）

- HEAD：`767f7cdf7c717c98d3516a0e31b53bd8cd9ae1a9`
- source：`2282 / a67185141d05d0f2fce5b0905e58b116f00473cb70e37ab891aa61cbb7e7f8ae`
- migration：`425 / a2f0b783051cd50a5a200096ec52482d176418264a2136d2ee15fd15acf40b6b`
- 说明：C 没有新增迁移；source 变化来自审计写入器、下载路径和测试。工作区整体仍未提交，候选不包含其他任务未提交内容的归属声明。

## OPT04-A  <状态：完成>

- 现状核实：`test_demo_cloud_postgres.py::test_pg_all_tables_force_rls_and_internal_schedule_has_no_direct_runtime_access` 在 PG17 失败 `assert 24 == 0`，24 张表未 FORCE；其中 19 张有租户列/父表，5 张为全局配置或恢复内部表。`fee_receivable_*` 与 `fsc_company_profiles` 的读写共用 `company_visible` 策略，只读 book 角色也能通过 RLS 写。
- 失败测试：
  - 先跑现成全表断言：PG17 1 failed，artifact `tmp_test/ak05-pg-runs/bc45687388114fd5acc5f07945e2eda8`。
  - 新增 `nexus_backend/tests/test_opt04_rls_postgres.py`：事务所甲 owner 读不到乙的应收和档案；甲 owner 写乙的应收被 RLS 拒绝；甲 `viewer`+book `editor` 读得到本公司应收但 INSERT/UPDATE 被拒；甲 owner 可新建本企业档案；乙 owner 只看得到乙行。
- 修改文件：
  - 新增 `nexus_backend/migrations/postgres/263_opt04_force_rls_tenant_tables.sql`：19 张租户表/事件表 `ENABLE + FORCE RLS`；事件表按父表可见性补策略；5 张无租户列全局表（`ai_endpoint_capabilities`、`ai_trusted_endpoints`、`recovery_release_audit`、`report_templates`、`restore_state`）也 `ENABLE + FORCE`，以显式 `USING(true)` 策略保留原有全局访问语义。
  - 新增 `nexus_backend/migrations/postgres/264_opt04_fee_fsc_write_policies.sql`：拆分 `fee_receivable_items/allocations/refunds` 与 `fsc_company_profiles` 的读策略和写策略；写策略要求 `ai_book_reviewer(book_id)`（费用）或 `company_manager(company_id)`（档案），不再只凭 `company_visible` 放行。
  - 新增 `nexus_backend/migrations/sqlite/178_opt04_force_rls_noop.sql`、`179_opt04_fee_fsc_write_policies_noop.sql`：SQLite 无 RLS，前向空操作仅对齐迁移流，未改历史迁移。
  - 新增 `nexus_backend/tests/test_opt04_rls_postgres.py`：跨所读、跨所写、只读写入、同所 owner 写入四类负向/正向断言。
- 验收命令与输出：
  ```
  python scripts/test_accounting_postgres_isolated.py --config tmp_test/fsc02-pg/env17/unified.json \
    nexus_backend/tests/test_demo_cloud_postgres.py::test_pg_all_tables_force_rls_and_internal_schedule_has_no_direct_runtime_access -q
  1 passed in 2.53s   # artifact a8cc32b866f2414f888f5cb789a3aa45
  ```
  ```
  python scripts/test_accounting_postgres_isolated.py --config tmp_test/fsc02-pg/env16/unified.json \
    nexus_backend/tests/test_demo_cloud_postgres.py::test_pg_all_tables_force_rls_and_internal_schedule_has_no_direct_runtime_access -q
  1 passed in 2.63s   # artifact e6803529b7a44dd68f8b60c7728f1e7a
  ```
  ```
  python scripts/test_accounting_postgres_isolated.py --config tmp_test/fsc02-pg/env17/unified.json \
    nexus_backend/tests/test_opt04_rls_postgres.py -q
  1 passed in 0.72s   # artifact a6fef3455a1b4adfa1af484b7f849585
  ```
  ```
  python scripts/test_accounting_postgres_isolated.py --config tmp_test/fsc02-pg/env16/unified.json \
    nexus_backend/tests/test_opt04_rls_postgres.py -q
  1 passed in 0.71s   # artifact 43ed3c51476b4407aa1877741dc7bd89
  ```
  ```
  python scripts/test_accounting_postgres_isolated.py --config tmp_test/fsc02-pg/env17/unified.json \
    test_demo_cloud_postgres.py::... test_tax06_fees_postgres.py test_fsc04_contracts_postgres.py -q
  11 passed in 15.97s   # artifact 2382e49117cd462aaee92ac7755f5f53
  python scripts/test_accounting_postgres_isolated.py --config tmp_test/fsc02-pg/env16/unified.json <same> -q
  11 passed in 15.88s   # artifact a9cab7bee11442559e454a6f3a0c1652
  ```
  `ruff check` 新测试、`ruff format --check` 新测试、`git diff --check` 通过。
- 未做或未验证：未重跑全表权限扫描之外的跨模块全量 PG；5 张全局表的 `USING(true)` 是保留既有语义，不是租户隔离；未做 `ai_route_decisions` 等其余租户表的跨所负向用例；OPT04-B/C/D 未开始。
- 顺带发现：PG 事务在读策略失败后必须回滚/重开连接再断言下一条，否则后续语句会得到 `InFailedSqlTransaction`；测试已按此拆分连接，不计为产品失败。

### OPT04 候选更新（2026-11-17，A）

- HEAD：`767f7cdf7c717c98d3516a0e31b53bd8cd9ae1a9`
- source：`2287 / 2db0989fb11757add4bdcdc68e92a73484786d8bc26da02b61f5386160adf9da`
- migration：`429 / 71b0b874963c76c0b0506a788fe2b629801ad52c2e612696248fa3f10567d4df`
- 说明：候选包含 OPT03-C、OPT04-A 的新增迁移/测试；工作区仍未提交，不把历史 FSC/OPT 改动分别归属错位。


## OPT04-B  <状态：完成>

- 现状核实：`fsc_company_profiles` 的版本/supersede 查询只按 `company_id`，合同/证据策略同样；SQLite 下两个事务所共享同一企业时会互相覆盖草稿。旧唯一索引 `idx_admin_single_servicing_firm` 使第二家事务所无法登记同一企业，测试夹具先撞该约束。`fsc_evidence_request_links` 的 FK 在 SQLite 重建策略表后仍指向被重命名的旧表。
- 失败测试：
  - 新增 `nexus_backend/tests/test_opt04_profile_scope.py`：两个事务所共享同一企业，各自 v1；甲 v2 后乙仍为 v1/draft；合同/证据策略同样独立。
  - 修改 matrix 前，乙事务所读取到甲事务所的 v2：`assert (2,'draft') == (1,'draft')` 失败。
  - 新增 `nexus_backend/tests/test_opt04_profile_scope_postgres.py`：PG 下两所共享企业、同所重复 v1 唯一冲突、各自只看到自己的版本。
- 修改文件：
  - `sqlite/180_opt04_profile_firm_scope.sql`：删旧唯一索引；重建档案表为 `UNIQUE(firm_id,company_id,version)`，保留外键、确认不可变和禁删触发器。
  - `postgres/265_opt04_profile_firm_scope.sql`：删旧索引与旧唯一约束，新增 `(firm_id,company_id,version)` 唯一约束。
  - `sqlite/181_opt04_contract_evidence_firm_scope.sql` / `postgres/266_opt04_contract_evidence_firm_scope.sql`：合同、证据策略唯一键改按 firm；单一生效索引改 `(firm_id,industry_code)`；内容不可变/禁删保持不变。
  - `sqlite/182_opt04_evidence_link_fk.sql`：重建 `fsc_evidence_request_links`，FK 指向新的 `fsc_evidence_policies`，保留数据与索引。
  - `app/fsc/profiles.py`：版本、草稿 supersede、确认 supersede、active、列表全部加 `firm_id`；能力矩阵三个子查询补 `p.firm_id=fc.firm_id`。
  - `app/fsc/contracts.py`、`app/fsc/evidence.py`：版本/supersede/active 查询加 `firm_id`。
- 验收命令与输出：
  ```
  cd nexus_backend && PYTHONPATH=..:. pytest -q tests/test_fsc01_profiles.py     tests/test_fsc03_organization.py tests/test_fsc04_contracts.py tests/test_fsc04_imports.py     tests/test_fsc04_advertising.py tests/test_fsc05_evidence.py tests/test_opt04_profile_scope.py
  14 passed in 33.03s
  ```
  ```
  python scripts/test_accounting_postgres_isolated.py --config tmp_test/fsc02-pg/env17/unified.json     test_opt04_rls_postgres.py test_fsc04_contracts_postgres.py test_fsc04_imports_postgres.py     test_fsc04_advertising_postgres.py test_fsc05_evidence_postgres.py -q
  6 passed in 3.33s   # artifact 48cb0078d14941f1a2984b5866100edc
  ```
  ```
  python scripts/test_accounting_postgres_isolated.py --config tmp_test/fsc02-pg/env16/unified.json <same> -q
  6 passed in 3.40s   # artifact a563c0bbea444487a13d95a0aa9b5d5d
  ```
  ```
  python scripts/test_accounting_postgres_isolated.py --config tmp_test/fsc02-pg/env17/unified.json     nexus_backend/tests/test_opt04_profile_scope_postgres.py -q
  1 passed in 0.55s   # artifact 8e9a17bf06a2413ea16121c1f1cc23bd
  ```
  ```
  python scripts/test_accounting_postgres_isolated.py --config tmp_test/fsc02-pg/env16/unified.json     nexus_backend/tests/test_opt04_profile_scope_postgres.py -q
  1 passed in 0.58s   # artifact bd0c242d58f14877b4782429bd54413c
  ```
  `ruff check`、`ruff format --check`（profiles 与两个新增测试）通过；`git diff --check` 通过。
- 未做或未验证：合同/证据策略的 PG 共享企业矩阵未单独复跑，已由 SQLite 作用域用例与 PG FSC 测试覆盖其读写路径；未做全仓查询审计；老代码“先按主键取出再鉴权”不在本模块范围。
- 顺带发现：SQLite 重建被 FK 引用的父表时，必须同时重建引用表或确认 FK 已重指向；本次 182 修正了 181 留下的 `fsc_evidence_policies_legacy` 悬空引用。

### OPT04 候选更新（2026-11-17，含 B）

- HEAD：`767f7cdf7c717c98d3516a0e31b53bd8cd9ae1a9`
- source：`2294 / a3579ae0be21b7d9097a6e9bf55f7bd8ce09ac6550d761d752416c13a6828064`
- migration：`434 / cda51cb09c1a01b153858c0ee32c204d34eee0f67675d777aef727bb157fb0e2`
- 说明：包含 OPT04-A/B 新增迁移、作用域修复和测试；工作区仍未提交，不把历史 FSC/OPT 改动分别归属错位。


## OPT04-B  <状态：完成>

- 现状核实：`fsc_company_profiles` 的版本/supersede 查询只按 `company_id`，合同/证据策略同样；SQLite 下两个事务所共享同一企业时会互相覆盖草稿。旧唯一索引 `idx_admin_single_servicing_firm` 使第二家事务所无法登记同一企业。`fsc_evidence_request_links` 的 FK 在 SQLite 重建策略表后仍指向被重命名的旧表。
- 失败测试：
  - 新增 `nexus_backend/tests/test_opt04_profile_scope.py`：两个事务所共享同一企业，各自 v1；甲 v2 后乙仍为 v1/draft；合同/证据策略同样独立。
  - 修改 matrix 前，乙事务所读取到甲事务所的 v2：`assert (2,'draft') == (1,'draft')` 失败。
  - 新增 `nexus_backend/tests/test_opt04_profile_scope_postgres.py`：PG 下两所共享企业、同所重复 v1 唯一冲突、各自只看到自己的版本。
- 修改文件：
  - `sqlite/180_opt04_profile_firm_scope.sql`：删旧唯一索引；重建档案表为 `UNIQUE(firm_id,company_id,version)`，保留外键、确认不可变和禁删触发器。
  - `postgres/265_opt04_profile_firm_scope.sql`：删旧索引与旧唯一约束，新增 `(firm_id,company_id,version)` 唯一约束。
  - `sqlite/181_opt04_contract_evidence_firm_scope.sql` / `postgres/266_opt04_contract_evidence_firm_scope.sql`：合同、证据策略唯一键改按 firm；单一生效索引改 `(firm_id,industry_code)`。
  - `sqlite/182_opt04_evidence_link_fk.sql`：重建 `fsc_evidence_request_links`，FK 指向新的 `fsc_evidence_policies`。
  - `app/fsc/profiles.py`：版本、草稿 supersede、确认 supersede、active、列表全部加 `firm_id`；能力矩阵三个子查询补 `p.firm_id=fc.firm_id`。
  - `app/fsc/contracts.py`、`app/fsc/evidence.py`：版本/supersede/active 查询加 `firm_id`。
- 验收命令与输出：
  ```
  cd nexus_backend && PYTHONPATH=..:. pytest -q tests/test_fsc01_profiles.py     tests/test_fsc03_organization.py tests/test_fsc04_contracts.py tests/test_fsc04_imports.py     tests/test_fsc04_advertising.py tests/test_fsc05_evidence.py tests/test_opt04_profile_scope.py
  14 passed in 33.03s
  ```
  ```
  python scripts/test_accounting_postgres_isolated.py --config tmp_test/fsc02-pg/env17/unified.json     test_opt04_rls_postgres.py test_fsc04_contracts_postgres.py test_fsc04_imports_postgres.py     test_fsc04_advertising_postgres.py test_fsc05_evidence_postgres.py -q
  6 passed in 3.33s   # artifact 48cb0078d14941f1a2984b5866100edc
  ```
  ```
  python scripts/test_accounting_postgres_isolated.py --config tmp_test/fsc02-pg/env16/unified.json <same> -q
  6 passed in 3.40s   # artifact a563c0bbea444487a13d95a0aa9b5d5d
  ```
  ```
  python scripts/test_accounting_postgres_isolated.py --config tmp_test/fsc02-pg/env17/unified.json     nexus_backend/tests/test_opt04_profile_scope_postgres.py -q
  1 passed in 0.55s   # artifact 8e9a17bf06a2413ea16121c1f1cc23bd
  python scripts/test_accounting_postgres_isolated.py --config tmp_test/fsc02-pg/env16/unified.json     nexus_backend/tests/test_opt04_profile_scope_postgres.py -q
  1 passed in 0.58s   # artifact bd0c242d58f14877b4782429bd54413c
  ```
  `ruff check`、`ruff format --check`（profiles 与两个新增测试）通过；`git diff --check` 通过。
- 未做或未验证：合同/证据策略的 PG 共享企业矩阵未单独复跑；未做全仓查询审计；老代码“先按主键取出再鉴权”不在本模块范围。
- 顺带发现：SQLite 重建被 FK 引用的父表时，必须同时重建引用表或确认 FK 已重指向；182 修正了 181 留下的 `fsc_evidence_policies_legacy` 悬空引用。

### OPT04 候选更新（2026-11-17，含 B）

- HEAD：`767f7cdf7c717c98d3516a0e31b53bd8cd9ae1a9`
- source：`2294 / a3579ae0be21b7d9097a6e9bf55f7bd8ce09ac6550d761d752416c13a6828064`
- migration：`434 / cda51cb09c1a01b153858c0ee32c204d34eee0f67675d777aef727bb157fb0e2`
- 说明：包含 OPT04-A/B 新增迁移、作用域修复和测试；工作区仍未提交，不把历史 FSC/OPT 改动分别归属错位。

## OPT04-C  <状态：完成>

- 现状核实：`app/fsc/profiles.py` 的建档/确认仍用全局写锁；`fsc/contracts.py`、`fsc/evidence.py`、`fsc/imports.py`、`fsc/advertising.py`、`tax/basis.py`、`fee_receivable` 分配/撤销/退款、规费复核/失效同样默认 `write_scope="global"`。全局锁下两个不同企业的写会互相阻塞。`tax/fees.py::generate` 还写 `sources`、`drafts`、`draft_lines`，属于旧兼容写域，不能在当前企业锁内完成，故保留全局锁并在此说明。
- 失败测试：
  - 新增 `nexus_backend/tests/test_opt04_profile_scope_postgres.py::test_company_write_lock_does_not_block_a_different_company`：会话一持有企业甲写锁，线程为企业乙建档，要求 3 秒内完成。
  - 临时把 `FscProfileService.create_draft` 还原为全局锁后运行，实际失败：`AssertionError: company B write was blocked by company A lock`（artifact `d2922893b4c5434e919e9e10fab14c63`）。随后恢复代码。
- 修改文件：
  - `app/fsc/profiles.py`：`create_draft` 企业写事务；`confirm` 先只读取 company，再用同 company 写。
  - `app/fsc/contracts.py`：建档/确认/生效/停用改用企业写事务；确认/生效/停用先只读 company。
  - `app/fsc/evidence.py`：建档/确认/生效/停用/补件任务生成改用企业写事务。
  - `app/fsc/imports.py`：导入信封创建/确认改用企业写事务。
  - `app/fsc/advertising.py`：广告草稿生成改用企业写事务。
  - `app/tax/basis.py`：计税核定生成/复核改用企业写事务。
  - `app/tax/fees.py`：核销、撤销、退款、复核、失效改用企业写事务；`generate` 保留全局并在注释/本段说明原因。
  - `app/intake/service.py::download`：下载审计改用企业写事务，避免把新审计写点留在全局基线上。
  - `nexus_backend/tests/write_scope_baseline.txt`：从 OPT02-D 基线删除已显式声明范围的 20 条。
- 验收命令与输出：
  ```
  cd nexus_backend && PYTHONPATH=..:. pytest -q \
    tests/test_opt04_profile_scope.py tests/test_fsc01_profiles.py tests/test_fsc03_organization.py \
    tests/test_fsc04_contracts.py tests/test_fsc04_imports.py tests/test_fsc04_advertising.py \
    tests/test_fsc05_evidence.py tests/test_tax06_fees.py tests/test_tax14_basis.py \
    tests/test_opt03_download_audit.py tests/test_write_scope_baseline.py
  41 passed in 87.29s
  ```
  ```
  python scripts/test_accounting_postgres_isolated.py --config tmp_test/fsc02-pg/env17/unified.json \
    nexus_backend/tests/test_opt04_rls_postgres.py nexus_backend/tests/test_opt04_profile_scope_postgres.py \
    nexus_backend/tests/test_fsc04_contracts_postgres.py nexus_backend/tests/test_fsc04_imports_postgres.py \
    nexus_backend/tests/test_fsc04_advertising_postgres.py nexus_backend/tests/test_fsc05_evidence_postgres.py \
    nexus_backend/tests/test_tax06_fees_postgres.py -q
  17 passed in 16.69s   # artifact 4f585890ffde44c395d0660234c28a82
  ```
  ```
  python scripts/test_accounting_postgres_isolated.py --config tmp_test/fsc02-pg/env16/unified.json <same> -q
  17 passed in 17.06s   # artifact 0b569dd8f6bf427e9fd0ede674d9fb83
  ```
  ```
  python scripts/test_accounting_postgres_isolated.py --config tmp_test/fsc02-pg/env17/unified.json \
    nexus_backend/tests/test_opt04_profile_scope_postgres.py -q
  2 passed in 0.79s   # artifact f1084bab904a4cce84ef998b873be20e；含企业甲/乙不互阻塞与共享企业版本隔离
  ```
  `ruff check`、`ruff format --check`（app/fsc、tax/basis、tax/fees、intake、新增测试）通过。
- 未做或未验证：`tax/fees.py::generate` 保留全局锁（写旧兼容域 sources/drafts/draft_lines）；`app/fsc/` 之外仍有历史全局写；未做全仓锁审计；OPT02-D 基线已收缩但仍保留既有历史条目。
- 顺带发现：企业级锁只在事务进入时声明范围，不会自动把新表纳入行级触发器；本次以显式事务范围为准，未新增表级 guard。

### OPT04 候选更新（2026-11-17，含 C）

- HEAD：`767f7cdf7c717c98d3516a0e31b53bd8cd9ae1a9`
- source：`2294 / 80a19e2640f3f3db044dd7b9c56ec99a9373f4e42530a907bd6b8afec38b277e`
- migration：`434 / cda51cb09c1a01b153858c0ee32c204d34eee0f67675d777aef727bb157fb0e2`
- 说明：包含 OPT04-A/B/C 改动；工作区仍未提交，不把历史 FSC/OPT 改动分别归属错位。

## OPT04-D  <状态：完成>

- 现状核实：`posting/service.py::post_draft` 先查 `voucher_sources(source_kind='draft',source_id=draft_id)` 再插入；表上只有非唯一索引。同一逻辑来源可对应红冲/更正多行，故唯一性按 `(source_kind,source_id)`，仅覆盖 `draft`/`source`；红冲唯一性继续由 `voucher_relations` 的 `kind='reversal'` 部分唯一索引保证。SQLite 的 `voucher_sources.source_kind` 还只允许 draft/source/manual，且 `source_id` NOT NULL，所以不能把 reversal 纳入本索引。
- 失败测试：
  - SQLite `test_voucher_sources_are_unique_per_logical_source` 修改前同来源第二行未被拒（红测）；加索引后第二行 `sqlite3.IntegrityError`，不同来源可插入。
  - PG 新增 `test_opt04_posting_postgres.py`：先完成人工凭证，再直接插入第二行同来源；修改前成功，修改后 `UniqueViolation`。并发用例双会话同来源插入，恰一成功一冲突。
- 修改文件：
  - `migrations/postgres/267_opt04_voucher_source_unique.sql`：存量重复检查（`ERRCODE=23505`），不删数据；`uq_voucher_sources_logical` 部分唯一索引，条件仅 `draft`/`source`。
  - `migrations/sqlite/183_opt04_voucher_source_unique.sql`：同部分唯一索引；历史重复时迁移失败，不清理数据。
  - `app/posting/service.py::post_draft`：PG 在来源检查前 `pg_advisory_xact_lock(hashtext('voucher-source:draft:'||draft_id))`，使同草稿并发按序进入既有 `409 该草稿已经生成过正式凭证` 分支。
  - `tests/test_posting_service.py`、`tests/test_opt04_posting_postgres.py`。
- 验收命令与输出：
  ```
  cd nexus_backend && PYTHONPATH=..:. pytest -q \
    tests/test_posting_service.py::test_voucher_sources_are_unique_per_logical_source \
    tests/test_posting_service.py::test_manual_voucher_and_reversal_preserve_original
  2 passed in 10.15s
  ```
  ```
  python scripts/test_accounting_postgres_isolated.py --config tmp_test/fsc02-pg/env17/unified.json \
    nexus_backend/tests/test_opt04_posting_postgres.py -q
  2 passed in 3.54s   # artifact 4171cf37bb7f457cac3114584243eda4
  ```
  ```
  python scripts/test_accounting_postgres_isolated.py --config tmp_test/fsc02-pg/env16/unified.json \
    nexus_backend/tests/test_opt04_posting_postgres.py -q
  2 passed in 3.20s   # artifact e4c375b7056a46d6b78f777a4be66a14
  ```
  `ruff check`、`ruff format --check`、`git diff --check` 通过。
- 未做或未验证：应用层 `post_draft` 的同源并发 409 未单独用完整草稿链路复现；数据库唯一约束和双会话冲突已实测。历史库若已有重复来源，迁移会拒绝启动而不是自动清理。
- 顺带发现：SQLite 的 `voucher_sources` 不允许 reversal/adjustment，冲销唯一性在 `voucher_relations` 而非本表；迁移注释已说明，未放宽 schema。

### OPT04 候选更新（2026-11-17，含 D）

- HEAD：`767f7cdf7c717c98d3516a0e31b53bd8cd9ae1a9`
- source：`2297 / 045a78d713212734359c5036223fd259954cef0c4edac222ea93e29ed855aec8`
- migration：`436 / f793d0d9d59c95f47a905e36daaa366d0b9d08c69042c8e31f33cff5e089cab9`
- 说明：包含 OPT04-A/B/C/D；工作区仍未提交，不抹去历史 FSC/OPT 改动归属。


## OPT05-A  <状态：完成>

- 现状核实：`scripts/run_accounting_service.py` 的 `ROLES` 只有 `api/worker/assistant/migration/maintenance`，`WORKER_MODULES` 没有批量入口；`BatchWorker` 只由 `app.main` 嵌入模式和 `app.unified_worker` 构造。按 `docker-compose.demo.yml` 部署时批量操作无人领取。`run_accounting_unified.py` 只是统一后台薄入口，不单独承担 batch 角色。
- 失败测试：
  - 新增 `test_demo_deployment.py::test_batch_role_is_a_first_class_worker_without_embedded_api`：修改前 `AssertionError: assert 'batch' in ('api', ...)`。
  - 修改后同用例及既有 role 分派参数化用例通过。
- 修改文件：
  - 新增 `nexus_backend/run_accounting_batch.py`：独立批量入口，构造 `IntelligenceService`/`MonthlyBatchService`/`BatchOperationsService`/`BatchWorker`；限制 worker 登录、校验 restore hold、私有存储、SIGINT/SIGTERM、`--once`、ready callback 后排空；不启动 API。
  - `scripts/run_accounting_service.py`：`ROLES` 加 `batch`，`WORKER_MODULES["batch"]="run_accounting_batch"`，中文校验文案同步。
  - `scripts/check_demo_deployment.py`：角色 choices/校验加 `batch`。
  - `docker-compose.demo.yml`：新增 `batch` 服务，`ACCOUNTING_SERVICE_ROLE: batch`，使用 `${ACCOUNTING_BATCH_WORKER_DATABASE_URL}`，不配置 API DSN，不挂端口。
- 验收命令与输出：
  ```
  cd nexus_backend && PYTHONPATH=..:. pytest -q \
    tests/test_demo_deployment.py tests/test_batch_operations.py
  42 passed in 9.79s
  ```
  ```
  python scripts/check_demo_deployment.py --role batch --env-file tmp_test/opt05-batch.env --json
  runtime=ok storage=ok model_*=ok pricing_*=warning business_setup=manual
  ```
  说明：`tmp_test/opt05-batch.env` 为本地合成配置，非真实云凭据；pricing/business_setup 警告按预检设计保留，runtime/storage 无 error。
- 未做或未验证：真实 compose 启动 `batch` 容器并让数据库队列从 queued 到终态；本机只用 `BatchWorker`/服务既有测试验证；真实云端 worker DSN/对象存储/健康探针未接。
- 顺带发现：批量入口与统一后台共享同一 `BatchOperationsService` 构造；未改 `unified_worker.py` 行为。

### OPT05 候选更新（2026-11-17，含 A）

- HEAD：`767f7cdf7c717c98d3516a0e31b53bd8cd9ae1a9`
- source：`2297 / 7710868bca6d067e0402ebd54a6e78caf83d0f70ec73c25ed5e9fe12d483957c`
- migration：`436 / f793d0d9d59c95f47a905e36daaa366d0b9d08c69042c8e31f33cff5e089cab9`
- 说明：新增批量角色/入口/compose；未提交，历史 FSC/OPT 改动仍在工作区。


## OPT05-B  <状态：完成>

- 现状核实：`BatchWorker.process` 在 `execute` 期间没有续租，租约到期后 `recover` 会把仍在执行的任务重新排队；旧执行器完成后仍会调用 `finish`，可能覆盖新执行器的结果。`BatchOperationsService` 只有 claim/finish，没有 token 绑定的续租入口。
- 失败测试：
  - 新增 `tests/test_opt05_batch_lease.py`：长任务租约 2 秒、执行 3 秒，2.2 秒时 `recover()` 必须为 0；失去续租时必须停止提交结果。
  - 修改前 `BatchWorker.__init__() got an unexpected keyword argument 'lease_seconds'`，2 failed；实现后第一个用例先因断言取到 queued 的第二项、第二个用例先因断言取到 queued 首项而红，均按实际 claim 顺序/状态修正断言。
- 修改文件：
  - `migrations/postgres/268_opt05_batch_lease_renewal.sql`：`accounting.worker_renew_batch_item(item,token,seconds)`，SECURITY DEFINER 固定 search_path；只更新 `status='running'`、token 匹配、`lease_until>now` 的行，续租取 `GREATEST(当前租约, now+seconds)`；REVOKE PUBLIC/app，GRANT worker。
  - `migrations/postgres/269_opt05_worker_schema_usage.sql`：补 `GRANT USAGE ON SCHEMA accounting TO accounting_worker`（268 已应用后新增前向授权，不改 268）。
  - `migrations/sqlite/184_opt05_batch_lease_renewal.sql`：SQLite 用服务层条件 UPDATE，迁移为空操作。
  - `app/batch_ops/service.py::renew(item, lease_seconds=120)`：无 token 返回 False；PG 调函数；SQLite 条件 UPDATE；使用 worker 事务提交。
  - `app/batch_ops/worker.py`：`lease_seconds`/`renew_interval` 构造参数；执行期间后台续租；续租失败置 lost；lost 后不调用 `finish`；所有分支 finally 停止并 join 续租线程；`claim` 使用配置租约。
  - `tests/test_opt05_batch_lease.py`、`tests/test_opt05_batch_lease_postgres.py`。
- 验收命令与输出：
  ```
  cd nexus_backend && PYTHONPATH=..:. pytest -q tests/test_opt05_batch_lease.py
  2 passed in 11.97s
  ```
  ```
  cd nexus_backend && PYTHONPATH=..:. pytest -q     tests/test_batch_operations.py tests/test_demo_deployment.py tests/test_opt05_batch_lease.py
  44 passed in 16.00s
  ```
  ```
  python scripts/test_accounting_postgres_isolated.py --config tmp_test/fsc02-pg/env17/unified.json     nexus_backend/tests/test_opt05_batch_lease_postgres.py -q
  3 passed in 1.87s   # artifact 6fc72bc0d49b4bfaaad6d4e7dd2086df
  ```
  ```
  python scripts/test_accounting_postgres_isolated.py --config tmp_test/fsc02-pg/env16/unified.json     nexus_backend/tests/test_opt05_batch_lease_postgres.py -q
  3 passed in 1.84s   # artifact e36090675c7a441bb3739050344a77ca
  ```
  PG 用例覆盖：有效 token 续租成功、错误 token 不续租、过期租约不续租、已结束任务不续租、`accounting_worker` 具备 schema USAGE 与函数 EXECUTE。
- 未做或未验证：真实 compose `batch` 容器从 queued 到终态的端到端；两个真实执行器同时争抢同一项的运行时观测；真实云端 worker DSN/对象存储/健康探针。未被真实环境验证的部分不冒充通过。
- 顺带发现：首次运行曾直接修改已应用的 268 补授权，触发迁移 checksum 拒绝；已恢复 268 并新增 269 前向授权，未改写已应用迁移。
- 未提交/推送/部署。

### OPT05 候选更新（2026-11-17，含 B）

- HEAD：`767f7cdf7c717c98d3516a0e31b53bd8cd9ae1a9`
- source：`2302 / 7ee99dc96b299d9d4a2b14a6902bf002c7f6823507adb69772fbc2cc4984b760`
- migration：`439 / ae4f9f3936d1ff35a9526cb79892f82601f11b387046629295502662a4311cd5`
- 说明：含 OPT05-A/B；工作区未提交，历史 FSC/OPT 改动仍在工作区。


## OPT05-C  <状态：完成>

- 现状核实：前端 `src/MonthlyBatchDialog.tsx` 已调用 `/api/firm/batch-operations/monthly-open` 并轮询批次；`BatchOperationsService.create_monthly_open`/`BatchWorker` 已支持持久批次、幂等和单失败项重试。旧 `POST /api/monthly/batch/open` 仍直接调用 `MonthlyBatchService.open_month`，可绕过队列。
- 失败测试：新增 `tests/test_opt05_queue_open.py`，修改前旧入口同步执行且返回非 202；实现后验证 202、queued、无 results、重复请求同批次 id，并设置 loopback `client` scope。扩展 `test_batch_operations.py::test_monthly_open_partial_failure_retries_only_failed_item`，覆盖失败项重试后全部完成且每企业仅一本账。
- 修改文件：
  - `app/monthly/workbench_router.py`：旧入口改为 202 入队，派生 `monthly-open:<payload fingerprint>` 幂等键，调用 `BatchOperationsService.create_monthly_open`，不再同步开账。
  - `tests/test_opt05_queue_open.py`：新增 HTTP 队列契约测试。
  - `tests/test_batch_operations.py`：补重试后完成、无重复账套断言。
- 验收命令与输出：
  ```
  cd nexus_backend && PYTHONPATH='..;.' pytest -q     tests/test_batch_operations.py tests/test_opt05_queue_open.py tests/test_opt05_batch_lease.py
  5 passed in 19.50s
  ```
  ```
  cd nexus_backend && PYTHONPATH='..;.' pytest -q -k batch
  1 failed, 139 passed, 40 skipped, 6 errors in 176.04s
  ```
  ```
  npx playwright test e2e/monthly-batch.spec.ts --reporter=line
  1 passed (18.1s)
  ```
  说明：浏览器用例原先把企业名同时匹配标题和“当前对象”导致 strict mode 冲突，已改为 heading 定位；截图 `artifacts/monthly-batch-dialog.png` 显示“已开期 2 家 / 进度 2/2”。`-k batch` 的 1 failed/6 errors 均为既有环境契约问题（4 个旧 `TestClient(client=...)` 在 Starlette 0.35 不支持；PG worker 用例缺 `ACCOUNTING_TEST_WORKER_DATABASE_URL`），不是本次队列改动失败；不能据此宣称全量 batch 全绿。
- 未做或未验证：真实 compose `batch` 容器 consume 旧入口产生的批次至终态；真实双 worker 争抢运行观测；真实云 DSN/存储/健康探针。
- 顺带发现：旧入口重复请求的幂等键由完整 payload 生成，semantic 相同但字段顺序不同仍会不同；当前前端/接口调用稳定，未扩大改造范围。
- 未提交/推送/部署。

### OPT05 候选更新（2026-11-17，含 C）

- HEAD：`767f7cdf7c717c98d3516a0e31b53bd8cd9ae1a9`
- source：`2303 / 1e8796f765e6f97d2a083711f089b3c25b52728141591ee6c457549829671d33`
- migration：`439 / ae4f9f3936d1ff35a9526cb79892f82601f11b387046629295502662a4311cd5`
- 说明：含 OPT05-A/B/C；工作区未提交，历史 FSC/OPT 改动仍在工作区。


## OPT05-D  <状态：完成>

- 现状核实：批量/定时能力只有内存定时与嵌入 worker，没有持久计划；停机、并发扫描、租约/重复触发都没有数据库证据。`run_accounting_service.py` 仅有 api/maintenance/batch，compose 无独立 scheduler 服务。调度只能产生提醒/待确认批次，不能直接过账、关账、外发或调用模型。
- 失败测试：
  - 新增 `tests/test_opt05_scheduler.py`：tick 幂等、禁用不触发、monthly_open 确认前不可领取、employee 403、审计、SQL 规则拒绝、`SchedulerLoop`、停机三个月只补最近一次。
  - 修改前 `BatchWorker/Scheduler` 不存在或 `NameError: name 'plan' is not defined`；PG 版本先出现 boolean/JSONB/RLS 权限错误，均按真实契约修复。
- 修改文件：
  - `migrations/sqlite/185`、`postgres/270`：`fsc_schedule_plans/triggers/reminders`，dedupe 唯一键，PG RLS。
  - `postgres/271`：`GRANT EXECUTE ON FUNCTION accounting.actor_firm() TO accounting_system`。
  - `postgres/272`：调度三表 system SELECT/INSERT/UPDATE 授权。
  - `postgres/273`：调度三表 `TO accounting_system USING(TRUE) WITH CHECK(TRUE)` 系统策略（跨所扫描）。
  - `sqlite/186/187/188`：对应空操作。
  - `app/scheduler/service.py`：持久计划、上海时区换算 UTC、`_latest_due` 只补最近一次、PG advisory lock、同 dedupe 跳过、monthly_open 暂停批次、确认后放行、审计；`triggers` 返回 reminder kind。
  - `app/scheduler/router.py`、`app/scheduler/runner.py`、`run_accounting_scheduler.py`、`main.py`。
  - `scripts/run_accounting_service.py`、`scripts/check_demo_deployment.py`、`docker-compose.demo.yml`、`tests/test_demo_deployment.py`。
- 验收命令与输出：
  ```
  cd nexus_backend && PYTHONPATH='..;.' pytest -q tests/test_opt05_scheduler.py
  7 passed in 14.34s
  ```
  ```
  cd nexus_backend && PYTHONPATH='..;.' pytest -q tests/test_demo_deployment.py tests/test_opt05_scheduler.py tests/test_opt05_scheduler_postgres.py tests/test_batch_operations.py
  51 passed, 1 skipped in 19.10s
  ```
  ```
  python scripts/test_accounting_postgres_isolated.py --config tmp_test/fsc02-pg/env17/unified.json nexus_backend/tests/test_opt05_scheduler_postgres.py -q
  1 passed in 1.29s   # artifact 9e866fd2a54c45a3abc4b16873a64eea
  ```
  ```
  python scripts/test_accounting_postgres_isolated.py --config tmp_test/fsc02-pg/env16/unified.json nexus_backend/tests/test_opt05_scheduler_postgres.py -q
  1 passed in 1.19s   # artifact db4a96f2c3434ec69f8076bc35760e8c
  ```
  ```
  python scripts/check_demo_deployment.py --role scheduler --env-file tmp_test/opt05-scheduler.env --json
  runtime/storage/model_*=ok, pricing_*=warning, 0 error
  ```
  ```
  python nexus_backend/run_accounting_scheduler.py --once   # PG17 and PG16 app DSN
  exit 0 both
  ```
- 未做或未验证：真实 compose `scheduler` 容器、云端 system DSN/健康探针、双 scheduler 进程长时间争抢；真实供应商/客户提醒发送。未被真实环境验证的部分不冒充通过。
- 顺带发现：
  - SQLite `Path.write_text` 默认把新文件写成 CRLF，已改为 LF；既有脚本只保留最小 diff，不整文件重排。
  - `Database` 默认角色只支持 `accounting_app/accounting_projection`，scheduler 进程必须用 `accounting_app`，由 system 事务切角色；直接传 `accounting_system` 会被拒绝。
  - 已应用迁移不可改；268/271 的 checksum 回滚后改用 272/273 前向修复。
- 未提交/推送/部署。

### OPT05 候选更新（2026-11-17，含 D）

- HEAD：`767f7cdf7c717c98d3516a0e31b53bd8cd9ae1a9`
- source：`2317 / 4cc474a32c88792f56b1762497f22b999bb132f2d5c4058395ef40c3549eba84`
- migration：`447 / 84bada1ce9f5a970305f1e7e3e6faa117f5e46bec29639249b315fe565e26d36`
- 说明：含 OPT05-A/B/C/D；工作区未提交，历史 FSC/OPT 改动仍在工作区。

## OPT05-E  <状态：部分完成>

- 现状核实：`CloseService.publish` 对已 `published` 的 run 仍会走报表发布与版本检查，响应丢失后的重试可能 409；SQLite 可覆盖，PG 需要真实 preflight→review→lock→publish 链才能构造 published run，DB 守卫拒绝直接插入。
- 失败测试：
  - `test_b04_pilot.py::test_periodic_accrual_asset_closing_reports_and_close` 增加“报表发布已提交、月结仍 locked”崩溃恢复与重复 publish 断言；修改前重试会再次进入发布路径/重复审计。
  - PG 尝试 `test_opt05_close_postgres.py` 直接插入 published run，被 `close_revision_guard()` 以 `CheckViolation: Current actor must create an unreviewed current preflight` 拒绝，说明夹具不能伪造历史，不是产品缺陷；该测试已删除，未以 skip 冒充通过。
- 修改文件：
  - `nexus_backend/app/b04_close/service.py::publish`：run 已 published 时直接返回 `_view`，不重复发布/审计。
  - `nexus_backend/tests/test_b04_pilot.py`：锁期后先直接发布报表模拟崩溃，再 `close.publish` 恢复；再次重试；断言 `close.publish` 审计 1 条、report_runs 1 条 published。
- 验收命令与输出：
  ```
  cd nexus_backend && PYTHONPATH='..;.' pytest -q tests/test_b04_pilot.py::test_periodic_accrual_asset_closing_reports_and_close
  1 passed in 10.43s
  ```
  ```
  tmp_test/opt02-ruff-venv/Scripts/ruff.exe check nexus_backend/app/b04_close/service.py nexus_backend/tests/test_b04_pilot.py
  All checks passed!
  ```
  PG 尝试：`test_opt05_close_postgres.py` 删除前失败 artifact `6a1eb67efe624af6befe3cc9f0336bc3`，错误为 `CheckViolation: Current actor must create an unreviewed current preflight`；这是 DB 守卫，不是产品失败。未取得 PG 幂等发布通过证据。
- 未做或未验证：PG16/17 的同链路幂等发布证据；真实响应丢失/网络断开；真实客户关账发布。
- 顺带发现：直接改历史/已应用迁移会触发 checksum；本模块无新增迁移。
- 未提交/推送/部署。

### OPT05 候选更新（2026-11-17，含 E）

- HEAD：`767f7cdf7c717c98d3516a0e31b53bd8cd9ae1a9`
- source：`2317 / b495239f88ea9e9d9dcc09ce88535c14413b814d25a1563c7c45852d60e36cca`
- migration：`447 / 84bada1ce9f5a970305f1e7e3e6faa117f5e46bec29639249b315fe565e26d36`
- 说明：含 OPT05-A/B/C/D 与 E 的 SQLite 幂等发布；PG published 重试证据缺失，E 标部分完成。

## OPT06-A  <状态：完成>

- 现状核实：`ledger_review_policies` 允许 `single_operator` 时，同所管理员可将其写回；缺少事务所级“禁止单人复核”的持久开关、MFA 配置入口与 DB 兜底。SQLite 与 PostgreSQL 都需要在策略插入时阻断。
- 失败测试：
  - 新增 `tests/test_opt06_review_control.py`：MFA 拒绝、禁用后切换单人 403 且审计 1 条、既有单人账套自我复核 403、直接 INSERT 触发器拒绝。
  - 修改前红：`firm_review_controls` 不存在 / `/api/firm/review-control` 404。
  - PG 首次 `migrate()` 因 274 未限定 `accounting.*` 报 `UndefinedTable relation "firms" does not exist`；修复后 PG16/17 触发器测试通过。
- 修改文件：
  - `migrations/sqlite/189_opt06_firm_review_control.sql`：控制表 + 策略 BEFORE INSERT 触发器。
  - `migrations/postgres/274_opt06_firm_review_control.sql`：同表、RLS、GRANT、策略触发器；所有表名限定 `accounting.*`。
  - `app/accounting/review_policy.py`、`review_policy_models.py`、`app/accounting/router.py`。
  - `src/review-policy.ts`、`src/ReviewPolicyPanel.tsx`。
  - `tests/test_opt06_review_control.py`、`tests/test_opt06_review_control_postgres.py`。
- 验收命令与输出：
  ```
  cd nexus_backend && PYTHONPATH='..;.' pytest -q tests/test_opt06_review_control.py
  3 passed in 9.90s
  ```
  ```
  python scripts/test_accounting_postgres_isolated.py --config tmp_test/fsc02-pg/env17/unified.json nexus_backend/tests/test_opt06_review_control_postgres.py -q
  1 passed in 0.78s   # artifact 092f5693d2394c35b75774820dd7d49a
  ```
  ```
  python scripts/test_accounting_postgres_isolated.py --config tmp_test/fsc02-pg/env16/unified.json nexus_backend/tests/test_opt06_review_control_postgres.py -q
  1 passed in 0.80s   # artifact 511fd1fcf4644018ad02ecc6c43b3222
  ```
  ```
  npm run typecheck
  exit 0
  ```
  ```
  tmp_test/opt02-ruff-venv/Scripts/ruff.exe check ... && ruff format --check ...
  All checks passed!
  ```
  `git diff --check` 通过。
- 未做或未验证：真实 MFA 供应商/管理员设备；集团多所策略界面批量配置；真实客户使用；PG 服务层 403 + 审计重试用例（当前 PG 只验证 DB 触发器）。
- 顺带发现：手工执行 274 后留下未登记对象，已 DROP 表/函数并重新走迁移；274 原未限定 schema，已修正。真实生产迁移前必须用受控迁移账号执行。
- 未提交/推送/部署。

### OPT06 候选更新（2026-11-17，含 A）

- HEAD：`767f7cdf7c717c98d3516a0e31b53bd8cd9ae1a9`
- source：`2321 / 088a8894e4fce74a73c34125793ac2ee2f2275020ca4330002a202d14ded6ac7`
- migration：`449 / 78517b805bbfcb4637604a38e4e61a97970e8571ee033fd2adb73f5ce966654c`
- 说明：含 OPT05 与 OPT06-A；工作区未提交。

## OPT06-B  <状态：完成>

- 现状核实：`AccessControl.permissions` 对 owner/admin 的 edit/review/export/manage 全短路；PG `rr_book_permissions` 对 owner/admin 的 can_review/can_edit 为真；`record_review_authority()` 只校验 `book_review_allowed`。这使“管理与复核分离”无法表达。
- 失败测试：
  - `tests/test_opt06_review_control.py::test_separation_requires_a_non_manager_reviewer_for_every_book`：修改前红（无字段、无补人检查）。
  - `tests/test_opt06_review_control_postgres.py::test_separation_removes_manager_review_but_keeps_non_manager_reviewer`：修改前红（视图仍给 owner can_review）。
- 修改文件：
  - `migrations/sqlite/190_opt06_management_separation.sql`、`postgres/275_opt06_management_separation.sql`：增 `management_review_separation`；PG 替换 `record_review_authority()`，分离开启时 manager 复核 42501。
  - 前向修复：`postgres/276_opt06_separation_permission_view.sql`（按分离策略重算 can_edit/can_review）、`postgres/277_opt06_separation_view_grant.sql`（GRANT SELECT）、`sqlite/191`/`192` 空操作。未改已应用 274/275。
  - 前向加固：`postgres/278_opt06_manager_self_review_grant.sql`、`sqlite/193_opt06_manager_self_review_grant.sql`：分离开启时禁止 owner/admin 的 reviewer 授权（DB 42501）。`app/accounting/directory.py` 同步返回 403。
  - `app/accounting/review_policy.py`、`review_policy_models.py`、`access.py`、`app/accounting/router.py`。
  - `src/review-policy.ts`、`src/ReviewPolicyPanel.tsx`。
  - `tests/test_opt06_review_control.py`、`tests/test_opt06_review_control_postgres.py`。
- 验收命令与输出：
  ```
  cd nexus_backend && PYTHONPATH='..;.' pytest -q tests/test_opt06_review_control.py tests/test_review_policy_service.py
  21 passed in 40.65s
  ```
  ```
  python scripts/test_accounting_postgres_isolated.py --config tmp_test/fsc02-pg/env17/unified.json nexus_backend/tests/test_opt06_review_control_postgres.py -q
  2 passed in 1.19s   # artifact 0c85e5e372444ee6b0036138c9a2f830
  ```
  ```
  python scripts/test_accounting_postgres_isolated.py --config tmp_test/fsc02-pg/env16/unified.json nexus_backend/tests/test_opt06_review_control_postgres.py -q
  2 passed in 1.16s   # artifact 03edbf14ee2b48ff98eefb11c4dfddc4
  ```
  ```
  tmp_test/opt02-ruff-venv/Scripts/ruff.exe check <4 files>  -> All checks passed
  ruff.exe format --check nexus_backend/tests/test_opt06_review_control_postgres.py -> 1 file already formatted
  npm run typecheck -> exit 0
  ```
- 行为：
  - 开启分离要求每账套有非管理员复核人；缺口 409 并列出账套。
  - manager 保留 read/manage，不再自动 edit/review/export；显式 book editor 仍可 edit；manager 永不 review。
  - 前端显示“管理与复核分离”警示；配置仍走 API/MFA/理由/expected_version。
  - 分离开启后，管理员给 owner/admin 授予 reviewer（含自授）在应用层 403、DB 层 42501；非管理员 reviewer 仍可复核。
- 未做或未验证：
  - `record_review_authority()` 的 manager 分支未单独以合法 draft/manual 链复现；直接伪造会被 `rr_manual_insert_fn` 先以“balanced payload”拒绝。当前 PG 证据覆盖分离感知视图与迁移；应用层覆盖 manager review 403。
  - 真实 MFA/管理员设备、关闭策略双人批准、真实客户。
- 顺带发现：修改已应用 275 会触发 checksum；已恢复并新增 276/277 前向迁移。SQL 迁移前必须用受控账号。
- 未提交/推送/部署。
### OPT06 候选更新（2026-11-17，含 B）

- HEAD：`767f7cdf7c717c98d3516a0e31b53bd8cd9ae1a9`
- source：`2329 / b98a351a29dfaf55662374af607f396b91236dcf8a11e93e36f19f66e578e8fe`
- migration：`457 / b3d82bd26848ba8a2ee47068edbbd585e0cdf7096cc0800a57e6401ff9886781`
- 说明：含 OPT06-A/B，以及管理员自授复核的前向加固；工作区未提交。

## OPT06-C  <状态：完成>

- 现状核实：无 delegation/substitute 机制；`AccessControl.book` 只看有效账套权限；PG `book_review_allowed()` 只认 reviewer/manager，因此替补复核会被应用层与 DB 触发器同时拒绝。
- 失败测试：
  - 新增 `tests/test_opt06_review_delegation.py`：生效期内财务可复核经理草稿、到期 403、替补不能复核自己制的单、发起人自批 403、使用/到期/撤销审计。
  - 修改前红：SQLite 触发器报 `Current independent reviewer and policy required`，因 `service_book_user_permissions.can_review` 不认 delegation。
- 修改文件：
  - `migrations/sqlite/200_opt06_review_delegations.sql`：`book_review_delegations` 表；重建 `service_book_user_permissions` 仅在 active delegation 窗口内增加 can_review，不增加 edit/export/manage。
  - `migrations/postgres/285_opt06_review_delegations.sql`：同表、RLS/FORCE/GRANT；替换 `accounting.book_review_allowed()`，仅非 manager 且当前窗口内 allow。
  - `app/accounting/review_delegations.py`：申请/批准/撤销/到期服务；发起人与批准人不同，>31 天拒绝，目标不得是 owner/admin 或本人。
  - `app/accounting/access.py`：active delegation 只增 read/review；分离策略开启时不生效。
  - `app/accounting/service.py::_approve_in_transaction`：使用替补时写 `review.delegation.use` 审计。
  - `app/accounting/review_delegation_router.py`、`app/main.py`：API 注册。
  - `tests/test_opt06_review_delegation_postgres.py`：PG 生效/到期 window。
- 验收命令与输出：
  ```
  cd nexus_backend && PYTHONPATH='..;.' pytest -q tests/test_opt06_review_delegation.py
  4 passed in 10.71s
  ```
  ```
  cd nexus_backend && PYTHONPATH='..;.' pytest -q tests/test_opt06_review_delegation.py tests/test_opt06_review_control.py tests/test_review_policy_service.py
  25 passed in 50.41s
  ```
  ```
  python scripts/test_accounting_postgres_isolated.py --config tmp_test/fsc02-pg/env17/unified.json nexus_backend/tests/test_opt06_review_delegation_postgres.py -q
  1 passed in 0.87s   # artifact d61dca3e2a7b4448a4515abc0c04463a
  ```
  ```
  python scripts/test_accounting_postgres_isolated.py --config tmp_test/fsc02-pg/env16/unified.json nexus_backend/tests/test_opt06_review_delegation_postgres.py -q
  1 passed in 0.88s   # artifact 8dbed57c5ee84c3b9db42a6d9a6d4aeb
  ```
  ```
  tmp_test/opt02-ruff-venv/Scripts/ruff.exe check <6 files>  -> All checks passed!
  ruff format --check <6 files> -> 6 files already formatted
  git diff --check -> clean
  ```
- 行为：
  - 替补授权有起止时间、账套/职责、发起人、批准人；发起人≠批准人，最长 31 天。
  - 每次权限判断按当前时间计算；到期后自动失效，`expire` 只做状态与审计，不承担授权判断。
  - 替补人只获得 read/review；不能编辑、导出或管理；分离开启时 manager 不能借替补绕过。
  - 替补人复核自己制的单仍被独立复核规则 403。
- 未做或未验证：前端申请/批准界面；真实请假/审批流程；定时 expire 任务的实际部署调度（权限不依赖它）；真实客户。
- 顺带发现：SQLite 200 首版重复追加 view 定义，已删除重复段后重跑；PG 285 未改历史迁移。
- 未提交/推送/部署。

### OPT06 候选更新（2026-11-17，含 C）

- HEAD：`557c4fa1aa7c9aa2f2179fadcbcd5ee1ffd2c77d`
- source：`2366 / 9b48bd000f88e7773dd018e07814f5245edfdf71c6e2fa7223cc92080f96d104`
- migration：`471 / ba300e1d75eb5630659fba15d3398d341e39d324149e6c0f3be30fd92c3ad439`
- 说明：含 OPT06-C；工作区仅 9 项未提交。HEAD 557c 是他人已完成的 FSC01–17/OPT 提交，本轮未提交。


### OPT06-D 凭证断号巡检（2026-11-17，部分完成）

- 现状核实：月结雷达原先没有凭证编号连续性检查；`vouchers` 只有追加和断号数据可能来自历史导入/人工补录，雷达无法阻断。
- 失败测试：
  - `tests/test_opt06_voucher_gaps.py` 构造 1、3 号凭证（仅 disposable SQLite fixture 临时移除 insert 触发器以模拟历史断号），`scan/watch` 前无对应 blocker。
  - `tests/test_opt06_voucher_gaps_postgres.py` 首版直接用 raw psycopg 连接，`?` 占位符报 `the query has 0 placeholders but 1 parameters were passed`，属测试连接适配错误；改为 `Database.connect()` 后通过。
- 修改文件：
  - `app/posting/integrity.py`：`VoucherGapService.scan/scan_in_transaction`；按类型聚合 count/max，SQLite 递归 CTE、PG `generate_series` 找缺口；不修改凭证、不重排编号。
  - `app/observability/closing.py`：雷达每账套扫描，追加 `voucher_number_gap` blocker；`precheck=blocked`、`closable=false`，下一步要求人工核对。
  - `migrations/sqlite/201_opt03_audit_hash_init_guard.sql` 与 `migrations/postgres/286_...`：修复 OPT03-B 回归。SQLite 原 append-only 守卫会与“插入后一次性写 hash”的哈希链触发器冲突，导致 `material_request_actions` 的审计插入报 `audit_events are append-only`；新迁移只放宽一次性初始化：内容字段不可变、prev_hash 必须等于链尾、row_hash 只能由 NULL 变非 NULL，二次更新仍拒绝。PG 侧 no-op 对齐编号。
  - `tests/test_opt06_voucher_gaps.py`、`tests/test_opt06_voucher_gaps_postgres.py`。
- 验收命令与输出：
  ```
  cd nexus_backend && PYTHONPATH='..;.' ../.venv/Scripts/python.exe -m pytest -q tests/test_opt06_voucher_gaps.py
  2 passed in 8.11s
  ```
  ```
  python scripts/test_accounting_postgres_isolated.py --config tmp_test/fsc02-pg/env17/unified.json tests/test_opt06_voucher_gaps_postgres.py -q
  1 passed in 0.30s   # bc9ef385f0c747acbe88e1f5e56f73fd
  ```
  ```
  python scripts/test_accounting_postgres_isolated.py --config tmp_test/fsc02-pg/env16/unified.json tests/test_opt06_voucher_gaps_postgres.py -q
  1 passed in 0.34s   # 7135e4730e454457b7cab96bde85377b
  ```
  ```
  pytest -q tests/test_opt06_voucher_gaps.py tests/test_opt03_audit_immutability.py tests/test_opt03_audit_chain.py
  7 passed, 3 skipped in 9.84s
  ```
  ```
  pytest -q -k 'close or posting'
  5 failed, 172 passed, 89 skipped, 6087 deselected in 298.82s
  ```
- 未通过/未验证：
  - `-k close or posting` 的 5 个失败不是断号用例；审计哈希初始化回归已由 201/286 修复（`test_stage25_service_plans.py` 目标用例通过）。剩余失败为旧 reviewer 授权测试（2）、P02 缺隔离 runner（1）、OCR/案例夹具（2）。
  - 迁移数应为 473（471+201/286）；source 已变化。
  - 未跑全量 backend；未跑 OPT06-D 的浏览器/真实历史断号。
  - 因上述失败，OPT06-D 只记“定向用例/两库 SQL 分支通过”，不记完成。
- 未提交/推送/部署。

### OPT06 候选复核（2026-11-17，含 D 改动）

- HEAD：`557c4fa1aa7c9aa2f2179fadcbcd5ee1ffd2c77d`
- source：`2371 / 85f5760e92f11a8a0691997fefb703055263a93b3a523279f17282fe4998d8e4`
- migration：`473 / c97932c91ce8bcbe219e6910479e7f342d6cc6d3127b3fdfa425f0731697f4bc`
- 说明：工作区 16 项未提交（本模块新增/修改 + 201/286）；未提交、未推送、未部署。


### OPT07-A 硬编码科目盘点（2026-11-17，只读完成）

- 现状核实：`rg` 扫描 `nexus_backend/app` 中作为字面量出现的四位/六位科目代码。未改代码，未改迁移。
- 盘点表：

| 文件:位置 | 字面量 | 用途分类 | 说明 |
| --- | --- | --- | --- |
| `reports/profit.py:19-26` | `6001/6051/6111`、`6401/6402`、`6403`、`6601`、`6602`、`6603`、`6711/6301`、`6801` | 报表取数 | 利润表行次科目前缀 |
| `reports/service.py:140` | `1122/2202/2203/2204` | 报表取数 | 往来项目/勾稽辅助判断 |
| `reports/service.py:187` | `1001/1002` | 报表取数 | 货币资金分类 |
| `accounting/service.py:35-44` | `CHART` 10 个科目 | 种子数据 | 小企业默认科目表 |
| `accounting/service.py:49-53` | `RULES` 房租/办公/交通/通信/收入 | 业务规则 | 关键词到科目映射 |
| `accounting/service.py:188-190` | `6001` 方向判断 | 业务规则 | 科目方向/余额符号 |
| `accounting/service.py:440-441` | `1002` 前缀 | 对账口径 | 银行流水借贷合计 |
| `accounting/service.py:1047` | `1002` | 业务规则 | 银行分录默认科目 |
| `accounting/queries.py:48-49` | `1002` 前缀 | 对账口径 | 总账银行发生额 |
| `settlements/service.py:20-21` | `2202/2241`、`1122/1221` | 对账口径 | 应付/应收结算范围 |
| `settlements/service.py:650` | `1002` | 业务规则 | 结算银行科目 |
| `monthly/briefing_source.py:167-169` | `1122/1221`、`2202/2203`、`2221` | 报表取数 | 月度简报应收/应付/税额 |
| `monthly/briefing.py:167-172` | `1122/1221`、`2202/2203` | 报表取数 | 简报风险分类 |
| `subledger/reconciliation.py:143,232,289` | `1001/1002` | 对账口径 | 现金/银行子账范围 |
| `subledger/service.py:120,740` | `1122/1123/2202/2203` | 对账口径 | 往来子账范围 |
| `subledger/service.py:954` | `1001/1002` | 业务规则 | 银行凭证校验 |
| `observability/insights_extra.py:172,177,258,263` | `1122/1221`、`2202/2241` | 报表取数 | 经营看板应收/应付 |
| `tax/leases.py:50-54` | `1122/2203/6001/1002` | 业务规则 | 租赁默认科目（可被请求覆盖） |
| `business/service.py:26-29` | `6602.01-.04` | 业务规则 | 费用类别默认科目 |
| `business/service.py:923-929` | `1002/6001/2202/2241` | 业务规则 | 手工业务分录默认科目 |
| `legacy_migration/service.py:1700-1702,1755-1779` | `1002/2001/6602.02` | 种子/迁移样例 | 合成迁移样例，非生产业务规则 |
| `legacy_migration/settlements.py:296` | `1002` | 对账口径 | 旧账迁移银行科目校验 |
| `intelligence/service.py:1392-1393` | `1002` | 对账口径 | 银行发生额核对 |
| `integrations/service.py:380` | `1002` | 业务规则 | 集成样例银行科目 |

- 结论：真正需要 OPT07-D 配置化的是“业务规则/对账口径”约 15 处；`CHART`/`RULES` 属种子数据，保留。
- 未改任何代码；未提交/推送/部署。
Open objectives: OPT07-B 报表模板数据化；OPT07-C/D/E/F（m40767）。

### OPT07-B 报表模板数据化（2026-11-17，部分完成）

- 现状核实：`ReportService._ensure_template` 固定写死 `small_enterprise_basic`；`definition_json` 只是描述性字符串，行顺序/分组仍由 Python 固定。`report_runs` 已保存 `template_id/template_version`，但没有按企业 `standard_kind` 选模板，也没有缺失模板/非法定义的明确拒绝。
- 失败测试：
  - `test_report_requires_template_for_company_standard`：将活动 `accounting_policies.standard_kind` 改为 `enterprise` 后生成报表，修改前会静默套用小企业模板。
  - `test_report_rejects_invalid_template_definition`：将 `report_templates.definition_json` 改为缺少利润表行定义，修改前不会拒绝。
- 修改：
  - `app/reports/service.py`：新增 `TEMPLATES`、`_standard_kind`、`_template_for_book`、`_validate_definition`。
  - 按 `accounting_policies.standard_kind` 选模板；无对应模板 → 409“账套适用的报表模板未配置：enterprise”。
  - 模板 `definition_json.sections.balance_sheet/profit_statement` 驱动报表行分组、顺序、标签；值仍由现有科目活动计算，未新增任意表达式/脚本。
  - 非法定义（非 CNY、无 sections、空行、未知科目分组、缺名称）→ 422。
  - `_build_in_transaction` 接收模板；指纹纳入模板 id/version/定义指纹；`report_runs` 写模板 id/version。
  - 保留 `small_enterprise` 原有输出：资产负债/利润表金额与现有测试一致。
- 验收命令与输出：
  - `cd nexus_backend && PYTHONPATH='..;.' ../.venv/Scripts/python.exe -m pytest -q tests/test_b04_pilot.py -k 'template or report_is_stale' --maxfail=1`
    - `3 passed, 2 deselected in 10.87s`
  - `... pytest -q tests/test_b04_pilot.py tests/test_b04_api.py --maxfail=1`
    - `7 passed, 1 warning in 23.95s`
- 未做或未验证：模板配置 UI、`enterprise` 正式模板、每行公式完全数据化（当前仅行分组/顺序/标签由模板驱动，值计算仍是固定通用函数）、模板变更对已发布报表的历史冻结仅靠 `report_runs.template_version` + 内容快照保证，尚未做跨模板回放。因此本模块只记“部分完成”，不宣称模板引擎已完全数据化。
- 顺带发现：`ReportService.TEMPLATE_ID/TEMPLATE_VERSION` 常量仍保留供兼容引用，实际路径已按企业标准选择模板。
Open objectives: OPT07-C/D/E/F；OPT08–OPT10（m40767）。
- 候选（OPT07-B 后）：HEAD `557c4fa1aa7c9aa2f2179fadcbcd5ee1ffd2c77d`；source **2371 / `5552f1790eb99759449ceb93ddac01ae4432071c71582c6286d40d1c5fc89a68`**；migration **473 / `c97932c91ce8bcbe219e6910479e7f342d6cc6d3127b3fdfa425f0731697f4bc`**。
- 未提交/推送/部署。

### OPT07-C 企业会计准则报表模板（2026-11-17，部分完成）

- 现状核实：OPT07-B 已按 `standard_kind` 选择模板；尚无 `enterprise` 模板；`standard_kind='enterprise'` 会被明确 409。
- 失败测试：`test_report_requires_template_for_company_standard`（修改前静默用小企业模板）、`test_enterprise_template_generates_balanced_report`（先红，因无 enterprise 模板）。
- 修改：
  - `app/reports/service.py` 增加 `enterprise_general` v1 合成模板，标题注明“合成模板，未经客户与会计师确认，仅供离线验收”。
  - 资产负债表/利润表使用同一通用分组/行标签驱动；未编造不确定行次的取数公式。
  - `_template_for_book` 区分“无模板”与“模板被停用”：停用 → 409“报表模板未启用：enterprise”。
  - 原缺失模板测试改为插入 inactive 企业模板，验证不回退小企业模板。
- 验收命令与输出：
  - `pytest -q tests/test_b04_pilot.py -k 'enterprise or template'` → **3 passed/10.63s**。
  - `pytest -q tests/test_b04_pilot.py -k 'template or report_is_stale'` → **3 passed/11.03s**。
  - `ruff check` 通过。
- 未做或未验证：企业会计准则科目表种子；现金流量表企业模板；每个有取数规则行次的专项用例；企业账套 PG16/17；“待专业确认”清单尚未逐行展开（仅保留模板头注）。
- 因此 C 记“部分完成”，不宣称企业准则报表已完整覆盖。
- 候选（C 后）：HEAD `557c4fa1aa7c9aa2f2179fadcbcd5ee1ffd2c77d`；source **2371 / `e6e8e8ccd2c971af58826da1458af042b0b1bb0fcf5372c30d009914d06019f2`**；migration **473 / `c97932c91ce8bcbe219e6910479e7f342d6cc6d3127b3fdfa425f0731697f4bc`**。
- 未提交/推送/部署。

#### OPT07-C 补充：可选企业科目种子（2026-11-17）

- 新增 `nexus_backend/app/opening/enterprise_chart.py`：19 个合成企业会计准则科目（资产/负债/权益/收入/费用），`enterprise_chart()` 返回独立副本，作为可选初始化数据，不改现有小企业科目表。
- 新测试 `test_enterprise_chart_seed_is_optional_and_synthetic` 覆盖代码集合、借贷方向、副本隔离；`pytest -q tests/test_b04_pilot.py -k enterprise` → **2 passed/6.60s**。
- 未接入开户初始化，也未用该科目表实际建账；C 仍为部分完成。
- 候选更新：HEAD `557c4fa1aa7c9aa2f2179fadcbcd5ee1ffd2c77d`；source **2372 / `2a63dde0a8f720bc26b6d23fb4ef4b48cafb59e902204dda4f06dcfa033fb6a5`**；migration **473 / `c97932c91ce8bcbe219e6910479e7f342d6cc6d3127b3fdfa425f0731697f4bc`**。
- 复核（2026-11-17）：`pytest -q tests/test_b04_pilot.py -k 'enterprise or template'` → **4 passed,3 deselected/10.40s**；`ruff check app/opening/enterprise_chart.py app/reports/service.py tests/test_b04_pilot.py` 通过；新文件 `ruff format --check` 通过。C 仍为部分完成。

### OPT07-D 科目角色配置化（2026-11-17，部分完成）

- 现状核实：总账银行发生额、结算应付/应收范围、结算银行科目均按 `1002/2202/2241/1122/1221` 硬编码；报表/月度简报/子账等其它口径尚未迁移。
- 失败测试：自定义应付科目 3202 登记 `payable` 后，`AccountRoleService.codes` 仍只返回默认科目；`open_item` 进一步被 `settlement_items.clearing_account` CHECK 阻断。
- 修改：
  - `sqlite/202_opt07_account_roles.sql`、`postgres/287_opt07_account_roles.sql`：`account_roles` + 默认回填。
  - `app/accounting/account_roles.py`、`create_book` 种子、`queries.py` 银行 CTE、`settlements/service.py` 的 allowed/bank code 改读角色；空结果才回退旧常量。
  - `test_opt07_account_roles.py` 验证自定义角色解析。
- 验收：
  - `tests/test_opt06_review_delegation.py tests/test_stage20_access_reads.py tests/test_opt07_account_roles.py` → **18 passed**。
  - `tests/test_stage10_settlements.py::test_full_allocated_bank_clears_reviewed_payable_then_exports_without_repeating_expense` → **1 passed**。
- 未完成/阻断：`settlement_items.clearing_account` 仍有硬编码 CHECK（sqlite 031 / pg 045），PG `settlement_clearing_account()` 仍硬编码；自定义应付代码无法端到端过账。需新增前向迁移重建约束/函数，未做。D 只记部分完成。
- 候选（D 部分完成）：HEAD `557c4fa1aa7c9aa2f2179fadcbcd5ee1ffd2c77d`；source **2376 / `062432a951169e145debae58eafc76b488e97940202234fba1c98253b7b1ef67`**；migration **475 / `904a548e462dde788fe3f2b0aef37230f50fb1b0d7f818582f7fb84246a57d54`**。
- 未提交/推送/部署。

### OPT07-D 历史阻断记录（已被下方完成段取代）

- 失败证据：自定义应付科目 3202 经 `AccountRoleService` 识别，但 `open_item` 插入 `settlement_items` 时被硬编码 CHECK 拒绝：
  `CHECK constraint failed: clearing_account IN ('2202','2241','1122','1221')`。
- 该约束位于历史迁移 `031_settlements.sql` / `045_settlements.sql`；PG `accounting.settlement_clearing_account()` 同样硬编码。
- 按 OPT00 边界，不修改已应用迁移；修复必须新增前向迁移重建 SQLite 表/放宽约束，并在 PG 替换 CHECK 与函数。该改动会影响结算主链，当前未完成，不能以只改 Python 冒充通过。
- 决定：OPT07-D 标记“部分完成/阻断”，不宣布通过；按总令跳到不依赖该约束的 OPT07-E。后续恢复 D 时先做前向迁移与 `3202` 端到端红测。
- 候选：source `062432a951169e145debae58eafc76b488e97940202234fba1c98253b7b1ef67`；migration `904a548e462dde788fe3f2b0aef37230f50fb1b0d7f818582f7fb84246a57d54`。


### OPT07-D 科目角色配置化（2026-11-17，完成）

- 现状核实：银行发生额 CTE、结算应付/应收 allowed、结算银行科目均硬编码 `1002/2202/2241/1122/1221`；结算表 CHECK 和 PG 函数也硬编码。
- 失败测试：自定义 3202 设为 payable 后，业务草稿仍写 2202/2241；`open_item` 被 `CHECK constraint failed: clearing_account IN (...)` 拒绝。
- 修改：
  - `sqlite/202_opt07_account_roles.sql`、`postgres/287_opt07_account_roles.sql`：`account_roles(book_id, code, role)`，默认角色回填。
  - `sqlite/203_opt07_settlement_roles.sql`、`postgres/288_opt07_settlement_roles.sql`：前向重建/放宽 `settlement_items` 的 hardcoded CHECK；PG `settlement_clearing_account()` 改读 `account_roles`。
  - `AccountRoleService`；`create_book` 种子；银行 CTE、结算 allowed/bank code 读角色；业务未付款草稿优先默认应付、仅一个自定义应付时用自定义代码。
- 验收命令与输出：
  - `pytest -q tests/test_opt07_account_roles.py` → **2 passed/8.03s**。
  - `pytest -q tests/test_opt07_account_roles.py tests/test_stage10_settlements.py tests/test_opt06_review_delegation.py tests/test_stage20_access_reads.py` → **27 passed/46.56s**。
  - `pytest -q tests/test_stage10_settlements.py` → **10 passed/23.89s**。
  - PG17 自定义应付 **1 passed/3.03s** `a661fd69120449cc8ea4c6cb0ad91f70`；PG16 **1 passed/3.05s** `747139153aad47599ae90df4d1696f1e`。
  - PG17 结算全量 **5 passed/11.97s** `adf43088b3fd453190fc12d8c62bff77`；PG16 **5 passed/12.30s** `e5eb760a3819450591e24ad15b31c503`。
  - `ruff check` 通过；新测试已格式化。既有 CRLF 文件未整文件重排。
- 未做或未验证：带既有 allocation 数据的 203 升级路径；`-k "close or posting"` 仍有 5 个旧失败（旧 reviewer 授权、P02 runner、OCR/案例夹具），与 D 无关。
- 候选：HEAD `557c4fa1aa7c9aa2f2179fadcbcd5ee1ffd2c77d`；source **2378 / `e0aacc1c6dd45201e5dde5562f21b7dc03684d6e7f8d2d7c31dd96984a377056`**；migration **477 / `c90fe378fcf5b12ee4a0c4d7254f93fef5109ad3589c0b2eec5c971ad49df36e`**。
- 未提交/推送/部署。

## OPT07-D  科目角色配置化  <状态：完成>

- 现状核实：结算 `settlement_items.clearing_account` 与 PG `settlement_clearing_account()` 硬编码 2202/2241/1122/1221；业务未付款草稿、银行 CTE、结算 allowed 同样假定固定代码。
- 失败测试：`test_custom_payable_role_is_used_by_business_and_settlement` 在 3202 设为 payable 后，`open_item` 触发 `CHECK constraint failed: clearing_account IN ('2202','2241','1122','1221')`；PG 首次运行还暴露 `COALESCE(boolean,0)` 类型错误。
- 修改文件：
  - 新增 `nexus_backend/migrations/sqlite/202_opt07_account_roles.sql`、`postgres/287_opt07_account_roles.sql`：`account_roles` 与默认角色回填。
  - 新增 `nexus_backend/migrations/sqlite/203_opt07_settlement_roles.sql`、`postgres/288_opt07_settlement_roles.sql`：放宽结算 CHECK，PG 函数改读角色。
  - 新增 `nexus_backend/app/accounting/account_roles.py`；`create_book`、`queries.py`、`settlements/service.py`、`business/service.py` 改读角色。
  - 修复 `access.py` 的 PG boolean `COALESCE`。
  - 新增/扩展 `tests/test_opt07_account_roles.py`、`tests/test_stage10_settlements_postgres.py`。
- 验收命令与输出：
  - `pytest -q tests/test_opt07_account_roles.py` → `2 passed in 8.14s`
  - `pytest -q tests/test_opt07_account_roles.py tests/test_stage10_settlements.py tests/test_opt06_review_delegation.py tests/test_stage20_access_reads.py` → `27 passed in 46.56s`
  - PG17 自定义应付 → `1 passed/3.03s`，artifact `a661fd69120449cc8ea4c6cb0ad91f70`
  - PG16 自定义应付 → `1 passed/3.05s`，artifact `747139153aad47599ae90df4d1696f1e`
  - PG17 结算全量 → `5 passed/11.97s`，artifact `adf43088b3fd453190fc12d8c62bff77`
  - PG16 结算全量 → `5 passed/12.30s`，artifact `e5eb760a3819450591e24ad15b31c503`
  - `ruff check` 通过。
- 未做或未验证：带既有 allocation 数据的 203 升级路径；`pytest -k "close or posting"` 仍有 5 个既有失败（旧 reviewer 授权 2、P02 runner 1、OCR/案例夹具 2），与 D 无关。
- 候选：HEAD `557c4fa1aa7c9aa2f2179fadcbcd5ee1ffd2c77d`；source `e0aacc1c6dd45201e5dde5562f21b7dc03684d6e7f8d2d7c31dd96984a377056`；migration `c90fe378fcf5b12ee4a0c4d7254f93fef5109ad3589c0b2eec5c971ad49df36e`。

## OPT07-E 税费政策条目化  <状态：部分完成>

- 现状核实：`tax_profiles` 只有单一 `vat_rate_bp/surtax_rate_bp`；`TaxWorkpaperService._snapshot` 用销项净额乘单一税率，不按票面税率/条目分项。现有 `_invoice_rows` 已有逐票 `amount_cents/tax_cents`，可用于后续多税率归集。
- 已新增：
  - `nexus_backend/migrations/sqlite/204_opt07_tax_policy_items.sql`、`postgres/289_opt07_tax_policy_items.sql`：`tax_policy_items`（profile 下 kind/code/name/basis_kind/rate_bp/effective_from/effective_to/conditions/authority/approved_by/created_by/created_at/fingerprint），PG RLS/FORCE/GRANT。
  - `nexus_backend/app/tax/policy_items.py`：`TaxPolicyItemService.list/add/calculate`；`calculate` 按 `basis_kind` 匹配生效条目，复用 OPT01-C 的 `scale_by_basis_points`，缺条目抛 409。
  - API：`GET/POST /api/tax/books/{book_id}/policy-items`、`POST .../policy-items/calculate`；`main.py` 注册 `application.state.tax_policy_items`。
  - `nexus_backend/tests/test_opt07_tax_policy_items.py`：6%/13% 两档销项、文化事业建设费、农产品扣除，合计 2210 分；缺 `missing_basis` 409；复核后新增条目不改写旧底稿且旧版本导出 409。
- 验收命令与输出：
  - `pytest -q tests/test_opt07_tax_policy_items.py` → **3 passed/9.51s**（含 workpaper 条目集成与版本冻结负向）。
  - `pytest -q tests/test_tax_workpaper.py tests/test_n04_tax_contract.py tests/test_opt07_tax_policy_items.py` → **24 passed/42.75s**，无 policy 时旧 API 形状不变。
  - `pytest -q tests/test_tax_workpaper_scope_sqlite.py tests/test_tax_workpaper_guards.py tests/test_tax_api.py` → **19 passed/35.41s**。
  - PG 工作底稿回归（含迁移 289）：PG17 **32 passed/16.36s** `tmp_test/ak05-pg-runs/1f049ebcd6bd44d7a6af1f289677412c`；PG16 **32 passed/16.60s** `tmp_test/ak05-pg-runs/ec92fc4f99764f3daba744711e8d375c`。
  - `ruff check` 通过；`workpaper.py` 已格式化。
- `TaxWorkpaperService._snapshot` 已读取条目：`_policy_snapshot` 按 `basis_kind` 匹配生效条目，缺基础/条目 409；`vat_cents=vat_output−agriculture_deduction`；无 policy 保持旧单税率路径。
- 未完成或未验证：
  - 多税率按发票/业务分类归集、附加税分项、企业所得税预缴、印花税模型未实现；API 条目集成测试未加；前端未接。
  - 两企业/停用条目对评审的负向测试未覆盖；版本冻结与导出 409 已覆盖。
- 候选：HEAD `557c4fa1aa7c9aa2f2179fadcbcd5ee1ffd2c77d`；source **2382 / `cd05573e5e3df22947963dea8fd072fdda67b93adb8021c714bf470272521b0a`**；migration **479 / `e31665124832171eed717a9c9b463e10151556a62643de39d578eb45845f3a41`**。
- 未提交/推送/部署。


## OPT07-F 通用主体别名  <状态：后端本地通过，前端待做（整体部分完成）>

- 现状核实：`transport_aliases` 只有 firm/company/subject_id/alias/source/status/created_at，没有主体类型和生效期；出租车解析只认旧表。
- 已新增：
  - `nexus_backend/migrations/sqlite/205_opt07_subject_aliases.sql`、`postgres/290_opt07_subject_aliases.sql`：`subject_aliases`（firm/company/subject_type/subject_id/alias/source/status/effective_from/effective_to/created_by/created_at/停用信息），唯一 `(firm,company,type,alias,effective_from)`；回填旧出租车别名；PG RLS/FORCE/GRANT。
  - `nexus_backend/app/aliases.py`：`AliasService.add/retire/resolve`；同企业同类型有效期重叠的别名只能指向一个主体，冲突 409；解析返回 resolved/ambiguous/not_found。
  - `nexus_backend/app/ledgers/service.py::_transport_subject` 改为只查 `subject_aliases`，按发生日期解析，不再回退旧表。
  - `nexus_backend/app/alias_router.py`：`GET /api/companies/{id}/aliases/resolve`、`POST /api/companies/{id}/aliases`、`POST .../{alias_id}/retire`；`main.py` 注册 `application.state.aliases`。
  - `nexus_backend/tests/test_opt07_alias_http.py`：未命中返回 not_found、缺主体 404；**1 passed/7.99s**。
  - `nexus_backend/migrations/sqlite/206_opt07_generic_alias_types.sql`、`postgres/291_opt07_generic_alias_types.sql`：前向放开 `subject_type` 到 `transport_subject`/`counterparty`；SQLite 重建表，PG 动态替换旧 CHECK；不改已应用 205/290。
  - `nexus_backend/app/aliases.py`：`counterparty` 主体必须存在、`active=1` 且同企业；解析时过滤已停用客商；其余类型仍 422。
  - `nexus_backend/tests/test_opt07_subject_aliases.py`：客商别名新增/解析、同名指向第二客商 409、停用后 not_found；冲突 409、生效期解析、失效后 not_found、手工脏数据触发 ambiguous；`AliasService` 写入 `alias.create`/`alias.retire` 审计。
  - `pytest -q tests/test_opt07_subject_aliases.py` → **4 passed/10.36s**；`test_opt07_subject_aliases.py + test_tax03_transport.py` → **10 passed/21.48s**（旧表回退移除时）。
- 验收命令与输出：
  - `pytest -q tests/test_opt07_subject_aliases.py` → **3 passed/8.83s**。
  - `pytest -q tests/test_tax03_transport.py tests/test_opt07_subject_aliases.py` → **10 passed/21.48s**。
  - PG16/17 `test_opt07_subject_aliases_postgres.py` 各 **1 passed**：同 firm/company/type/alias/生效日唯一，跨生效日与跨 company 允许，`counterparty` 行可写入。
  - 修复 `postgres/290` 回填：`substr(timestamptz)` 需 `created_at::text`；测试用 savepoint 隔离预期唯一冲突。
  - `pytest -q tests/test_opt07_subject_aliases.py` → **4 passed/10.36s**；PG17 `1 passed/0.40s`、PG16 `1 passed/0.43s`。
  - `pytest -q tests/test_opt07_subject_aliases.py tests/test_tax03_transport.py -k 'not http'` → **10 passed/18.33s，1 deselected**；被排除的是 `test_http_transport_overview_and_subject_creation`，本机 Starlette 0.35 `TestClient(client=...)` 不兼容，不是产品断言失败，也不算通过。
  - `pytest -q tests/test_opt07_alias_http.py` → **1 passed/7.85s**（未命中/缺主体 HTTP 契约，未覆盖客商正例）。
- 未完成或未验证：
  - 前端未提供客商别名维护/解析入口；
  - 未做两企业/并发的服务层 PG 负向；`counterparty` 只验证 schema 与服务，未接真实导入消费者；
  - 已移除出租车解析的旧表回退；新增别名 `effective_from=0001-01-01`，历史名称对既有业务日期可解析。
- 候选：HEAD `557c4fa1aa7c9aa2f2179fadcbcd5ee1ffd2c77d`；source **2391 / `aee9fbbcfcba4b510ebee20cded0f57b0049edc4f30beccc814360fab8222d64`**；migration **483 / `cc997bbc2eeffd73b7994ce73e58df900333fdf1ad078c010e6f7fe44154fc46`**。
- 未提交/推送/部署。


## OPT08 金额、表格与输入  <状态：本地验收通过；跨浏览器/人工 IME 未验>

结论：A–E 的本地实现与验收已完成；Firefox/WebKit 与真实输入法/屏幕阅读器不构成本地代码阻断，但未验证，不得写成全浏览器通过。

- A 金额精确化：新增 `src/money.ts`（`parseYuanToCents`、`formatCents`、`sumCents`、`centsToApiString`、`balanceDirection`），以 bigint 分为单位；拒绝指数、超两位小数、前导零和超出上限。`csv.ts`、`formal-books-types.ts` 委托该模块，已替换 JournalEntryGrid、TaxFilingRecords、TaxCostBatchPanel、TaxFeeBatchPanel、SettlementDetail、OaSnapshotPanel、AnalysisEvidencePanel、ClientAccessTools、AssetReconciliation、tax-filings、TaxFPanel。`FirmObservability`/`value-baseline` 仍为 micro 元成本（非分），`transport-limits` 为 MB，未强行替换。
- B MoneyCell：新增 `src/MoneyCell.tsx`/`src/money-cell.css`，统一等宽右对齐、负数语义色与方向列；FormalBooksWorkbench 增加“方向”，期末余额显示绝对值；JournalEntryGrid 合计、InlineReviewDrawer 借贷/差额接入。
- C 科目选择：新增 `src/account-picker.ts`、`src/AccountPicker.tsx`/CSS。末级科目按父前缀识别，支持代码/名称过滤、父级禁用分组、ArrowUp/Down、Enter、Escape；菜单固定定位避免被表格裁剪，已替换 JournalEntryGrid 原生 select。
- D 键盘：JournalEntryGrid 支持 Alt+Enter 新增行、Ctrl+Enter 保存（排除 IME）；删除行后聚焦相邻行；工具栏显示快捷键提示。`InlineReviewDrawer` 与 stage7 实际使用的 `VoucherReviewer` 均已接线。提交前仍使用既有 dirty/摘要/金额错误/busy 阻断，不新增绕过复核的入口。
- E 验收：`e2e/stage7.spec.ts` **3 passed/24.9s**，覆盖打开候选、按 6401 过滤、选择 6401.02、Alt+Enter 新增、删除回焦、Ctrl+Enter 保存；桌面截图 `tmp_test/opt08-account-picker.png`。同一用例在 390×844 下重新打开候选，断言菜单横向不越界，截图 `tmp_test/opt08-account-picker-mobile.png`。`e2e/tax05-large-voucher.spec.ts` **6 passed/37.0s**，516 行分页断言本页借 ¥2,500.00、全量借 ¥25,800.00。
- 离线门禁：`npm test -- --run` **154 files / 1416 passed**；`npm run lint` 0 error（4 条既有 unused eslint-disable warning）；`npm run format:check` 通过；`npm run build` 通过，initial JS **567.43 KB / gzip 175.87 KB**，CSS **99.95 KB / gzip 20.32 KB**。首次 build 因系统临时目录 `esbuild` 文件删除被 Windows 拒绝而失败，改用仓库内 `TMP/TEMP` 后通过，不是产品构建缺陷。
- 未完成或未验证：
  - 跨浏览器 Firefox/WebKit、真实 IME/输入法、屏幕阅读器人工验证未做；手机 390 已由 Chromium 覆盖。
  - 生产构建 chunk 仍 >500 KB 警告，未在本模块内做代码分割。
- 候选：HEAD `7cce49a57543f8ec3a02c68114eb1500b308eb12`；source **2399 / `1f48e709861409682a3eb7ba6a0099d2ebeb98dc190a6a3e4374cb83ee15ab07`**；migration **483 / `cc997bbc2eeffd73b7994ce73e58df900333fdf1ad078c010e6f7fe44154fc46`**。
- 候选包含其他任务在 `7cce49a` 的提交与 AccountPicker/MoneyCell 接线；本模块另有未提交的 `src/account-picker.css`、`src/journal-grid.css` 两个文件（均只改本模块样式）。未由本任务提交/推送/部署。

## OPT09 复核队列风险分档、年度矩阵与防误操作  <状态：本地验收通过；跨浏览器/顶栏切换未专项>

结论：A–E 的本地实现与浏览器验收完成。真实年度数据规模、Firefox/WebKit、顶栏企业切换的迟到响应未专项验证。

- A 风险分档：新增 `app/accounting/review_risk.py`（`review-risk-v1`）。任一硬性 finding/关期/自审→high；非 bank 来源无 finding→medium；bank 无 finding→low。原因去空去重。
- A 队列：`review.py::list_drafts` 每行按当前库内事实重算 `risk`、`risk_reasons`、`risk_rule_version`、`risk_legacy`、`issue_count`；筛选支持三档及旧值；`risk_scope="deterministic_projection_v1_full_checks_on_preview_and_confirmation"`。
- B 前端：`ReviewWorkbench` 改为高密度表格（企业/账套、风险与首因、摘要/来源、金额、查看证据），sticky 表头、40px 行高、三档筛选、三档摘要与规则版本；移动端三列堆叠。
- C 年度矩阵：`workbench.py::annual(actor, year)` 按授权账套返回每企业×12 月 `stage/label/next/blockers/owner/book_count/books`；缺账套为 `no_book`（不按 0/已完成处理）；同企业同月多账套折叠并保留明细；路由 `GET /api/monthly/annual?year=`。
- C 前端：`AnnualMatrixPanel` + `annual-matrix.css`，企业列/表头 sticky，1280 下 12 月完整可见，390 横向滚动；点格回月度清单并设期间；企业行可展开各账套。
- D 防串数据：e2e 验证切换企业筛选后原高风险行消失、切回后恢复；切换风险筛选后“已选 1 张”归零为“已选 0 张”。
- E 防误操作：确认区 `role=note` 明确“确认后不能在此直接撤销，只能更正/冲销，原审核记录保留”；e2e 预览后断言该提示。
- 验收：
  - `test_opt09_review_risk.py` **5 passed/0.31s**；`test_opt09_review_risk.py + test_batch_review.py` **31 passed/24.71s**。
  - `test_opt09_annual_matrix.py` **2 passed/7.87s**。
  - `e2e/opt09-annual-matrix.spec.ts` **1 passed/13.9s**（5 企业×12 月、12 月右边界在桌面容器内、移动端可横向滚动、点格回清单）。
  - `e2e/opt09-review-queue.spec.ts` **1 passed/15.7s**（高/低风险、原因、URL 筛选、企业/风险切换清空选择、不可逆提示）。
  - 截图：`tmp_test/opt09-review-risk-1280.png`、`-390.png`；`tmp_test/opt09-annual-matrix-1280.png`、`-390.png`。
- 未完成或未验证：跨浏览器/真实数据规模未验；顶栏切换企业的迟到响应由全局会话机制覆盖，未在本模块专项 e2e；确认提交后的不可逆流程未制造真实审核记录。

## OPT10 安全准入  <状态：本地大部分通过；PG16 实际还原受客户端版本限制/多进程限流未验>

结论：加密备份（SQLite/PG16/PG17 旧格式兼容）、敏感字段脱敏与经理按需揭示审计、日志/错误不泄露、单进程限流已实现并通过定向测试；PG17 已执行真实“备份→删除 schema→产品 `restore`→行数/迁移/表数比对”，PG16 加密备份与 verify 通过但产品 restore 被 PG17 客户端写入 PG17-only `transaction_timeout` 阻断；多进程共享计数未验证。

- A 备份加密：`scripts/backup_accounting.py` 新增 `nxbk-gcm-v1`/AES-256-GCM，magic+12B nonce+密文+16B tag，1MiB 流式；密钥来自 `ACCOUNTING_BACKUP_KEY`+`ACCOUNTING_BACKUP_KEY_ID`，生产未配置拒绝；清单只记 key id，同时记明文/密文摘要；verify 解密核明文，restore 先验密文再解密、失败清理；旧未加密备份兼容。
- A 测试 `test_opt10_backup_encryption.py` **4 passed/9.15s**；旧恢复回归 `test_stage10_backup_recovery.py + test_c01_restore_safety.py + test_restore_hold.py` **24 passed/14.70s**。
- A PG 加密备份：`test_opt10_backup_encryption_postgres.py` 在 PG16/PG17 各 **1 passed**（1.52s/1.43s）；使用本机既有 `pg_dump`，验证 `database.dump` 为 `NXBK1` 密文、无 `PGDMP` 明文头、清单仅含 key id、`verify` 解密成功、错密钥失败。
- A PG17 实际恢复：`scripts/opt10_pg_restore_drill.py` 备份→`DROP SCHEMA accounting CASCADE`→产品 `restore()`→比对，前后均为 `companies 498 / migrations 278 / max_version 291 / tables 316`，回执 7 个文件，`serving_enabled=false`、`outbound_disabled=true`；`tmp_test/opt10-pg-restore-drill17/result.json`。
- A PG16 实际恢复受阻：本机仅有 PG17 `pg_restore`，dump 中 `SET transaction_timeout` 在 PG16 报 `unrecognized configuration parameter`；用 PG17 将 dump 转 SQL、删除该 GUC 后 `psql` 恢复，恢复后 `391 companies / 278 migrations / 316 tables`。这是工作区恢复，不冒充产品 `restore()` 通过；PG16 加密备份/verify 已通过。
- B 脱敏：`mask_bank_account` 尾4、`mask_tax_id` 首4+尾4、`mask_phone`、`mask_id_card`、`redact_sensitive`；`AdminService._company` 脱敏税号，`import_wizard` 脱敏银行账号；`reveal_sensitive` 仅 firm manager，返回完整税号并写 `security.sensitive.reveal` 审计；`GET /api/admin/companies/{id}/sensitive`。
- B 测试 `test_opt10_sensitive_redaction.py` **3 passed/6.05s**；`test_admin_workspace.py` **13 passed/13.02s**。全库检索确认业务接口未暴露 phone/mobile/id_card/account_no。
- B 前端：`AdminWorkspace` 企业详情显示脱敏税号，新增“显示完整税号/隐藏完整税号”，切换企业清空本地完整值；`e2e/opt10-admin-sensitive.spec.ts` 在 OPT09/OPT10 套件中 **3 passed/18.8s**（含两条 OPT09），断言列表脱敏、显式揭示后完整、隐藏恢复，截图 `tmp_test/opt10-admin-sensitive-1280.png`、`tmp_test/opt10-admin-sensitive-revealed-1280.png`。
- B 日志/错误：`test_qa_http_failures.py` 由 Starlette 0.35 不兼容的 `TestClient(client=...)` 改为不传 `client`（断言未变），**8 passed/19.28s**，覆盖 500 不泄露 `SECRET`、写请求 `result_unknown`、no-store、request id 安全；字段级加密未做。
- C 限流：`app/security/rate_limit.py` token bucket，读/写/高成本默认 6000/1200/600（环境变量覆盖，非法值拒绝）；429+Retry-After；健康/公开配置/静态豁免；身份为 token hash 或 client IP，仅 `ACCOUNTING_TRUST_PROXY=1` 信任 XFF；`main.py` 接入，`SCOPE="single_process"`。
- C 测试 `test_opt10_rate_limit.py` **4 passed/0.48s**；新增真实应用链路 `test_opt10_rate_limit_app.py` **1 passed/7.31s**：环境限制 2 次/分，`/api/session` 两次 200，第三次 429 + 整数 `Retry-After` + `detail=请求过于频繁，请稍后重试`；OPT10 四文件联合 **24 passed/17.63s**；加 OPT09 七文件 **57 passed/39.09s**。
- 未完成或未验证：多进程共享计数（当前明确 `SCOPE=single_process`）、PG16 经产品 `restore()` 的实际还原（本机缺少 PG16 客户端）、跨浏览器。PG17 已做实际恢复演练；PG16 只完成加密备份/verify 与工作区 SQL 恢复，不得写成产品恢复通过。
- 候选（OPT08 基线）：HEAD `7cce49a…`；source 2399/`1f48e709…`；migration 483/`cc997bbc…`。OPT09/OPT10 改动后的候选将在最终联合验收重算。

## OPT09/OPT10 联合门禁与当前候选

- `npm test -- --run`：**154 files / 1416 passed**。
- `npm run lint`：0 error，4 条既有 unused eslint-disable warning。
- `npm run format:check`：通过。
- `npm run build`（`TMP/TEMP=tmp_test/build-tmp`）：通过；initial JS **567.43 KB / gzip 175.85 KB**，CSS **99.95 KB / gzip 20.32 KB**，仍 >500KB chunk warning。
- `e2e/opt09-review-queue.spec.ts`：**1 passed/15.7s**；`e2e/opt09-annual-matrix.spec.ts`：**1 passed/13.9s**。
- 当前候选：HEAD `7cce49a57543f8ec3a02c68114eb1500b308eb12`；source **2415 / `c256fd547596d845e50df7daca06b5e28d8cbfa909bf3299c0d5443cd09655a6`**；migration **483 / `cc997bbc2eeffd73b7994ce73e58df900333fdf1ad078c010e6f7fe44154fc46`**。新增 `test_opt10_backup_encryption_postgres.py`、`test_opt10_rate_limit_app.py`、`e2e/opt10-admin-sensitive.spec.ts`、`scripts/opt10_pg_restore_drill.py`；OPT09 两条 + OPT10 敏感揭示 e2e 在当前候选 **3 passed/18.8s**。
- HEAD `7cce49a` 含其他任务提交与 AccountPicker/MoneyCell 接线；本任务未提交/推送/部署。
- 整体状态：OPT01–OPT08 本地通过/部分；OPT09 本地通过；OPT10 大部分通过（PG17 实际恢复通过、主应用真实 429 通过，PG16 产品恢复受本机客户端版本限制；多进程限流、跨浏览器未验）。

## 已知回归（本轮未修复，不能算全后端通过）

- `cd nexus_backend && PYTHONPATH=.. python -m pytest tests -q -k "tax"` → **10 failed / 233 passed / 87 skipped / 6060 deselected / 355.76s**。
- 4 个为本机 Starlette 0.35 不接受 `TestClient(client=...)`：`test_http_pilot_boundary_contract`、`test_http_transport_overview_and_subject_creation`、`test_tax_api.py::test_http_profile_workpaper_review_and_csv`、`test_http_readiness_blocks_before_estimating`；属环境不兼容，不得算产品失败，也不得算通过。
- 6 个待判定/修复：`test_tax_f_recovery` 的 `approved_version` 期望 0 实际 1；`test_taxi_ad_acceptance_repair` 缺外部映射；`test_taxi_ad_backend_continuity` 费用草稿 `counterparty` 写名称而非 party id；`test_taxi_ad_recovery` 恢复夹具与关系重叠约束冲突；`test_taxi_source_input_limits` 两个用例（畸形行绕过行数上限、boolean 被当 version）未抛 `AccountingError`。
- 本轮未修改这些断言或夹具以换取全绿；整体仍不能称“全后端通过”。
