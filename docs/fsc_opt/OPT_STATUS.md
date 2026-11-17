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
