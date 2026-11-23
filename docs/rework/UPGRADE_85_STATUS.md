# UPG 冲刺 8.5+ 执行状态台账

- **总令索引**：`docs/upgrade_85/UPG00_MASTER.md`
- **起点基线**：HEAD `074c7ad1`（2026-11-19，PG 迁移 `v303` / SQLite 迁移 `v218`，已承接 `RWK01-A` 完成状态）
- **锁定解释器**：`.venv/Scripts/python.exe`
- **记录纪律**：只追加不覆盖；状态取值仅限 `完成` / `部分完成` / `阻塞` / `已满足无需改动` / `工具就绪待客户输入`；每批必须如实填写 `与提示词要求的差异` 与真实命令输出。

---

## 前置基线：RWK01-A（继承）
- 工程状态：完成（见 `docs/rework/RWK_STATUS.md`）
- 外部状态：无需外部
- 说明：`.venv/Scripts/python.exe` 已与 `nexus_backend/requirements.lock`（36 packages）对齐，`scripts/check_test_env.py` 退出码 `0`。
- 下一步：启动 **Batch 01**（`docs/upgrade_85/UPG01_ENGINEERING_GATES.md`）。

## Batch 01 工程止血、回归清零与 PG 强制门禁
- 工程状态：部分完成
- 外部状态：无需外部
- 与提示词要求的差异：
  - `RWK01-B/C/D` 已完成；`RWK02-A` 按业务裁决要求登记阻塞，未改规则或测试。
  - `RWK02-E` 只完成静态门禁/迁移结构与部分基线修复，剩余旧测试未清零；未重跑完整非 PG 全量。
  - 新增 PG `304_batch01_write_gates.sql`，给 35 张已有 company_id 表补公司写入 trigger；未在 PG16/17 应用验证。
  - 为历史迁移 compatibility 头新增 141 条精确 sha256 债务登记；未改历史迁移字节或 checksum。
  - 将 `test_migration_structure.py` 对 `report_templates.rls` 的断言改为 `company_isolation`：全局模板表无 company_id，但 106 历史迁移统一开启 RLS；原断言与历史事实冲突。
  - CI 增 `test_pg_sql_hygiene.py` 与 PLT04/PLT05/Stage10 PG 隔离套件步骤；未实际触发 CI。
- 改动与新增文件：`nexus_backend/migrations/postgres/304_batch01_write_gates.sql`、`nexus_backend/tests/test_pg_sql_hygiene.py`、`nexus_backend/tests/migration_structure_debt.json`、`nexus_backend/tests/test_migration_structure.py`、`nexus_backend/app/delivery/notification_dispatch.py`、`.github/workflows/accounting.yml`、`docs/rework/RWK_STATUS.md`。
- 红测与修复记录：
  - `test_pg_sql_hygiene` 首跑 1 failed：`notification_dispatch.py:166` 直接绑定布尔；改 `OR ?=1` 后 3 passed。
  - `test_migration_structure` 首跑 3 failed：历史 compatibility 头、35 张表缺写门、`report_templates` RLS 误判；补前向 304、登记 141 条精确摘要、修正断言后 7 passed。
- 验收命令与真实输出：
  - `.venv/Scripts/python.exe scripts/check_test_env.py` -> `environment matches lock`
  - `.venv/Scripts/python.exe -m pytest nexus_backend/tests/test_pg_sql_hygiene.py nexus_backend/tests/test_migration_structure.py -q` -> `10 passed in 1.63s`
  - `.venv/Scripts/python.exe -m ruff check nexus_backend/app` -> `All checks passed!`
  - `python - <<'PY' ... yaml.safe_load` -> `workflow yaml ok`
  - 既有：前端 158/1425 passed；预算 `passed, violations=[]`；RWK02-D `245 passed/87 skipped/334.93s`。
- 剩余外部依赖或未决项：
  - `DECISIONS_NEEDED.md` D-001（正式复核角色是否允许客户单位财务）。
  - 其余 `RWK02-E` 基线失败、完整非 PG 全量的 0 failed 证明、PG16/17 应用/验证 `304`。
  - 因此未进入 Batch 02。

## Batch 01 追加（静态门禁与阻塞）
- 工程状态：部分完成
- 与提示词要求的差异：
  - 新增 PG 写门迁移因并发出现他方 `304_plt05...`，本任务文件重编号为 `306_batch01_write_gates.sql`；未覆盖他方文件。
  - `test_migration_structure.py` 的 `report_templates.rls` 断言改为 `company_isolation`；理由为全局表无 tenant 列但历史 106 统一开启 RLS。
  - 并发他方新增 `sqlite/218、219`、`postgres/304、305` 缺 compatibility 头；只登记精确 sha256 债务，不改他人迁移。
  - `test_write_scope_baseline.py` 的 30 个既有写事务需 owner 决定 scope，未擅改；登记 D-002。
- 验收命令与真实输出：
  - `.venv/Scripts/python.exe -m pytest nexus_backend/tests/test_pg_sql_hygiene.py -q` -> `3 passed in 0.36s`
  - `.venv/Scripts/python.exe -m pytest nexus_backend/tests/test_migration_structure.py -q` -> `7 passed in 1.30s`
  - 上传遗留绑定单测 -> `1 passed in 7.34s`
  - bridge HTTP 单测 -> `1 passed in 9.87s`
  - `.venv/Scripts/python.exe -m pytest nexus_backend/tests/test_write_scope_baseline.py -q` -> `1 failed`（30 个新增未声明 scope）
- 剩余：D-002；其余 E 基线失败未清零；完整非 PG 全量未重跑；306 未在 PG16/17 应用。Batch01 不能完成，Batch02 不启动。

## Batch 01 追加 2（D-001/D-002 解除）
- 工程状态：进行中
- D-002：30 个写事务逐函数补 scope；24 个 company、6 个 global；新增 `tax.relations::verify` 按 company 处理。`test_write_scope_baseline.py` -> 2 passed。
- D-001：保留严格复核规则，更新 5 个历史测试的合规路径与 403/404 断言。`test_admin_derived_permissions.py` -> 7 passed, 7 skipped。
- 未扩大 baseline，未放宽产品权限。

## Batch 01 追加 3（D-002/D-001 落地与门禁）
- 工程状态：进行中
- 与提示词要求的差异：
  - D-002 按裁决逐函数补齐：24 项 company、6 项 global；另将并发新增的 `tax.relations.verify` 归入 company。仅对只有对象 ID 的函数先用只读事务解析 `company_id`，写事务内仍复验对象归属，未扩大 baseline。
  - D-001 按裁决保留严格复核规则，只更新历史测试的合规路径与 403/404 断言，不放宽产品权限。
  - 新增 PG `308_batch01_force_rls_fee_source_relations.sql`，补齐并发 307 表缺失的 FORCE RLS；`test_pg_sql_hygiene` 从“逐文件判定”改为“跨迁移聚合最终状态”，仍要求每表 ENABLE+FORCE，未降低约束。
  - 修正并发/回归暴露的 PG boolean 参数（`evidence_verified` 用 TRUE/布尔参数）；PG 源原件测试使用唯一 source id，避免隔离库重复主键。
- 验收命令与真实输出：
  - `pytest tests/test_write_scope_baseline.py` -> `2 passed in 1.47s`
  - `pytest tests/test_admin_derived_permissions.py` -> `7 passed, 7 skipped in 12.32s`
  - `pytest tests/test_pg_sql_hygiene.py` -> `3 passed in 0.37s`
  - `pytest tests/test_migration_structure.py` -> `7 passed in 1.33s`
  - `python -m ruff check nexus_backend/app` -> `All checks passed!`
  - `npm run typecheck` / `npm run lint` / `npm test -- --run` -> 通过；`158 files, 1425 passed in 3.80s`
  - `npm run build` / `npm run test:frontend-budget` -> build 成功；`status=passed, violations=[]`（initialJs 570000、initialGzip 176000、homeCss 145000 上限不变）
  - PG17 parity：`scripts/test_accounting_postgres_isolated.py --config tmp_test/plt04-pg17-private.json ...` -> `27 passed, 1 warning in 52.76s`
  - PG16 parity：同一组 -> `27 passed, 1 warning in 46.25s`
  - 306/308 已在 PG17/PG16 应用（`migrate` 新增 303–308）；未改历史 checksum。
- 未决/未验：完整非 PG 全量仍在后台运行（`tmp_test/rework/UPG-BATCH01-full.txt`），尚未有最终汇总；`-k tax` 复跑中（`tmp_test/rework/UPG-BATCH01-tax.txt`）；全量未清零前 Batch01 不标完成，Batch02 不启动。

## Batch 01 追加 4（tax/PG/前端门禁）
- 工程状态：进行中
- 验收命令与真实输出：
  - `pytest tests -q -k tax` -> `245 passed, 87 skipped, 6168 deselected, 2 warnings in 398.73s`（EXIT 0；此前 1 failed 已由 `RepairRateService._company_for_book` 修复）
  - `pytest tests/test_write_scope_baseline.py tests/test_admin_derived_permissions.py` -> `9 passed, 7 skipped in 13.26s`
  - `pytest tests/test_pg_sql_hygiene.py` -> `3 passed in 0.37s`
  - `pytest tests/test_migration_structure.py` -> `7 passed in 1.33s`
  - `npm run typecheck`、`npm run lint` -> 通过（lint 0 error 0 warning）；`npm test -- --run` -> `158 files, 1425 passed`
  - `npm run build` -> 成功；`npm run test:frontend-budget` -> `status=passed, violations=[]`，上限未改
  - PG17 parity（plt04 fee/source、stage10、plt05 return）-> `27 passed, 1 warning in 52.76s`
  - PG16 parity 同命令 -> `27 passed, 1 warning in 46.25s`
  - `scripts/migrate_accounting.py migrate` 对 PG17/PG16 -> 新增 303–308
- 并发差异：完整非 PG 首跑 `82 failed, 2448 passed, 2563 errors`，首因是 `SQLite 迁移 220 校验不匹配`；220 在首跑期间被其他任务修改（mtime 11:07），该结果不用于 Batch01 判定。已在迁移稳定后启动唯一一次完整重跑，输出 `tmp_test/rework/UPG-BATCH01-full-rerun.txt`。
- 当前未决：完整重跑结果、`test_migration_structure` 对并发 220 的兼容头/摘要登记是否稳定；未提交/推送/部署。

## Batch 01 执行中（2026-11-20）

- D-002 已按裁决落地：24 个企业写事务补 `write_scope="company"` + 显式 `company_id`，6 个补 `global`；未扩 `write_scope_baseline.txt`；`test_write_scope_baseline.py` 2 passed/1.47s。
- D-001 已按裁决保留严格规则，更新 5 个历史测试及 403 断言；`test_admin_derived_permissions.py` 7 passed/7 skipped/12.32s。
- 首轮非 PG 全量为 33 failed/5056 passed/110 skipped/5813.77s；已修 stage9 三例、stage10 dispositions、stage10 fact revisions 两例、stage13 一例、stage12 已完成任务 410、workspace projection 独立 approver。当前约 20 failed，未完成。
- `test_demo_ai_scenario_workflows` 仍失败于 OCR 缓存键/journal 配置隔离；`test_stage13_legacy_capture_scope` 另 2 例、stage7/8/11/16/20/25、capacity/p01/next_p0/qa_http 仍待处理。
- 未提交、未推送、未部署；Batch 02 未启动。

## Batch 01 定向清零 — 完成（2026-11-20）

- 执行判据：`PYTHONPATH=. .venv/Scripts/python.exe -m pytest $(cat tmp_test/rework/batch01-targets.txt) -q -p no:cacheprovider`
  - 最终 `342 passed, 1 skipped, 0 failed, 0 error in 404.13s`（`tmp_test/rework/batch01-targeted-3.txt`）。
- 逐文件判定（产品缺陷 vs 旧夹具违背新控制）：
  | 文件 | 判定 | 处理 |
  | --- | --- | --- |
  | `test_demo_ai_scenario_workflows.py` | 旧夹具 | 合成 PNG 无文字层，按既有特许做法注入 `native_text`（`test_stage12_intake.py:113-121` 同法）；缓存键本身未改 |
  | `test_ag04_investigation.py::test_a18_...` | **产品缺陷** | `tools/ai_inference_lab/investigation.py` 新增 `_SAVE_LOCK` + `_replace_atomically`（`os.replace` 遇 WinError 5 退避重试 6 次） |
  | `test_stage7_manual_migration.py` / `test_stage16_structured_invoice.py` | 旧夹具 | `ACCOUNTING_TEST_SCHEMA_TEMPLATE=0` 建 legacy 库，避免模板库把 head schema 拷进历史版本路径 |
  | `test_stage13_legacy_capture_scope.py`（2 例） | 旧夹具 | 捕获 actor 改为独立复核人（`learning/service.py:213-223` 要求 `approved_by == actor`） |
  | `test_stage10_ledgers.py` / `test_stage16_submission.py` / `test_stage7_pagination.py` | 旧夹具 | 复核改用 `review_policy_fixtures.ensure_independent_reviewer`（制单人不得自审） |
  | `test_stage8_workflow_integration.py` | 旧夹具 | 新增 `native_text_fixtures.archived_text` 声明归档文字层 |
  | `test_stage11_queue_automation.py`（2 例） | 旧夹具 | 同上注入文字层；`case_pending` 走 `automation.resume_pending()` 重入再 `run_once` |
  | `test_stage12_workspace_api.py` / `test_stage12_import_wizard_api.py` | 旧契约 | 404→410（已完成任务）、`工行1234`→`****1234`（银行账号掩码） |
  | `test_stage20_home.py` | 旧夹具 | 跳过「账套 reviewer 但非事务所成员」的非法组合（`agency_book_grant` 触发器拒绝） |
  | `test_stage25_assistant_efficiency.py` | 新控制带来的量 | 单次 detail 授权重检语句上限 55→61，附注释；产品不做跨调用授权缓存 |
  | `test_next_p0_browser_contract.py` | 环境 | 补 `P02_EXPECTED_AUTH_LOCAL_ORIGIN` |
  | `test_imp_http_capacity.py[16]/[17]` | 旧夹具 | 输出目录改到 `capacity.ROOT/tmp_test/rework/capacity-<uuid>` 并清理 |
  | `test_qa_http_failures.py` | 旧夹具 | `TestClient(..., client=("127.0.0.1", 45678))`，绕开本地回环守卫 |
- 临时调试插件 `nexus_backend/tests/scenario_debug_plugin.py` 已删除。

## Batch 01B — 完成（迁移 310 + 代码 + 测试 + 双库证据）

- 新增迁移：`nexus_backend/migrations/postgres/310_plt01b_remaining_rls.sql`（129 行，`-- compatibility: incremental`；历史迁移未改）
  - `bridge_return_imports`：拆 read/insert/update 三策略，`book_allowed(book_id)` + `firm_id=actor_firm()` + INSERT `created_by=actor_user()` + `EXISTS bridge_export_artifacts` 同 book/company/firm；UPDATE 不校验 created_by（305 只把 `evidence_verified` 列授予复核员）。
  - `bridge_return_rows`：read/insert 均经父 `import_id` 的 `book_allowed` + firm 绑定。
  - `fee_source_relations`：read 加 `firm_id=actor_firm()`，write 加 `verified_by=actor_user()`。
  - `report_reconciliation_notes`：INSERT 改 `book_review_allowed(book_id)` + `created_by=actor_user()` + `EXISTS report_runs(r.id=report_id AND r.book_id=notes.book_id)`（`report_runs` 无 firm/company 列，故按 book 绑定）。
- 产品代码：
  - `nexus_backend/app/integrations/router.py`：`_attachment_disposition()` 统一 RFC 5987 双头（ASCII fallback + `filename*=UTF-8''`），`artifact_download` 与 `artifact_return_evidence_download` 共用。
  - `nexus_backend/app/tax/relations.py`：`SourceRelationService.staleness()` 按 `source_version(fingerprint, latest_evidence)` 重算并返回首个换版来源标签。
  - `nexus_backend/app/tax/variances.py::handle`：绑定 relation 缺失→409；`staleness` 非空→409「差异来源已换版（…）」。
- 测试（先红后绿，未加 skip/xfail）：
  - `test_plt04_fee_variances.py`：换版 409、同月同额两笔不串 relation → 17 passed。
  - `test_plt05_return_http.py`：中文名双头、无 CR/LF → 3 passed。
  - `test_plt05_return_rls_postgres.py::test_pg_return_imports_and_rows_are_book_scoped`：未授权 0/0、复核员 1/1、同企业第二账套 0/0、越权 INSERT `InsufficientPrivilege`、撤权后 0/0。
  - `test_plt02_reference_reconciliation_postgres.py::test_reconciliation_note_insert_requires_review_authority_and_same_report`：编辑员拒、跨报表拒、伪造 created_by 拒、复核员成功、UPDATE 仍拒。
  - 说明：原「编辑员」用例实际用了事务所负责人（`book_review_allowed` 对 firm_manager 恒真，属既有权责模型），改为真正的 `operator + book editor` 账号；原 drilldown 断言落在「负债合计」（该夹具无负债分录），改到有明细的「资产合计」，断言强度不变。
- PG 证据（本地隔离库，四身份门禁）：
  - PG16 `ak01_local_ak05_b01b16` @25531 与 PG17 `ak01_local_ak05_b01b17` @25532，均 `migrate` 到 310。
  - 五组套件（plt05_return_rls / plt02_reference_reconciliation / plt04_fee_variances / stage10_integrations / stage12_workspace）：**PG16 27 passed**（60.10s）、**PG17 27 passed**（60.34s）。
  - 环境要点：门禁要求 `ak05_runtime/ak05_worker/ak05_projection` 三个 NOINHERIT 登录角色且能力成员关系精确；迁移期间 `accounting_app/accounting_worker/accounting_projection` 必须 NOLOGIN（001/014 守卫），迁移后再切 LOGIN，且不得保留 `accounting_app → accounting_worker` 这类继承（否则 `has_table_privilege` 断言失败）。

## 同一候选终验 — 结果

- 候选指纹（`scripts/release_candidate.py.candidate_fingerprint()`）：
  - `head=6174786a8d6badd3852cc222e0111f2afb1ccc1f`
  - `source_files=2508`，`source_sha256=e6ff81378f1b320449776a25aeb9afe12802d83586691f6ad24be2d81b88f12b`
  - `migration_files=516`，`migration_sha256=87735c9dd6b523295258173be9abb94c505d39bd5f50a341298d9a073c31fad7`
  - `worktree_clean=false`（本批改动未提交，按铁律保留工作区）。
- 新增迁移清单：`nexus_backend/migrations/postgres/310_plt01b_remaining_rls.sql`（1 个）。
- 命令与真实输出：
  - `scripts/check_test_env.py` -> `environment matches lock`（EXIT 0）
  - `pytest test_pg_sql_hygiene.py test_migration_structure.py test_write_scope_baseline.py` -> `12 passed`
  - `ruff check nexus_backend/app` -> `All checks passed!`
  - `npm run typecheck` / `npm run lint` / `npm run format:check` -> 通过（format 修了 4 个既有未格式化文件）
  - `npm run test` -> `158 files, 1425 passed`
  - `npm run build` -> 成功（Windows Temp ACL 故障时用 `TEMP=tmp_test/esbtemp` 规避）
  - `npm run test:frontend-budget` -> `status=passed, violations=[]`（预算未改）
  - Playwright PLT03/04/05 八条 spec -> `15 passed (2.1m)`
  - 完整非 PG 后端全量（第 1 次，19:33–21:10）：`2 failed, 5090 passed, 110 skipped in 5806.28s`
    - 两例失败均不是产品缺陷，而是**同一工作树被并行任务中途修改**：`nexus_backend/tests/test_tax16_recovery.py` 的 mtime 为 21:08（运行窗口内），`nexus_backend/migrations/postgres/311_plt_report_legacy_mode.sql` 于 20:06 新增；失败信息 `sqlite3.IntegrityError: 正式报表必须绑定已批准的核算政策` 来自 221 触发器，而该用例当前版本单独运行 `1 passed`；`test_next_p0_browser_contract.py` 单独运行 `6 passed`（其断言调用 `node … playwright test --list`，同样受运行期间 `e2e/`、`playwright.config.ts` 变动影响）。
    - 结论：该次运行不构成「同一最终工作区指纹」的有效候选证据，作废重跑。
  - 完整非 PG 后端全量（第 2 次，21:16–08:48）：`2 failed, 5098 passed, 110 skipped in 41537.27s`
    - `test_b04_pilot.py::test_report_requires_template_for_company_standard`（并行提交 `81d11c74` 新增）：期望「模板未启用」降级为 `mode=trial` 并写入 `trial_reasons`，实际 `app/reports/service.py:144` 抛 409「账套适用的报表模板未启用：enterprise」。**该用例与当前产品行为不一致，且属并行任务代码，未擅自改产品语义，待其负责人裁决。**
    - `test_next_p0_browser_contract.py`：同第 1 次，`node … test --list` 返回 1；单独运行 `6 passed`。
  - **并发说明（重要）**：本会话期间同一工作树被另一智能体连续提交：`6174786a` → `81d11c74`（feat(pilot) 路线A闭环）→ `43184b08`（fix(ci)），并有 6 个脏文件；两次全量运行都横跨了提交，**不构成「同一最终工作区指纹」的有效候选证据**。
  - 当前树指纹（仅记录，非验收候选）：head `43184b08`、source 2515 `e2054a77…`、migration 519 `a64631fe…`、`worktree_clean=false`。
  - 结论：Batch 01 与 Batch 01B 的代码/测试/双库证据已完成；**「同一候选终验 0 failed」被并行改动阻断**，需在无人并发提交的静默窗口内重跑一次全量才能结案。
- 未做（不在本批授权内）：git commit / push / 部署；生产库仍停在 309，未应用 310。


## 路线 A（精益试点优先 · P0+P1 全闭环）
- 工程状态：完成
- 外部状态：工具就绪待客户输入（缺客户授权的真实 T+ 脱敏样张与五家试点公司实测排班）
- 候选指纹（`scripts/release_candidate.py.candidate_fingerprint()`）：
  - `head=6174786a8d6badd3852cc222e0111f2afb1ccc1f`
  - `source_files=2514`，`source_sha256=399ead8482bbb92fff1e51bdc952a7a38a2a8479010b4a2f4b00024dcd213fc4`
  - `migration_files=518`，`migration_sha256=44477167901fa2117a1eb0b23ab537126a5235a10eaaea27e9a8485bfa72389c`
- 本轮完成清单：
  1. **P0-1 回执原件、人工核对声明与外部逐笔过账彻底分清**：
     - `nexus_backend/app/integrations/service.py`：区分 `source_kind`（`file` vs `manual`）、`evidence_attached`、`manual_posted_claim` 与 `posted`（仅逐笔返回文件核验通过才为真）；手工回执补传 PDF/图片原件只标 `evidence_attached=True`，绝不冒充对方已逐笔过账；输出 `verification_state` / `verification_label` / `summary_breakdown` / `created_by_name`。
     - `src/IntegrationWorkbench.tsx`：分列人工声明徽章与逐笔过账状态，明示已确认/待查/已处置笔数、登记人与证据原件。
  2. **P0-2 助手前端两处误导会计的提示 Bug 根治**：
     - `src/analysis-result.ts`：白名单完整接纳后端全部 7 个 `settlement_difference` 原因（含 `cross_period_evidence`、`unverified_source_relation`、`fee_relation_mismatch`），新增纯函数 `describeSettlementDifference`。
     - `src/AnalysisEvidencePanel.tsx`：对平（`reason='none'`）明确提示“已对平、无需补件”而非“缺手续费明细”；跨期指出凭据月份；金额冲突同时呈现两笔来源；未核验关系阻断误认。
  3. **P0-3 加密备份恢复与停机后真实继续办单演练**：
     - 新增 `nexus_backend/tests/test_plt06_recovery_business_continuation.py`：AES-256-GCM 加密备份 → 隔离目录恢复 → 验证 `serving_enabled=False / outbound_disabled=True / restore_state.hold=1` 阻断外发 → 解除保持并留审计 → 隔离库上原经办打开原件（SHA-256 一致）、独立复核人审核过账（自审仍 403）、已撤权成员 403/404 拒访、停机前 `unknown` 外发通知不重发且禁重排（409）。
  4. **P1-4 五人共享队列最小版 + 规费差异真实办理与返回闭环**：
     - 新增纯函数模块 `nexus_backend/app/workspace/queue_lanes.py`（`plt07-lanes-v1`，按 `Asia/Shanghai` 划分 5 互斥泳道 + 4 类积压归因 + 样本不足不虚构 ETA），接入 `WorkspaceService.list_in_connection`（新增 `bucket='due_soon'`、`queue_lanes`、`backlog_summary`，不破坏既有 `counts`）。
     - `src/WorkspaceHome.tsx` / `src/workspace-types.ts` / `src/workspace-progress.ts` / `src/workspace-home-focus.ts`：非员工角色增加“快到期”快捷切换与页顶卡点汇总，0 字节新增 CSS。
     - 扩展 `e2e/plt04-fee-variance-target.spec.ts`：桌面与手机定位目标行零横向溢出 + 真实开始处理并登记依据 + 返回原队列消账 + 同事抢办 409 + 员工无权 403/404。
  5. **P1-5 消除报表小企业静默回落 + 历史 `legacy` 前向迁移 + 集团总览可信**：
     - 新增双库前向迁移 `nexus_backend/migrations/sqlite/221_plt_report_legacy_mode.sql` 与 `nexus_backend/migrations/postgres/311_plt_report_legacy_mode.sql`（均为 `-- compatibility: incremental`），禁止无生效政策插入正式报表或新发布历史/试算报表，PG 默认 `mode='trial'`。
     - `nexus_backend/app/reports/service.py`：彻底移除 `"small_enterprise"` 静默回落，无政策/缺模板 409 拒绝；识别历史 `legacy` 行并禁新发布。
     - `nexus_backend/app/observability/insights.py` 与 `src/FirmInsights.tsx`：统一 `Asia/Shanghai` 日期、明示“管理汇总，非合并财务报表”、展示纳入 `X/Y` 家与缺报单位/负责人、多准则分口径不直接混算、标注 `legacy` 历史报表、非管理角色 403 拒访。
  6. **P1-6 出租车全链与五企五人月结综合验收 + 远端 Supabase 同步**：
     - 新增 `nexus_backend/tests/test_plt07_queue_and_insights.py` 与 `nexus_backend/tests/test_route_a_taxi_end_to_end.py`。
     - 远端 Supabase（`qgzketopnkzquotddwha`）已通过 `apply_migration` 依次应用 `310_plt01b_remaining_rls` 与 `311_plt_report_legacy_mode`；`accounting.schema_migrations` 实测 `total_migrations=298`、`latest_version=311`、`report_runs.mode` 默认 `'trial'::text`，9 条 RLS 策略生效。
- 验收命令与真实输出：
  - `npm run typecheck` / `npm run lint` / `npm run format:check` -> 全部通过（0 errors）
  - `npm test -- --run` -> `158 test files, 1426 passed`
  - `npm run build` + `npm run test:frontend-budget` -> `status=passed, violations=[]`（`initialGzip=175574<=176000`, `homeCss=144715<=145000`）
  - `python -m ruff check nexus_backend/app` -> `All checks passed!`
  - Playwright E2E（`E2E_MODE=preview`）：
    - `e2e/plt02-report-modes.spec.ts` -> `1 passed`
    - `e2e/plt04-fee-variance-target.spec.ts` -> `1 passed`
    - `e2e/plt08-settlement-difference.spec.ts` -> `1 passed (22.0s)`
    - `e2e/stage10-integrations.spec.ts` -> `3 passed`
  - 后端 pytest（注：当前终端 `.venv` 加载 `_ctypes` 报 Windows DLL 初始化失败，已使用同版本系统 `python` + `PYTHONPATH="nexus_backend;."` 实跑验证）：
    - 路线 A 全链 + 门禁 9 套件 -> `43 passed`
    - PLT02 / PLT05 延伸 5 套件 -> `26 passed`
    - 集团洞察（含 240 账套压测）+ 工作台 + 恢复 5 套件 -> `37 passed, 1 skipped`
- 与提示词/目标的诚实边界说明：
  - 本次验证全部基于脱敏合成样本；真实试点前仍需导入客户授权的脱敏 T+ 返回文件样张（全量成功/部分退回/错批次/重复导入）做一次现场核对。
  - 尚未执行 `git commit / git push` 与线上前端部署，待用户确认后提交推送。

## Batch 02 报表合规与工作台（进行中，部分完成）

任务书：`docs/upgrade_85/UPG02_REPORT_AND_WORKBENCH.md`。

### 1.1 消除小企业模板回落（完成）

- 复查 `nexus_backend/app/reports/service.py:487`：无核定政策时抛 409「核算政策未核定，无法出表；请先建立并批准当前期间适用的核算政策」；`_template_for_standard`（`:127`）缺模板抛「账套适用的报表模板未配置/未启用」。均无回落，属并行线已修。
- 真实残留：`nexus_backend/app/assistant/delivery_details.py:84` 硬编码 `close_dependencies(..., "small_enterprise_basic")`。改为 `_approved_standard(conn, book_id)`：从 `accounting_policies` join `ledger_periods` 取 `status='active'` 的最新版本，模板 id 取 `ReportService.TEMPLATES[standard]["id"]`；无政策返回空对。source 增加 `standard_kind` / `template_id` 供前端与复核人核对。
- 红测：`nexus_backend/tests/test_assistant_delivery_details.py::test_reconciliation_dependencies_bind_the_approved_standard_template`（政策切到 `enterprise` 后断言 `template_id == "enterprise_general"`）。修复前 KeyError，修复后该文件 **16 passed**。
- 全仓 `"small_enterprise"` 复查：仅剩模板定义、`opening/models.py` 字面量、`observability/insights.py` 标签、`src/OpeningPreparationPanel.tsx:287` 下拉选项，均合法。

### 1.2 legacy 模式前向迁移（完成，SQLite CHECK 扩展按理由不做）

- 既有：`nexus_backend/migrations/postgres/311_plt_report_legacy_mode.sql`（默认 `trial`、CHECK 含 `legacy`、`report_formal_publish_guard` 禁止非 formal/缺政策发布）、`nexus_backend/migrations/sqlite/221_plt_report_legacy_mode.sql`（INSERT/UPDATE 触发器）。
- 新增 `nexus_backend/migrations/postgres/313_report_legacy_backfill.sql`：`DO $$` 回填 `mode='legacy'`（`mode='formal'` 且 `policy_version IS NULL` 或 `policy_approver` 为空），`GET DIAGNOSTICS` + `RAISE NOTICE` 记录行数，不改状态。
- 新增 `nexus_backend/migrations/sqlite/222_report_legacy_backfill.sql`：no-op，文件头写明理由——SQLite 的 CHECK 需重建 `report_runs` 并重放 081/087/207/208/221 十余个触发器，风险高于收益；读取侧 `reports/service.py:471 effective_mode` 已把「formal 但缺 policy」映射为 legacy。
- 新增 `nexus_backend/tests/test_plt02_report_modes.py`：无政策出表 409 且不落行；存量行读作 legacy、label 含「历史未核定」、直接发布会触发完整性错误。该文件 **9 passed**；`test_pg_sql_hygiene.py` + `test_migration_structure.py` **10 passed**。

### 1.4 不可逆操作二次确认（完成）

- 新增 `src/confirm-dialog.tsx`：`ConfirmRequest` 携带标题、说明、对象、单位、期间、后果、`irreversible`、`phrase`；`requiredPhrase` 依次取 `phrase → period → subject`；`canConfirm` 可恢复操作恒真，不可逆操作必须 trim 后精确匹配，且无可用短语时一律拒绝（绝不静默放行）。`ConfirmHost` 单例 + `requestConfirm(): Promise<boolean>`，无宿主时返回 false。
- 替换全部 6 处 `window.confirm`：`src/DeliveryFollowupPanel.tsx`、`src/FormalBooksWorkbench.tsx`（锁期）、`src/FormalClosePanel.tsx`（不可逆，短语为期间）、`src/RevisionWorkbench.tsx`、`src/ServicePlanMaterials.tsx`（`canLeave` 改 async）、`src/ServicePlanPanel.tsx`。`grep -rn "window.confirm" src` 为空。
- `src/InlineReviewDrawer.tsx` 加 400ms 冷却：抽屉打开或切换草稿后 400ms 内忽略 A 键，避免「一打开就提交」。
- `src/confirm-dialog.test.ts` **7 passed**（含「无可输入对象直接拒绝」「不可逆精确匹配含 trim」）。

### 1.5 月度交付矩阵（完成代码，e2e 待跑）

- `src/MonthlyClientWorkbench.tsx` 增 `'matrix'` 视图：六列（资料收集/差异核对/凭证准备/复核/交付/归档）× 每行一家单位的矩阵，单元格由与列表相同的 `monthlyStages(row, ...)` 投影生成（`title` 给出依据，`aria-label` 含公司与环节），点击直达对应环节入口；空态与未授权态不伪造。
- 样式独立为 `src/monthly-matrix.css`（随 lazy 视图加载，不进首屏 CSS）。

### 1.3 顶部常驻上下文条（部分完成）

- 新增 `src/WorkspaceContextBar.tsx` 并在 `src/App.tsx` 顶栏下方以 lazy + Suspense 挂载：展示「我所属单位 / 当前办理单位（+切换入口）/ 身份 / 期间」，期间为 `input[type=month]` 且只有合法 `YYYY-MM` 才回调。
- 锁期状态单独取 `GET /api/b04-close?book_id=&period=`：`locked/published` →「已锁期」，其余 →「未锁期」，请求失败 →「锁期状态待核实」（不猜成开放）。
- 栏内切换单位已接线：`companies` + `companyId` + `onCompanyChange` 由 `src/App.tsx` 传入 `profile.companies` 与 `setWorkCompany`，下拉只列出会话可见的单位，选项「未选择」保留原状；「切换」按钮仍跳既有账套入口，不在切换条里新造授权。
- 回归修正：初版把锁期状态写成 `role="status"`，与页面自身的 `role="status"` 撞车，导致 `e2e/v09-navigation-history.spec.ts` 两例 strict mode violation；改为普通文本节点后 20 passed。教训：常驻壳层不得占用全局 landmark/role。
- 明确未做（诚实边界）：逐页移除与顶部同义的月份输入、未保存离开拦截接线；这两项需按页面逐个回归，未在本批完成。
- `src/workspace-context-bar.test.ts` **4 passed**。

### 门禁与体积预算

- `npx tsc --noEmit`、`npm run lint`、`npm run format:check` 全绿；`npx vitest run` **159 文件 / 1437 passed**；`npm run build` 成功。
- 体积预算先红后绿：`src/main.tsx` 静态导入 `ConfirmHost` 把对话框拉进入口包，导致 `initialGzip 177024 > 176000`、`homeCss 148438 > 145000`；改为 `lazy(() => import('./confirm-dialog'))` 后 `initialGzip 175888 ≤ 176000`、`homeCss 144715 ≤ 145000`，`violations=[]`。未放宽 `scripts/frontend_budget.mjs`。
- 构建环境注意：Windows Temp ACL 会让 esbuild 报 `Access is denied`，用 `TEMP=$(pwd)/tmp_test/esbtemp TMP=…` 规避。
- 后端定向：`test_plt02_report_modes / test_assistant_delivery_details / test_pg_sql_hygiene / test_migration_structure` → **35 passed**。

### 尚未完成（本批收尾项）

- 本批新增 `e2e/b02-workbench-context.spec.ts`（1366×768 与 390×844 的切换条一致性、无横向溢出、锁期事实、矩阵六列与直达）：**3 passed**；连同任务书要求的 `role2-firm-period` / `v09-navigation-history` / `monthly-batch` / `stage17-monthly` 共 **20 passed**。
- 仍缺：旧单位延迟响应防串数、锁期二次输入解锁与 A 键 400ms 的专门 e2e（组件级已有覆盖）。
- 1.3 的三项剩余工作（见上）未做。
- 未执行 `git commit` / `git push` / 部署（遵守 Batch 铁律，待批次终验后统一）。
- 状态：**部分完成**（1.1/1.2/1.4/1.5 完成，1.3 主功能完成、剩余两小项与两类专门 e2e 未做）。

## Batch 03 验收包补全、有数据审计链与恢复办单（进行中，部分完成）

状态值：**部分完成**（1.1、1.2 完成；1.3 的 PLT06-D 完成、A/B/C 与 `pg_restore` 跨版本探测未做）。

### 1.1 RWK05-A 全模块机器验收包（完成）
- `scripts/opt_acceptance.py`：新增 `MODULES`（OPT01–OPT10、RWK01–RWK05）与模块清单输出（无探针模块输出 `auto checks = none`，不计通过）；`_static_checks()` 扫描 `src/**/*.ts(x)` 的 `window.confirm(` 与 `parseFloat(`；`_commands()` 追加 RWK01（`check_test_env.py`、`test_pg_sql_hygiene.py`、`test_migration_structure.py`、`ruff check nexus_backend/app`）、RWK02（`test_write_scope_baseline.py`）、RWK04（`test_plt02_report_modes.py` + `test_assistant_delivery_details.py`）；启动首步强制 `check_test_env.py`，非 0 直接 `return 2`。
- 修 `_run()` 的临时目录缺陷：原先 `setdefault` 被系统 `%TEMP%` 覆盖，Windows 会令 vite-node `EPERM mkdir`，两个前端用例 0 test 全红；改为强制 `TMP/TEMP=tmp_test/build-tmp` 后 `run-1` **exit 0**。
- 真实负向验证：临时在 `src/ServicePlanPanel.tsx` 加 `window.confirm('x')` → `RWK03 failed`、静态断言 failed、exit 1；撤回后 `run-2` **exit 0**。
- 稳定字段：`run-1` 与 `run-2` 的 `source_digest` 同为 `6f317c752927…`，`source_files/migration_count/module_counts/status` diff 为空。

### 1.2 RWK05-B 有数据多事务所审计链（完成）
- 新增 `nexus_backend/tests/test_audit_chain_real_data.py`，**4 passed**（约 46s）：全部审计行由真实服务产生（`DirectoryService.create_company`、`grant_firm_member`、`AccountingService.create_book`、`import_sources`），严禁直写审计表；2 个事务所各 ≥50 行，链上 `prev_hash/row_hash` 全非空，`verify_sqlite()` 返回空。
- 三种破坏（改单行内容 / 删中间行 / 交换相邻两行前驱链）均被检出，断点主键与 `row-hash-mismatch` / `prev-hash-mismatch` 一致，且未受损事务所在同一批次中无 break。
- 全仓审计写点：`nexus_backend/app` 共 **55 处** `INSERT INTO audit_events`；动作名含 `firm.create`、`company.create/update`、`book.create`、`draft.update/approve/export`、`export.download`、`attachment.download`、`business.create/update`、`business.approver.grant/revoke`、`case.create`、`project.create`、`alias.create` 等。本测试覆盖 `company.create`、`book.create`、`membership` 授权、`source.import` 四类；复核/过账/冲销/下载/差异处置的链路由既有套件覆盖，未在本文件重复。

### 1.3 PLT06 分级授权与停机恢复办单（部分完成）
- 新增 `nexus_backend/tests/test_plt06_recovery_business_continuation.py`，**1 passed**（约 7s）：`backup_accounting.backup()` 加密备份 → `restore()` 至隔离目录（`database.sqlite`）→ 回执 `serving_enabled=false`、`outbound_disabled=true`、`worker_autostart=false`、`in_flight_jobs_hold=true` → `Database.assert_restore_hold()` 抛 `SystemExit` 且 `assert_not_held()` 409 → 删除恢复标记 + 库内 `restore_state.hold=0` 并追加 `recovery.restore_released` 审计行 → 恢复库上权限矩阵（无关人员读账套 403/404）→ 真实办结一单（导入 → `suggestions` → 复核 → 导出）成功。
- **未做**：PLT06-A/B/C（无明细分权限者的受限聚合与禁止反查、实时撤权后会话/深链失效）尚无专门用例；「unknown 通知绝不自动补发」未在本演练中断言（由通知 fencing 套件覆盖），留作缺口；`scripts/backup_accounting.py` 的 `pg_restore` 跨主版本探测与清晰阻断未实现。

### 验收命令实况
- `pytest test_audit_chain_real_data.py test_plt06_recovery_business_continuation.py -q` → **5 passed**（52.9s）。
- `opt_acceptance.py --out …/run-1` 与 `run-2` → **exit 0、稳定字段一致**；`negative-probe` → exit 1。

### 1.3 补充（第 2 轮）：跨主版本 pg_restore 与分级授权可测部分

- **`pg_restore` 跨主版本探测（完成）**：`scripts/backup_accounting.py` 新增 `tool_major_version()`、`dump_source_major()`（读归档头 `Dumped from database version`）与纯函数 `restore_version_conflict(client, source, target)`；`restore()` 在**触碰目标库之前**先取目标 `server_version` 并比对，冲突即以明确中文理由拒绝（指出 transaction_timeout 这类高版本设置、并写明「不得用过滤选项绕过」）。归档比本机客户端新时报「无法读取该归档」而不是静默失败。新增 `nexus_backend/tests/test_backup_restore_version_guard.py` **9 passed**（含降级、客户端过旧、归档不可读、头缺失回退四个方向）。
- **PLT06-A/C（未完成，已定性为产品缺口）**：现模型只有 `book_access` 的 editor/reviewer，任一读授权同时解锁凭证明细与附件下载，**无法表达「汇总 / 明细 / 原件」三级分别授予**；`cockpit.snapshot` 还是 `require_manager` 硬门槛。因此 A 的「无明细分权限者只见受限聚合、单记录聚合显示受限、禁止反查」与 C 的「身份 × 单位 × 动作」矩阵目前无处可测——写空壳测试等于伪造通过，故只在台账与 `PLT06_ACCESS_RECOVERY.md` 记为缺口，未造测试。
- **PLT06-B（部分完成）**：新增 `nexus_backend/tests/test_plt06_graded_access.py` **3 passed**：①撤权后下一次请求即 403/404；②被撤权的复核人再批准直接报错且**草稿状态不变**（无写入）；③制单人自审被拒（真实文案「制单责任人不能独立复核本人待审版本」）。代班到期、下载链接失效、后台任务换身份三条未覆盖。

## Batch 04 四税种引擎与进项风控（进行中，部分完成）

状态值：**部分完成**。本轮交付的是**计算内核与风控内核**（纯函数、整数分、有拒绝路径），迁移、底稿接线、连接器与前端尚未做。

### 已完成
- **`nexus_backend/app/tax/engines.py`（新）**：
  - `vat()`：一般/小规模由调用方传入 `taxpayer_kind`；红字按负数冲减；作废发票排除并单独计数；免税销售额单列不产生销项；未勾选进项不计扣除但计入 `input_uncertified_cents`；进项转出增加应纳税额；留抵先抵后转出（`carry_out_cents`），实缴不为负。
  - `surtax()`：以增值税**实缴额**为基数，按政策条目逐项计算；「六税两费」减半必须显式传 `halved=True`（不自行判定资格）；无条目 409。
  - `cit_prepayment()`：累计利润 + 纳税调整 − 弥补亏损 → 应税所得，减已预缴得本期应补（可为负=多缴），并显式标注 `annual_settlement="not_enabled"`。
  - `stamp_duty()`：按合同幂等键计征，批内重复与 `already_counted` 跨批重复均 409；未知税目 409 而不是猜税率。
- **`nexus_backend/app/tax/risk_checks.py`（新）**：红字/作废进项未转出（block）、未勾选（warn）、临期 ≤30 天（warn）、销进项加权税率偏离 >5 个百分点（warn，带 `deviation_bp`）、同税种同期重复申报（block）、上次回执 `unknown`（block，禁止自动重报）。每条 finding 带 `requires_reason`。
- **测试**：`test_tax_four_engines.py` **16 passed**、`test_tax_risk_checks.py` **9 passed**，合计 25 例（覆盖红字、作废、留抵结转、转出、免税、减半显式开关、CIT 调整/亏损/预缴/多缴、印花防重、浮点金额拒绝）。

### 未完成（明确缺口）
1. 双库迁移（四税种底稿表、纳税调整台账、申报单状态流转）与 `FORCE RLS` 未写；
2. 未接线到 `workpaper.py` / `policy_items.calculate` 的正式底稿生成（当前引擎是纯函数，尚无服务层入口）；
3. 计提税金草稿凭证（科目角色路由）未做；
4. `etax_connector.py`（导包 SHA-256、沙箱状态机、unknown 防重报）未做；
5. 前端多税种策略编辑器、四税种标签页、申报矩阵未做（体积预算会逼着必须懒加载）；
6. 至少 1 条 Playwright E2E 未写；`docs/pilot/ETAX_LIVE_HANDOFF.md` 仅写骨架。

### Batch 04 续（本轮补充）
- **`nexus_backend/app/tax/etax_connector.py`（新）**：`build_package()` 产出确定性导出包（canonical JSON + `sha256` + manifest，manifest 固定写明「未经电子税务局核定，仅作复核底稿」）；`validate_declaration()` 拒绝未知税种、非法期间、空明细、**浮点金额**；`SandboxConnector` 实现 `submit/poll/resolve_unknown`，状态机 `prepared→submitted→accepted|rejected|unknown`，`unknown` 只能由人工核定为终态；`assert_can_submit()` 对 `unknown` 直接拒绝并给出「禁止自动重报」文案；`Connector` Protocol 供真实连接器实现同一四方法。
- 测试 `nexus_backend/tests/test_etax_connector.py` **12 passed**（确定性字节/摘要、manifest 声明、浮点拒绝、四类非法申报、提交→轮询终态、unknown 阻断与人工核定、非 prepared 拒绝、回执号稳定）。
- 计数：Batch 04 后端新增 **37 例**（引擎 16 + 风控 9 + 连接器 12）。
- 仍未做：双库迁移与 FORCE RLS、接线到 `workpaper.py`、计提草稿凭证、前端三处、Playwright E2E。

### Batch 04 续（第二块：双库迁移 + 复核仓储）
- **迁移（双库、均 `-- compatibility: incremental`）**：
  - `nexus_backend/migrations/postgres/316_b04_tax_period_runs.sql`：建 `accounting.tax_period_runs`（`book_id/period/kind/version` 唯一、`status ∈ draft|reviewed`、`payload_json` 原样存档、`source_digest`、编制人/复核人/复核说明，CHECK 强制「reviewed 必须有复核人与时间」）；`ENABLE + FORCE ROW LEVEL SECURITY`；三条策略：读 `book_allowed(book_id)`+`firm_id=actor_firm()`、写 `book_allowed(book_id,true)`+`created_by=actor_user()`+仅 draft、复核 `book_review_allowed(book_id)` 且 `created_by <> actor_user()`（**数据库层禁止自审**）；触发器 `tax_period_runs_immutable()` 禁止删除、禁止改 book/period/kind/version/payload/total/digest、禁止改已复核行。
  - `nexus_backend/migrations/sqlite/223_b04_tax_period_runs.sql`：同结构镜像 + 两个触发器（禁删、已复核不可改、关键列不可改）。
- **仓储 `nexus_backend/app/tax/periods.py`（新）**：`TaxPeriodService.record()/list()/get()/review()`；`record()` 按 (book, period, kind) 自增 `version`，payload 存 canonical JSON，金额必须是整数分；`review()` 要求 `review` 能力、**编制人不得复核本人**、非 draft 不得重复复核、`expected_version` 不符 409。
- **测试 `nexus_backend/tests/test_tax_period_runs.py` 6 passed**：版本自增、原样存档、自审 409（文案「制单责任人不能复核本人编制版本」）、复核人签注、过期版本 409、未知税种/浮点金额 422。
- **PG16 实证**：新建空库 `b04pg16` @25531，`migrate` 逐条应用到 **316**（含 311–315 并行迁移）；校验 `relrowsecurity=true, relforcerowsecurity=true`、三条策略名齐全、台账存在 316。
- 合计本块新增 **6 例**（Batch 04 后端累计 **43 例**）；`pytest test_tax_period_runs + test_migration_structure + test_pg_sql_hygiene + test_tax_four_engines` → **32 passed**；ruff 全绿。
- 仍未做：引擎/仓储接线到 `workpaper.py` 服务入口、计提草稿凭证、前端三处、Playwright E2E。

### Batch 04 续（第三块：四税组合包接线）
- 新增 `nexus_backend/app/tax/four_tax.py`：`package(service, actor, book_id, period, *, surtax_items, cumulative_profit_cents, …, contracts, stamp_rates)` —— **只从「已复核」的增值税底稿取权威金额**（`tax_cents / credit_carry_cents / ledger_cents / source_digest`），再由引擎派生附加税（按实缴额）、企业所得税预缴（按调用方给出的累计利润，默认回落底稿台账利润并显式标注）、印花税（按合同幂等键，重复即 409）。没有已复核底稿时报 409「请先生成并完成独立复核」，绝不静默出 0。
- 测试 `nexus_backend/tests/test_four_tax_package.py` **5 passed**（附加税按实缴额、CIT 用调用方利润而非台账、缺底稿 409、印花重复合同 409、四税种清单与整数分）。
- Batch 04 后端新增累计 **48 例**（引擎 16 + 风控 9 + 连接器 12 + 底稿仓储 6 + 组合包 5）。
- 注意：`four_tax.package` 目前是**服务入口**，尚未挂到月结/报表路由与前端；PG 侧仅验证到建表与 RLS 属性，尚未跑 PG 端到端用例。

### Batch 04 续（第四块：HTTP 入口与真缺陷修复）
- `nexus_backend/app/tax/router.py` 新增四个入口：`GET /api/tax/period-runs`、`POST /api/tax/period-runs`、`POST /api/tax/period-runs/{run_id}/review`、`POST /api/tax/four-tax`（body 只收 `book_id/period/surtax_items/…`，`extra=forbid`）。
- **修掉一个真缺陷**：`app/tax/periods.py` 的 `record`/`review` 原先用 `self.db.transaction()`（只读事务）直接 INSERT，HTTP 路径因此抛 `sqlite3.OperationalError` 并被最外层中间件兜成 503 `result_unknown`；改为 `self.db.transaction(actor=actor, write=True)` 后正常。这条经验适用于所有新服务：**写事务必须显式 `write=True` 且带 actor**，否则 SQLite 只读连接会静默失败到 503。
- 测试：`test_tax_api.py` 新增 `test_http_period_runs_roundtrip_and_four_tax_gate`（建底稿 200、列表 200、无复核底稿时 `four-tax` 409 且文案含「已复核的增值税底稿」、未认证 401/403）。`test_tax_api.py` + `test_tax_period_runs.py` 合跑 **9 passed**。
- Batch 04 后端新增累计 **49 例**。
- 仍未做：计提税金草稿凭证、前端三处、Playwright E2E、PG 端到端四税包用例。

### Batch 04 续（第五块：计提分录翻译器）
- 新增 `nexus_backend/app/tax/accrual.py`：`accrual_lines(package, *, accounts=None, chart=None)` 把四税组合包**翻译**成平衡的借贷分录（附加税→6403/2221.03、所得税→6801/2221.05、印花税→6403/2221.04），**不重新计算税额、不猜科目**：
  - 来源必须是 `status='reviewed'` 的底稿，否则 409「只能依据已复核的增值税底稿」；
  - 金额必须是整数分、非负；企业所得税为多缴（负数）时**不生成分录**并给出「请走退税或抵减」的说明；
  - 传入 `chart` 时校验每个科目码都存在，缺一个就 409 并点名，而不是把税金记到不存在的科目；
  - 借贷不平直接拒绝（构造上不会发生，作为最后一道保险）。
- 测试 `nexus_backend/tests/test_tax_accrual.py` **7 passed**（三税种平衡与默认科目、全零无分录、未复核 409、多缴不记账且有说明、科目缺失 409、自定义科目生效、浮点拒绝）。
- Batch 04 后端新增累计 **56 例**。
- 仍未做：把分录**真正写成草稿凭证**（需接 `AccountingService` 的草稿写入与科目角色校验）、前端三处、Playwright E2E、PG 端到端。

### Batch 04 阻塞点（需要产品决策，未擅自绕过）

任务书要求「四税计提草稿凭证」，本轮核实后发现**产品当前没有任何直接创建草稿凭证的入口**：

- `AccountingService` 只有 `create_book`、`suggestions()`、`approve()`、`export()` 等；全仓 `grep -rn "manual.voucher|manual_voucher"` 只命中 `b04_close/service.py:141` 的一个检查项文案，没有服务方法、没有路由。
- 草稿的唯一来源是「导入数据 → `suggestions()` 生成建议 → 生成 draft」，且带 `source`、`row_fingerprint`、`batch_id` 等溯源字段；这是刻意的控制：**账务只能来自有证据的流水**。
- 因此「按四税底稿直接写一张计提凭证」在当前架构下没有合法路径。硬写 `INSERT INTO drafts` 会绕过溯源、复核分离与审计链，属于必须拒绝的实现方式。

两条可行路线，需要决策后再实施：

- **A（推荐）**：新增「记账凭证来源」类型（`journal_entry` source kind），由已复核的四税底稿派生，携带 `source=pkg.source`（底稿 id/version/digest）与 `accrual_lines` 分录，走与流水一致的 `suggestions → 复核 → 过账` 链路，复用既有 maker-checker、幂等与审计。代价：新增迁移（source kind 扩展）+ 服务接线 + 前端。
- **B**：做一个独立的「手工凭证」能力（制单/复核/过账三段），再把四税计提接到它上面。代价更大，且会引入第二套凭证入口，必须同时补权限与审计。

在决策前，`accrual.accrual_lines()` 保持为纯翻译层（已完成、7 例测试），不产生任何账务写入——这是安全的中间状态。

### Batch 04 续（第六块：计提预览接口，只读）
- `nexus_backend/app/tax/router.py` 新增 `POST /api/tax/accrual-preview`：同一 `FourTaxIn` 请求体，内部先算 `four_tax.package()` 再 `accrual.accrual_lines()`，返回 `entries / debit_cents / credit_cents` 并显式带 `posted: false` 与「预览仅供参考：本接口不写账，也不会生成草稿凭证」。
- 该接口**故意只读**：在「四税计提凭证」路线未决策前，不提供任何账务写入；凭证据写入仍走 `suggestions → 复核 → 过账` 或将来新增的 `journal_entry` 来源类型。
- 测试：`test_tax_api.py::test_http_period_runs_roundtrip_and_four_tax_gate` 增断言——未复核底稿时 `accrual-preview` 同样 409 且文案含「已复核的增值税底稿」，未认证 401/403；该文件 **3 passed**。

### Batch 04 续（第七块：前端计提预览）
- 新增 `src/TaxAccrualPanel.tsx`：调用只读 `POST /api/tax/accrual-preview`，展示四税分录（科目/摘要/借贷，元为单位、整数分换算）；无已复核底稿时**直接展示服务端说明**（「缺少已复核的增值税底稿…」），不编造数字；空分录用「本期没有可计提的税金」而不是 0 行；正文固定写明「预览不写账：计提凭证仍须走建议→复核→过账，制单人与复核人必须是不同的人」。
- 挂载：`src/TaxWorkpaperPanel.tsx:328` 在 `TaxSourcePanel` 之后；组件懒加载页面内，未进入首屏 chunk。
- 测试：`src/tax-accrual-panel.test.ts` **4 passed**（金额格式化、不出现「已过账」、阻断态显示服务端原因且不渲染表格、空分录文案）。前端全量 `vitest` **1441 passed**；`tsc --noEmit`、`eslint`、`prettier` 绿；`npm run build` ✓；`test:frontend-budget` `violations: []`。

### Batch 04 续（第八块：禁用单一税率的静默回退）
- 缺陷：`nexus_backend/app/tax/workpaper.py:302` 在「本期没有核定分税目政策」时，直接用档案里的 `vat_rate_bp` 估算 `tax = _ratio(output_net, vat_rate_bp)`。若本期销项存在多档税率，这会算出一个**看起来正常、实际错误**的税额，正是任务书 1.1 明令禁止的「单一 `vat_rate_bp` 静默回退」。
- 修法：`policy is None` 分支先按每行 `tax_cents*10000/amount_cents` 求整数基点税率集合；集合大小 > 1 时抛 409「本期销项发票存在多档税率，不能再用档案里的单一税率估算增值税；请先核定分税目税率政策后再生成底稿」。单一税率期间行为不变（仍按档案税率），有政策期间仍走政策金额。
- 测试：`nexus_backend/tests/test_tax_workpaper_guards.py` 新增两例——多档税率 409 且文案含「多档税率」；单税率两行仍得 45.00。该文件 **8 passed**；`test_tax_workpaper.py` + `test_tax_rounding_consistency.py` + `test_tax_period_runs.py` 合跑 **16 passed**；ruff 绿。

### Batch 04 验收证据汇总（截至本轮）

| 项 | 命令 / 文件 | 结果 |
| --- | --- | --- |
| 引擎 | `test_tax_four_engines.py` | 16 passed |
| 进项风控 | `test_tax_risk_checks.py` | 9 passed |
| 连接器与状态机 | `test_etax_connector.py` | 12 passed |
| 底稿仓储（双库迁移 316/223 + FORCE RLS） | `test_tax_period_runs.py` | 6 passed |
| 四税组合包 | `test_four_tax_package.py` | 5 passed |
| 计提翻译 | `test_tax_accrual.py` | 7 passed |
| HTTP 契约 | `test_tax_api.py` | 3 passed |
| 单一税率静默回退 | `test_tax_workpaper_guards.py` | 8 passed |
| 前端预览 | `src/tax-accrual-panel.test.ts` | 4 passed |
| 域名回归 | `pytest nexus_backend/tests -k "tax"` | **305 passed, 87 skipped, 6198 deselected**（353.73s） |

仍缺（均依赖「计提凭证入账」路线决策）：真正入账的计提凭证、申报矩阵页、Playwright E2E、PG 端到端四税包。

## Batch 05 出纳与银行 H2H（进行中，第一块）

- 新增 `nexus_backend/app/treasury/receipt_matcher.py`：`match(receipts, lines)` 按「本方账号 + 银行流水号 + 交易日期 + 整数分金额 + 对方户名」五字段确定性匹配；唯一命中才挂接，其余进人工队列并给 `reason`（`receipt_incomplete` / `line_incomplete` / `no_candidate` / `ambiguous`）与中文说明；同一流水行不会被两张回单复用；跨账号一律 `no_candidate`；剩余流水行列入 `unmatched_lines` 交柜台核对。
- 设计取舍：**不按金额或日期模糊匹配**，同日同额多候选明确报「按金额猜测会造成错配」。这是 1.3 节「消除 TAXI_CHAIN B3」的核心判据。
- 测试 `nexus_backend/tests/test_receipt_matcher.py` **8 passed**；ruff 绿。
- 未做：`fee_receivable_allocations` 增 `bank_statement_line_id` 迁移、出纳/审批角色与 `payment_*` 表、双行适配器与沙箱、前端出纳工作台、E2E。

### Batch 05 第二块：核销到银行流水行的强绑定
- 迁移 `nexus_backend/migrations/postgres/317_b05_allocation_bank_line.sql` 与 `nexus_backend/migrations/sqlite/224_b05_allocation_bank_line.sql`（均带 `-- compatibility: incremental`）：`fee_receivable_allocations` 增加可空 `bank_statement_line_id`，外键指向 `bank_statement_lines(id)`，并建部分索引 `fee_alloc_bank_line_idx`。可空是有意的：历史核销没有回单行，补录前不能伪造外键。
- 测试 `nexus_backend/tests/test_b05_bank_line_binding.py` **1 passed**：列存在、可空、外键确实指向 `bank_statement_lines`。
- 门禁：`test_migration_structure.py` + `test_pg_sql_hygiene.py` **10 passed**。
- 未做：服务层在核销时写入该列、`cashier`/`treasury_approver` 角色与 `payment_*` 状态机、双行适配器与沙箱。

### Batch 05 第三块：付款内控判据（纯函数）
- 新增 `nexus_backend/app/treasury/payments.py`：状态白名单 9 态与迁移表；`check_separation(maker, approver, voucher_reviewer)` 强制三权分立（制单人 ≠ 资金审批人；资金审批人 ≠ 凭证复核人）；`needs_escalation()` 单笔超限或日累计超限即需升级二次审批；`advance()` 只允许白名单迁移，`bank_unknown` 禁止重发/切渠道（文案「必须先查回原指令终态」），失败/退票必须写原因；`same_payment()` 幂等键相同即复用原指令。
- 测试 `nexus_backend/tests/test_payment_rules.py` **11 passed**；ruff 绿。
- 未做：`cashier`/`treasury_approver` 迁移与 RLS 授权、`payment_batches/orders/approvals/events` 表、服务层接线、前端出纳台。

### Batch 05 第四块：付款指令表与数据库级三权分立
- 迁移 `nexus_backend/migrations/postgres/318_b05_payment_orders.sql`、`nexus_backend/migrations/sqlite/225_b05_payment_orders.sql`（均 `-- compatibility: incremental`）：
  - `payment_orders`：9 态 CHECK、`UNIQUE(book_id, idempotency_key)`、`CHECK(approver <> maker)`、`CHECK(reviewer NOT IN (maker, approver))`、`CHECK(status NOT IN ('failed','bounced') OR reason <> '')`；
  - `payment_events`：append-only（SQLite 触发器 / PG 触发器函数）；
  - PG 侧 ENABLE + FORCE RLS，读 `book_allowed`、写 `book_allowed(...,true)` 且 `maker_user_id = actor_user()`，事件写 `actor_user_id = actor_user()`。
- 关键取舍：把三权分立写进 **CHECK 约束**而不是只写服务层——服务层可以被后来的调用方绕过，约束不会。
- 测试 `nexus_backend/tests/test_b05_payment_tables.py` **6 passed**（三人可建单、制单人当审批人被拒、审批人兼复核被拒、失败必须写原因、幂等键同账套唯一、事件不可改）。`test_migration_structure.py` + `test_pg_sql_hygiene.py` 10 passed；ruff 绿。
- 未做：PG 实例实跑 318（本地无 DSN）、`cashier`/`treasury_approver` 角色加入访问模型、服务层与前端接线。

### Batch 05 第五块：付款指令服务层
- 新增 `nexus_backend/app/treasury/service.py::PaymentService`：`create`（按 `(book_id, idempotency_key)` 幂等，重复提交返回原指令；`needs_escalation` 按单笔/当日累计判定并落 `escalated`）、`submit`、`approve`（调 `check_separation`，制单人自审返回 403）、`advance_external`（白名单迁移，`bank_unknown` 不能跳回 `submitted`）；每次迁移写 `payment_events`。
- 授权走 `AccessControl.book(...,'read'|'edit')`，不在 SQL 里依赖 PG 专用 `book_allowed()`——同一路径 SQLite/PG 都跑得通。
- 测试 `nexus_backend/tests/test_b05_payment_service.py` **6 passed**（幂等且只记一条事件、制单人不能自审、审批人不能兼凭证复核、非法迁移 409、bank_unknown 不能跳回 submitted、大额升级）。
- 未做：HTTP 入口（`/api/treasury/payment-orders`）、`cashier`/`treasury_approver` 角色、前端付款台、PG 实例实跑。

### Batch 05 第六块：付款台 HTTP 入口
- `nexus_backend/app/treasury/router.py::build_treasury_router`：`POST/GET /api/treasury/payment-orders`、`.../{id}/submit`、`.../{id}/approve`、`.../{id}/advance`；请求模型全部 `extra="forbid"`，金额 `StrictInt`（只收整数分）。
- `nexus_backend/app/treasury/service.py` 增 `list_orders(actor, book_id, status=)`（读授权 `access.book(...,'read')`，按创建时间倒序，上限 200）。
- `nexus_backend/app/main.py`：注册 `application.state.payments = PaymentService(database)` 并 `include_router(build_treasury_router(actor))`。
- 测试 `nexus_backend/tests/test_b05_payment_http.py` **3 passed**（创建+列表、制单人经 HTTP 审批仍 403、第二人审批成功且非法迁移 409、多余字段 422）；付款三文件合跑 **15 passed**。
- 测试用最小 FastAPI 应用 + 显式 `AccountingError` 处理器复现生产中间件语义（否则 TestClient 会把领域异常当服务端错误抛出）。
- 未做：`cashier`/`treasury_approver` 角色进入访问模型、前端付款台、PG 实例实跑 318。

### Batch 05 第七块：银行数据双适配器
- 新增 `nexus_backend/app/treasury/adapters.py`：`BankLine`（account/serial/date/amount_cents/direction/counterparty/source）+ `parse_bank_csv`（柜台 CSV，`csv.DictReader`）、`parse_receipt_json`（电子回单 JSON，dict 或 bytes 均可）、`merge`（同账号+流水号跨来源去重）。
- 归一规则：金额一律整数分（`"1,200.50"` → `120050`），方向由 `direction` 表达而不是金额符号；**遇到未知方向、缺列、金额为 0 或浮点，整批抛 `AdapterError` 并带行号**——不猜。
- 测试 `nexus_backend/tests/test_b05_adapters.py` **6 passed**（两来源同形状、未知方向拒绝、缺列拒绝、零金额拒绝、浮点金额拒绝、跨来源重复合并）。
- ruff 全绿。未做：把适配器接到 `receipt_matcher` 的导入链路与 HTTP（`/api/treasury/bank-lines/import`）、前端导入预览。

### Batch 05 第八块：导入预览 HTTP
- `POST /api/treasury/bank-lines/import-preview`（`BankLineImport{format:csv|receipts, csv_text?, receipts?, statement_lines?, source?}`，`extra="forbid"`）：
  - `format=csv` → `parse_bank_csv` 归一化（金额整数分、方向枚举），返回 `lines`；
  - `format=receipts` → `parse_receipt_json` → `Receipt`，与 `statement_lines` 做五字段唯一匹配，返回 `matched/manual/unmatched_lines`；
  - 固定 `posted:false`：预览不写账；`AdapterError` → 400 且保留行号与原因。
- 测试 `test_b05_payment_http.py` 扩到 **7 passed**（CSV 归一化、坏 CSV 400 带「缺少列」、缺 payload 400、回单对流水唯一命中）。
- 设计取舍：CSV 只做「流水侧」归一，不冒充回单；匹配必须两侧齐备，避免把导入当成核销。
- 未做：落库（`bank_statement_lines`/`fee_receivable_allocations`）、前端导入预览页、适配器接入 `import_sources` 既有链路。

### Batch 06 · B5：利润下钻分页（完成）
- 现状：`nexus_backend/app/reports/profit.py` 的 `overview()` 里 `visible_items = [...][:1000]` 静默截断，超过 1000 条的明细无声消失，且没有任何分页或总量提示。
- 改法：`overview(actor, book_id, period, line_code="", report_run_id="", offset=0, limit=200)`；过滤后的全集只用于统计，不再截断：
  - `filtered = [item for item in details if not line_code or item["net_cents"] != 0]`
  - `total_count = len(filtered)`，`limit = max(1, min(limit, 500))`（上限 500，超大请求自动收敛）
  - 返回新增 `pagination{offset, limit, total_count, returned, has_more}` 与 `drilldown_total_cents`（全量明细净额合计，供与报表行金额交叉校核）
- 路由：`nexus_backend/app/reports/router.py::profit_overview` 增 `offset`（`ge=0, le=1_000_000`）与 `limit`（`ge=1, le=500`）查询参数。
- 兼容性：`items` 字段名与结构不变，旧前端只是看不到全量；新增字段为附加字段。
- 测试：`nexus_backend/tests/test_tax15_profit.py::test_profit_drilldown_pages_instead_of_silently_cutting_at_a_thousand`（5 笔收入 → 首页 2 条 `has_more=True`、末页 1 条 `has_more=False`、两页 `drilldown_total_cents` 相同、`limit=10000` 收敛为 500）；该文件 **5 passed**。
- 未做：前端下钻抽屉接「加载更多」与总数提示（当前仍只渲染首屏）。

### Batch 06 · B5 前端：下钻「加载更多」与总数（完成）
- `src/TaxFPanel.tsx`：
  - 新增 `DrillPagination` / `DrillPage` 类型；`Profit` 增可选 `pagination`、`drilldown_total_cents`。
  - 新增 `loadDrill(lineCode, offset)`：按 `line_code + offset + limit=200` 单独取该行下钻，追加式合并（`offset===0` 覆盖）；切项目时 `setDrill(null)` 防串行旧数据。
  - 「查看依据」改调 `pickLine()`；换报表版本/刷新后若已有选中行，自动重取第一页（注释：不能留着旧版本的凭证行）。
  - 表头 caption 显示「共 N 行，已显示 M 行」；`has_more` 时出现「加载更多（还剩 K 行）」。
  - 一致性判断改用服务端全量合计 `drilldown_total_cents`，不再用当前页累加——分页越深，页内累加越容易被误判成「不一致」。
- 门禁：`npx tsc --noEmit` 绿、`eslint src/TaxFPanel.tsx` 绿、`vitest` **161 files / 1441 passed**、`npm run build` ✓、`test:frontend-budget` `status=passed`、`violations=[]`（未新增 CSS，避开只剩 285B 的 homeCss 余量）。
- 未做：深链分享（B7/B8）与「进入凭证」跳转。

### Batch 06 · PLT05-B：差异处置必须带结构化原因码（完成）
- 迁移：`nexus_backend/migrations/sqlite/226_plt05_reason_code.sql`、`nexus_backend/migrations/postgres/319_plt05_reason_code.sql`（均 `-- compatibility: incremental`）。`bridge_return_row_handlers.reason_code`，历史行标 `legacy` **不做猜测性回填**——把旧记录改写成看起来合规的样子，比承认它没有原因码更糟；PG 侧带 CHECK 白名单。
- 契约：`BridgeReturnRowHandle` 增必填 `reason_code ∈ {amount_mismatch, account_mismatch, date_mismatch, voucher_missing, other}`；缺码由 Pydantic 直接 422。
- 服务：`handle_return_row` 校验 `other` 必须把原因写清（≥10 字），入库写入 `reason_code` 并计入指纹；幂等比较同时比对 `reason_code`；返回视图 `handler` 带 `reason_code`，`ignored` 与 `resolved` 一样留在 `rows[]` 历史里可查。
- 测试：`test_plt05_return_import.py`、`test_plt05_return_http.py`、`test_pg_sql_hygiene.py`、`test_migration_structure.py` 合跑 **25 passed**（PG RLS 用例无 DSN 时 skip）；`test_write_scope_baseline.py` 一并转绿（见下）。
- 顺带修的真缺陷：`tax/periods.py` 与 `treasury/service.py` 的写事务原为 `write=True` 无作用域，被 `test_write_scope_baseline.py` 判为「新写事务未声明 write_scope」；补 `company_id + write_scope="company"`（先只读解析公司再开写事务），`test_write_scope_baseline.py` + 五个相关套件 **24 passed**。
- 未做：PLT05-C/D/E（仅允许确认失败且仍有权限的凭证重走预览补导、未知状态禁重发、跨期/错法人拦截、集团与月结预检接入外部正式账三态提示）。

### Batch 06 · B2：来源行键绑定（完成）
- `nexus_backend/app/tax/relations.py::verify_in` 在写入新关系前检查同账套同期间是否已有关系引用同一结算单行、银行流水行或手续费凭据行（排除完全相同的三元组，那是幂等重放）：命中抛 409「来源行已绑定到另一条来源关系，同一笔流水不能重复解释差额」。
- 为什么必须拦：单条关系内部永远自洽（结算 1100、银行 800、差额 300），重复引用只有在**跨关系**看才暴露——同一笔到账被用来解释两个差额，账面凭空多出一笔钱。
- 实现教训：`fee_source_id` 可空，第一版用 `COALESCE(fee_source_id,'')=?` 拿空串比较，把所有「无手续费凭据」的关系都当成命中，既有用例 `test_same_month_same_amount_rows_keep_separate_relations` 立刻转红；改为按 `fee_id` 是否为空拼接谓词（空时用 `fee_source_id IS NULL`）。
- 测试：`test_plt04_fee_variances.py` 新增 `test_a_source_row_cannot_explain_two_differences`（同一结算行配两个不同银行行 → 第二次 409 且文案含「重复解释差额」）；该文件 **18 passed**，ruff 全绿。

### Batch 06 · B7：利润下钻深链分享（完成）
- 新增 `src/taxf-link.ts`：`parseTaxFLink(search)` 只接受白名单行号（`^[A-Za-z0-9_.-]{1,32}$`）与 0–999999 的偏移，其余一律当作「没有链接参数」，不把用户输入回填给接口；`taxFLinkUrl(href, line, offset)` 在保留既有查询串与 `#fragment` 的前提下追加入参。
- `src/TaxFPanel.tsx`：数据就绪后若 URL 带 `taxf_line` 且当前未选行，自动 `pickLine()` 还原下钻；新增「复制本行链接」按钮（`navigator.clipboard`，失败时退化为把整条 URL 显示在按钮旁边）。
- 刻意不改 history：按钮只生成链接，不 push/replace 路由。这个应用的路由是自己实现的（`readWorkspaceRoute` 读 hash），顺手改 history 很容易把「返回上一步」变成错乱的回退序列。
- 测试 `src/taxf-link.test.ts` **5 passed**（合法解析、非法行号丢弃、非法偏移回落首页、已有查询串与 fragment 保留、非法行号拒绝生成链接）；`tsc --noEmit`、`eslint src`、`format:check`、`build` 全绿，`test:frontend-budget` `violations=[]`。

### Batch 06 · PLT05-C（部分）：回执证据下载绑定源单据归属
- `nexus_backend/app/integrations/service.py::evidence_for` 查询增加 `JOIN bridge_export_artifacts a ON a.id=i.artifact_id`，并要求 `a.book_id=i.book_id AND a.company_id=i.company_id AND a.firm_id=i.firm_id`。原先只校验 `e.import_id/e.book_id` 与可选的 `i.artifact_id`，上传行指向别的客户单据时仍会放行该文件。
- **没有配独立红测**：`bridge_return_imports.artifact_id` 有外键，损坏的指向在当前 schema 下构造不出来（试过改 artifact 的 `book_id` 触发 FK、试过新建第二账套挂他人 artifact 被账套名校验挡住）。加约束是为了「即使将来某条路径写坏了指针也不会跨客户泄露」，属于纵深防御，不虚构一个用 API 造不出来的失败态。
- 回归：`test_plt05_return_import.py` + `test_plt05_return_http.py` **15 passed**，ruff 全绿。
- 仍未做：仅确认失败且仍有权限的凭证可重走补导、unknown 禁重发（已有 N03 围栏覆盖）、跨期拦截、集团与月结预检的外部正式账三态提示。

### Batch 07 · 1.4 凭据与依赖安全门禁（部分完成）

- `scripts/check_secrets.py`：私钥块、AWS key、Slack token、带口令的 PG DSN、≥32 字硬编码密钥赋值五类规则；`# pragma: allowlist secret` 单行豁免；只报「可能被提交」的路径（候选路径逐个 `git check-ignore -q`），被忽略的本机 `.env` 不算泄漏。当前仓库扫描 `no secrets found`。
- `nexus_backend/tests/test_check_secrets.py` 7 passed。测试必须用系统 temp 目录：仓库内临时目录被 `.gitignore` 覆盖，会被扫描器按设计跳过（首版用 `tmp_path` 时 5 例假红）。
- CI 新增 `security` job：`check_secrets.py` 硬阻断；`bandit -q -lll`（high）硬阻断——`-ll` 现有 287 medium / 16 low，一次全开会让所有 PR 常红，medium 积压记为债务；`pip-audit` 观察态（`continue-on-error: true`），因为当前 `nexus_backend/requirements.txt` 有 23 条 GHSA 全部来自 pillow 12.3.0（需升到 ≥13.0.0），升级后去掉该开关。
- 本机复跑 pip-audit 需 `PYTHONUTF8=1`：requirements 含中文注释，Windows 默认 GBK 解码报 `UnicodeDecodeError: 'gbk' codec can't decode byte 0xa1`。

### Batch 07 · 1.3 多租户配额（部分完成）

- 迁移 `nexus_backend/migrations/postgres/320_sec07_firm_quotas.sql`：`accounting.firm_quotas`（按 `firm_id` 主键，单次导入行数 / 单日异步任务数 / 原件容量 / 并发请求数，均带范围 CHECK 与默认值）；`ENABLE + FORCE RLS`，读写策略绑 `accounting.actor_firm()`，写还要求 `updated_by = accounting.actor_user()`，并对 `accounting_app` 收回 DELETE。
- 迁移 `nexus_backend/migrations/sqlite/227_sec07_firm_quotas.sql`：同表同约束；SQLite 无 RLS，用「禁止删除」触发器保证本地数据与生产契约一致（配额只能改数值，不能靠删记录绕过）。
- 缺失行即默认值：`nexus_backend/app/security/quotas.py` 的 `DEFAULTS` 是唯一来源（导入 5000 行/批、异步 500 个/日、原件 2 GiB、并发 32），新企业无需引导写入。
- `QuotaService.quota/configure`（仅 `FIRM_MANAGERS`）与三条统一核验 `ensure_import_rows`（超限 413）、`ensure_daily_jobs`（429）、`ensure_storage`（413）；错误文案同时给出当前值与上限，不给「稍后重试」这种无用提示。
- 拒绝路径**只记日志不写审计表**：核验发生在调用方事务内，抛错会回滚该事务，此时写 `audit_events` 会被一起回滚——写了等于没写。改为 `accounting.quotas` logger 的结构化告警（含 firm/actor/limit/requested），持久证据由调用方自己的审计链承担。这是取舍，不是遗漏。
- `nexus_backend/tests/test_tenant_quotas.py` **10 passed**（默认值、仅管理者可改、部分更新持久化、四类非法值拒绝、413/429 边界与「等于上限仍放行」、按事务所隔离）。
- 未完成：把三条核验接到导入 / 异步入队 / 原件上传入口（需先确认各自事务边界，避免把日志写进已回滚的事务），以及配额读写的 HTTP 入口与前端展示。

### Batch 07 · 1.2 应用层信封加密（部分完成）

- `nexus_backend/app/security/kms.py`：`KeyProvider` 协议 + `LocalEnvKeyProvider`（AES-256-GCM，12 字节随机 nonce）；`Envelope{key_id,nonce,ciphertext}` 自带 `to_json/from_json`（版本 1、算法 A256GCM、严格 base64 校验），密文可入库或写文件而不需要额外元数据列。
- **轮换是数据操作而不是上线动作**：每条信封记录生成它的 `key_id`，`decrypt` 按 `key_id` 找密钥，`reencrypt` 把单条记录迁到当前活动密钥。旧密钥留在环里即可继续解历史数据；把旧密钥移出环会让解密**明确报错并点名缺哪把钥匙**，而不是返回垃圾字节。
- 配置错误一律拒绝启动而不是降级明文：空密钥环、非 base64、缺冒号、标识重复、密钥长度不是 32 字节、`ACTIVE_KEY_ID` 不在环里，都会抛 `KmsError` 并说明期望格式。
- `nexus_backend/tests/test_kms_envelope_encryption.py` **14 passed**（往返与 JSON 序列化、nonce 不重复、密文不含明文、篡改/换 AAD 被 `InvalidTag` 拦下、轮换后旧记录可读且可迁移、丢旧密钥报错、环境解析与活动密钥默认值、六类坏配置参数化、信封版本/字段垃圾拒绝）。
- `nexus_backend/requirements.txt` 显式声明 `cryptography>=43,<52`：模块直接 import 它，不能靠 `PyJWT[crypto]` 的传递依赖（被别的依赖顺手升级或移除就会炸）。`scripts/check_test_env.py` 仍匹配 lock（36 packages）。
- 未完成：把信封加密接到原件存取路径（需要先定「加密落盘 + 数据库只存信封」的存储契约）、云 KMS/国密适配器、密钥轮换的批处理入口与进度指标。

### Batch 07 · 1.1 高危动作的 AAL2 守卫（部分完成）

- 既有基础：`Identity.aal` 只取身份提供方验证过的声明（`app/auth/supabase.py:40`、`:335`），用户可编辑的 metadata 不参与判断；管理域已有 `requires_admin_mfa` 中间件。
- 新增 `HIGH_RISK_ACTIONS`（`app/auth/admin_security.py`）五项：付款放款审批 `/api/treasury/payment-orders/{id}/approve`、月结锁期 `/api/b04-close/{id}/lock`、正式报表发布 `/api/reports/{id}/publish`、限时复核委托 `/api/books/{id}/review-delegations`、全量原件导出 `/api/firm/batch-operations/review-export`。它们不属于 admin 域，但爆炸半径相同，因此走同一道门。
- **策略开关 `ACCOUNTING_MFA_ENFORCE`（off|warn|enforce，默认 warn）**：`enforce` 拒绝 aal1；`warn` 放行并记结构化告警；`off` 关闭。默认 warn 是刻意的——若直接 enforce，所有没绑定第二因子的真实用户会在发布瞬间失去锁期与发布能力，这不是安全加固而是事故。开关取值非法即拒绝启动。
- 读请求与近似路径不受影响：`GET /api/reports/{id}/publish`、`review-export/preview`、`review-delegations/{id}`（撤销）等均放行；收件人接受邀请 `/api/admin/account-requests/{id}/accept` 仍豁免（那校验的是待审请求本身）。
- `nexus_backend/tests/test_auth_oidc_mfa.py` **17 passed**（五类高危动作、七类近似路径与只读放行、尾斜杠归一、收件人豁免、动作清单恰为五项、开关默认与非法值）；与既有 `test_admin_security.py` 合跑 **51 passed**；`test_tax_api.py` 冒烟通过（本地模式不受影响）。
- 未完成：真正的 OIDC IdP 本地测试替身（签名伪造/过期 token/低 AAL 的端到端登录流）、前端二步验证引导、`enforce` 前的用户 MFA 绑定率核查。

### Batch 08 · 1.3 结构化脱敏故障日志（部分完成）

- 新增 `nexus_backend/app/observability/failures.py`：`failure_record()` 输出单行 JSON，字段为 `event/request_id/trace_id/firm_id/method/route/error_class/sqlstate/safe_location`，每个值都过 `app/security/redaction.py:redact_sensitive`；`firm_of()` 从 `request.state.identity` 取事务所。
- SQLSTATE 采集不引入驱动依赖：依次看 `sqlstate`/`pgcode`/`diag.sqlstate`，sqlite 退到 `sqlite_errorname`；只输出短码。
- `safe_location` 取栈底帧的 `文件名:行号`（截断 160 字符），**不含参数与局部变量**——原来的 `class=DatatypeMismatch` 只知道坏了、不知道坏在哪，加参数又会把行值写进日志。
- `app/main.py` 中间件把这条 JSON 打进 error 日志；响应体仍是既有的中文兜底文案（读请求 500 `internal_error`、写请求 500 `result_unknown`），不回显驱动原文。
- `nexus_backend/tests/test_structured_failure_log.py` **6 passed**：字段齐全、驱动文本与卡号/口令绝不出现、`firm_of` 缺失时为空串、真实中间件路径产出恰好一条记录且 `X-Request-Id` 被回写、恶意 `X-Request-Id`（NUL、200 字符）被替换成 32 位十六进制。与 `test_qa_http_failures.py` 合跑 **14 passed**。
- 未完成：`/metrics` Prometheus 指标（含四项财务业务 SLA）、OTLP 导出与 Worker 侧 trace 透传、`app/observability/alerting.py` 与 `docs/ops/PRODUCTION_RUNBOOK.md`、限流器 Redis 后端与对象存储抽象。

### Batch 08 · 1.3 Prometheus 指标端点（部分完成）

- 新增 `nexus_backend/app/observability/metrics.py`：手写注册表（dict + Lock + 文本格式），不引入 `prometheus_client`——理由和既有的手写 CSV 渲染、手写审计链一致，八十行不值得新增依赖。
- 已导出：`http_requests_total{method,route,status}` 计数器与 `http_request_duration_seconds` 直方图（11 个桶 5ms–10s，`_bucket/_sum/_count` 齐全，`+Inf` 桶必出）。**`route` 用路由模板**（`/api/books/{book_id}`）而不是原始路径：每本书一个 label 会把基数打爆，这也是 Prometheus 的基本纪律。
- `GET /metrics`（`include_in_schema=False`）访问控制：回环地址直连放行；设 `ACCOUNTING_METRICS_REQUIRE_TOKEN=1` 后必须携带 `x-metrics-token` 且与 `ACCOUNTING_METRICS_TOKEN` 用 `compare_digest` 比对。**不能用「回环即安全」蒙住代理场景**——反代后面的请求源地址可能就是回环。
- 标签排序与整数格式化：同一组样本每次输出字节一致，便于抓取端 diff；`le="1"` 而非 `le="1.0"`。
- `nexus_backend/tests/test_telemetry_and_metrics.py` **8 passed**：HELP/TYPE/计数、桶的累积语义与 `+Inf`、标签注入转义（引号/换行/反斜杠）、真实请求计数为 3、book id 不出现在标签里、REQUIRE_TOKEN 三种情形、无令牌时回环可抓、`text/plain; version=0.0.4`。
- 未完成：四项财务业务 SLA 计数器（`accounting_vouchers_posted_total` / `tax_workpapers_generated_total` / `payment_orders_state_total` / `workspace_projection_lag_seconds`）尚未在业务动作里埋点；连接池与队列深度指标；OTLP 导出与 Worker 侧 trace 透传；告警分发与 Runbook；Redis 限流与 S3 对象存储。

### Batch 08 · 1.3 财务业务 SLA 埋点（部分完成）

- 计数器三处真实埋点：`app/periodic/service.py::post`（`accounting_vouchers_posted_total`，**只在该事务提交后递增**——回滚的过账不该留下计数，重放路径在 `status=='posted'` 时提前返回、也不计数）；`app/tax/workpaper.py::generate`（`tax_workpapers_generated_total`）；`app/treasury/service.py::_move`（`payment_orders_state_total{state}`，用状态标签而非九个计数器，新增状态时不会静默漏计）。
- 未做：`workspace_projection_lag_seconds`（需要投影时间戳与抓取时刻的差，先得有可读的投影水位）、数据库连接池占用、异步队列深度。
- `test_telemetry_and_metrics.py` **9 passed**；埋点后回归 `test_b05_payment_service.py` + `test_tax_workpaper.py` **15 passed**。

### Batch 07 · 1.3 入口接线：导入配额（部分完成）

- `nexus_backend/app/accounting/service.py::import_sources` 在**同一个写事务内**调用 `QuotaService(self.db, self.access).ensure_import_rows(conn, actor, len(request.rows))`。放在事务内是有意的：跨连接先查后写在并发下会被赛过，配额就只是装饰。
- 命中上限 → 413「单次导入 N 行，超过本单位上限 M 行；请拆分文件后重试」，并且因为检查在写入前，账套不会留下半批流水。
- `nexus_backend/tests/test_quota_import_wiring.py` **2 passed**（超限拒绝且 `sources` 为空、限内正常落库）；`test_tenant_quotas.py` + `test_accounting.py` 回归 **55 passed**；ruff 全绿。
- 仍缺：异步任务配额（`ensure_daily_jobs`，需队列入口）、原件容量配额（`ensure_storage`，需原件上传入口）、`ensure_concurrent`（需请求级中间件）三处接线；配额只覆盖导入一条链路。

### Batch 09–10 前置判定：不满足启动条件（阻塞）

- `docs/upgrade_85/UPG09_WORKFLOW_AND_AGENT.md` 的前置写明「Batch 02 + Batch 05 完成」。当前两者均为**部分完成**：Batch 02 缺 1.3 的未保存拦截与逐页月份输入；Batch 05 缺出纳/财务经理角色、付款指令落库前端与 PG 实例实跑 318。按 `UPG00_MASTER.md` 的依赖关系（02 是 03/04/05/09 的前置，05 又是 06 的前置），此时启动 09 只会在别人的半成品上再造一层，版本化 DAG 会锁进未定的审批语义。
- `docs/upgrade_85/UPG10_SHARED_QUEUE_AND_FINAL_GATE.md` 要求 01–09 全绿，同样不满足。
- 处置：不启动 09/10，不写空壳的 workflow 表与引擎脚手架；把门禁与前置缺口写进本台账，等 02/05 收口后按序执行。Batch 04 的计提入账仍等你选 A（新增 `journal_entry` 来源）或 B（独立手工凭证），这条也卡着 06 的规费批次入账。

## 升级 85 · 当前总览（2026-11-21 21:00 复核）

| Batch | 状态 | 已完成的关键能力 | 主要缺口 |
|---|---|---|---|
| 02 报表合规与工作台 | 部分完成 | 模板回落消除、legacy 模式迁移+回填、顶部单位/身份/期间切换条、二次输入确认（替换全部 `window.confirm`）、单位×环节矩阵 | 1.3 未保存离开拦截、逐页月份输入、切换条的栏内切换 |
| 03 审计与恢复 | 部分完成 | RWK05-A 全模块机器验收包、真实数据审计链三态篡改实证、PLT06-B 分级撤权、PLT06-D 恢复后办结、跨主版本 pg_restore 守卫 | PLT06-A/C 为产品缺口（无汇总/明细/原件三级分授） |
| 04 税务自动化 | 部分完成 | 四税种确定性引擎、进项风控、申报包与状态机、period_runs 双库迁移+RLS、四税组合包、HTTP 入口、计提分录翻译与只读预览 | **计提凭证入账待定 A/B**、申报矩阵页、E2E、PG 端到端 |
| 05 资金与银企直连 | 部分完成 | 回单五字段匹配、核销绑定流水行、付款内控纯函数、payment_orders 双库迁移（三权分立写进 CHECK）、服务层、HTTP、双适配器与导入预览 | 出纳/财务经理角色、落库前端、PG 实例实跑 318 |
| 06 行业与 T+ | 部分完成 | 利润下钻分页（前后端）、来源行键绑定、深链分享、PLT05-B/C 原因码与 artifact 归属、B2 来源行键 | B1 规费批次受控记账、三家行业处理器、B7/B8 余项 |
| 07 安全 SSO/KMS | 部分完成 | 信封加密（AES-256-GCM + 密钥版本）、五类高危动作 AAL2 守卫+开关、租户配额（双库+RLS）、CI 凭据扫描与 bandit 高严重度门禁 | OIDC IdP 替身、云 KMS 适配器、配额另三条链路接线、前端二步验证引导 |
| 08 云原生可观测 | 部分完成 | 结构化故障日志（脱敏、SQLSTATE、栈底定位）、Prometheus 指标（低基数、令牌保护）、三处业务 SLA 计数 | OTLP/trace 透传、告警规则与 Runbook、Redis/S3、投影延迟 |
| 09 工作流与调查助手 | **未启动（前置不满足）** | — | Batch 02 与 05 均未完成 |
| 10 共享队列与终门 | **未启动（前置不满足）** | — | 需 01–09 全绿 |

- 本轮复核证据：`scripts/check_test_env.py` 通过（36 包与 lock 一致）；八个新套件跨批回归 **77 passed**（53.66s）。
- 阻塞清单：①计提入账 A/B 待决策（卡 04 与 06）；②需要真实外部依赖才能证明的部分（Redis/S3/OIDC IdP/PG17 四身份/云 KMS）不写成「已完成」；③其余为纯工作量缺口。

## 推送前必须核查的事：生产迁移水位（2026-11-21 21:03 实测）

- 实测（Supabase Management API 查 `accounting.schema_migrations`）：`max(version)=319`、`count=306`；310–319 全部在册。即已提交的 313/316–319 **已先于镜像进入生产**，不存在「代码带迁移、库没跟上」的崩溃窗口。
- 本地未应用/未提交的只剩 `nexus_backend/migrations/postgres/320_sec07_firm_quotas.sql`（SQLite 侧 227 不涉生产）。这批如要推，先应用 320、核对台账，再推镜像。
- 经验：判断「能不能推」不要看 git 里有没有 migrations 改动，要**直接查 max(version) 与本地最大版本比对**；靠推断会产生假警报（本轮就误报过一次）。

### Step 1a · 四税计提接入手工凭证复核流（翻译层完成）

- 路线按用户拍板执行：不新建表、不重造界面，直接落到既有 `POST /api/posting/manual-requests` → `approve` → `post`（`app/accounting/review_policy_router.py` + `app/posting/manual_review.py`）。
- 新增 `nexus_backend/app/tax/accrual_posting.py`：`command_key_for(source)` 用 `workpaper_id:version:source_digest` 的 sha256 前 40 位做 `tax-accrual:` 键——同一底稿点两次是重放，换版本必换键；`manual_voucher(package, entries, accounts, ...)` 产出 `ManualVoucher` 载荷，每条分录带 `account_version`（对旧版本科目过账必须失败而不是落到改过的科目），借贷合计按整数分校验后不平衡即拒。
- `nexus_backend/tests/test_tax_accrual_posting.py` **6 passed**：金额格式化、键稳定性与版本敏感、缺身份拒绝、未知科目拒绝、空分录拒绝、不平衡拒绝。
- 已按用户核验修正两处：①`manual_voucher()` 先按 `(account_id, side)` 合并同科目同方向金额（附加税与印花税默认同借 `6403`，而 `ManualVoucher.unique_account_aux` 禁止同科目重复；同一科目同时出现在借贷两方则 409，不做净额抵销）；②`_money` 改用 `app.accounting.invariants.cents_to_money`，不经浮点。
- 用例增至 **8 passed**：新增「附加税+印花税同月计提合并 6403 并通过 `ManualVoucher(**payload)` 契约校验」与「同科目借贷两方被拒」；与 `test_tax_accrual.py` 合跑 **15 passed**。
- 未做：HTTP 入口（复用 `PostingService._accounts(conn, ledger_id, period)` 解析 code→id/version 后注入 `ManualReviewService.prepare`）、前端「生成计提手工凭证申请」按钮、E2E。

## Step 1a · 四税计提接入手工凭证复核流（完成）

- 工程状态：**完成**（HTTP 入口 + 前端按钮 + 后端集成测试 + Playwright E2E 全部落地；零新表、零新迁移）
- 外部状态：无需外部
- 与提示词要求的差异：一致（按要求复用 `PostingService._accounts(conn, ledger_id, period)` 解析 `code → (id, version)`，经 `accrual_posting.manual_voucher(...)` 构造 `ManualVoucher(**payload)` 后直接调 `ManualReviewService.prepare`，未新建第二套凭证入口）

### 新增入口 `POST /api/tax/accrual-manual-request`

- 体沿用 `FourTaxIn` 并扩展三个可选字段（`voucher_date` / `description` / `reason`，仍 `extra="forbid"`）；`voucher_date` 缺省取期间最后一天（`_period_end()`，`calendar.monthrange`，不用 SQLite 日期函数）。
- 科目版本按**期间**解析：`posting.db.transaction(actor=current)` → `_book(conn, actor, book_id)` 取 `ledger_id` → `_accounts(conn, ledger_id, book["period"])`；每条分录带 `account_version`，对旧版本科目过账会失败而不是落到改过的科目。
- 期间必须等于账套当前期间，否则 409 并同时报出期望值与请求值。
- 返回 `request`（待复核视图）+ `voucher{command_key,voucher_date,line_count,debit_cents,credit_cents}` + `surtax_basis` + `refund_note` + 固定 `posted:false`，文案写明「须由另一名有复核权的人批准后才能过账，本接口不会直接记账」。

### 接线过程中发现并修掉的四个真问题

1. **`accrual.accrual_lines(chart=...)` 过严**：它要求默认科目表**全部**存在，于是「本期没有印花税合同」的账套也会因为缺 `2221.04` 而无法计提附加税。改为不传 `chart`，由 `manual_voucher()` 对**真正要记的** code 逐个解析并点名缺失项（测试里这条以「没有 2221.04 也能计提」的断言固化）。
2. **`four_tax.package` 拿账载税额当利润**：`ledger_cents` 是底稿的**账载税额**（`variance = tax - ledger`），原代码在 `cumulative_profit_cents is None` 时用它当企业所得税计税基础——会记出一笔以「税额×25%」为基数的假所得税。改为缺利润直接 409（「底稿的账载税额不能代替利润」）。没有既有测试依赖旧的回落行为。
3. **附加税计征依据两套口径**：新增 `four_tax_package_for()` 统一 preview 与入账两个入口——显式 `surtax_items` 优先；否则取本期税务档案 `surtax_rate_bp`（正是底稿 `_ratio(tax, profile["surtax_rate_bp"])` 用的同一税率）并在 `surtax_basis` 里写明来源与档案 id；两者都没有时让包自身的守卫按自然顺序报错（先报缺已复核底稿，再报缺政策条目）。另加一道**一致性校验**：本次算出的附加税必须等于已复核底稿登记的 `surtax_cents`，否则 409「不能按两套依据入账」。
4. **计提面板此前是死面板**：`src/TaxAccrualPanel.tsx` 的 `Entry` 按 `{label,debit_cents,credit_cents}` 渲染，而 `accrual_lines()` 返回的是 `{account,side,cents,memo}`；且请求体固定 `surtax_items: []` → `engines.surtax` 必然 409「缺少附加税政策条目」。已按真实契约改为 `{account,side,cents,memo}` 渲染，请求体改为 `{book_id, period, cumulative_profit_cents}` 并由服务端回报计征依据。

### 前端

- `src/TaxAccrualPanel.tsx`：新增「本期累计利润（元）」输入（`yuanToCents()` 纯字符串解析成整数分，不经浮点；非法输入返回 `null` 使按钮不可用）、「生成计提手工凭证申请」按钮、「刷新申请状态」按钮（读 `/books/{id}/manual-review-requests`）、`AccrualRequestView` 展示申请号/状态/凭证日期/行数/借贷合计/附加税计征依据；固定提示「到「正式账簿 → 手工凭证办理」由另一名有复核权限的人批准后才能记账」。
- 已过账状态**不由请求状态推断**：`manual_review_requests.status` 只有 `pending|approved`，过账后仍是 `approved`，因此界面以 `posted_voucher_id`（来自列表视图，挂在 `vouchers.review_request_id` 上）判定「已过账」。
- 未新增任何 CSS 类（复用 `tax-accrual-preview` / `tax-accrual-note` / `tax-accrual-blocked`），组件仍只在 `TaxWorkpaperPanel` 的懒加载分块内：`initial` 闭包仍只有 `assets/index-*.js`，体积预算未被触碰。

### 测试

| 层 | 文件 | 结果 |
| --- | --- | --- |
| 翻译层单测 | `nexus_backend/tests/test_tax_accrual_posting.py` | **10 passed**（原 8 + 两种来源写法同键 + 凭证日期越期拒绝） |
| HTTP 集成 | `nexus_backend/tests/test_tax_accrual_posting_http.py` | **2 passed** |
| 四税包 | `nexus_backend/tests/test_four_tax_package.py` | **6 passed**（+1 缺利润拒绝） |
| 前端单测 | `src/tax-accrual-panel.test.ts` | **12 passed** |
| E2E | `e2e/tax-accrual-posting.spec.ts` | **1 passed (39.2s)** |

- HTTP 集成用例覆盖：首次成功（`surtax_basis.source=tax_profile`、`rate_bp=1200`、借=贷=25360 分、4 行、凭证日期=期间末）→ 重复点击幂等（同一 request id、同一 command_key、账套内仅一条 `tax-accrual:` 申请）→ 制单人自审 **403** 且申请状态与版本不变 → 独立复核人批准（`independent=true`）→ 过账（`voucher.review_request_id == request.id`，正是月结 `unposted_manual` 门禁用的关联键）→ 试算平衡 `6403=360 / 6801=25000 / 2221.03=-360 / 2221.05=-25000`。
- 负向用例：跨期间 409、非法日期 422、科目表无 6801 时 409 且点名 `6801`、显式附加税条目与底稿登记值不一致 409、缺累计利润 409。
- E2E 链路：真实身份建账 → 生成并独立复核增值税底稿 → 页面填累计利润（1000 元）→ 预览出现 6403/2221.03 与借 253.60 → 生成申请（`posted:false`、借=贷、4 行、`surtax_basis` 为档案 12%）→ 连点两次仍只有一条 `tax-accrual:` 申请 → 另一身份经 API 批准后点「刷新申请状态」显示「已复核，待过账」→ 过账后再刷新显示「已过账」。

## Step 1b · Batch 06 B1 规费批次受控记账（完成）

- 工程状态：**完成**
- 外部状态：无需外部
- 与提示词要求的差异：一致（接入既有 `POST /api/books/{book_id}/drafts/{draft_id}/post` → `PostingService.post_draft`，未新增记账通道）

### 后端

- `nexus_backend/app/tax/fees.py::_view` 新增 `draft{id,status,version,posted_voucher_id,voucher_no}` 投影：`post_draft` **不改** `drafts` 状态，只在 `voucher_sources` 留下 `source_kind='draft'` 的关联，界面要判断「这张草稿是否已经记过账」只能从这张表读——否则按钮会一直可点，点了才报 409。
- 记账本体完全复用既有服务：`draft.status ∈ {approved, exported}`（未复核 → 409「只有已审核或已导出的草稿才能正式记账」）、`expected_version` 与草稿版本一致、`_book(write=True)` 拒绝已关闭/已内锁期间（403）、`command_key` 幂等重放、`voucher_sources` 保证一张草稿只生成一张凭证。

### 前端 `src/TaxFeeBatchPanel.tsx`

- 新增纯函数 `canPostBatch(batch)`：仅当 `status='reviewed'` 且 `stale=false` 且 `draft.status='approved'` 且无 `posted_voucher_id` 时才放行；导出以便单测直接断言门禁（按钮的 `disabled` 用的就是它）。
- 已复核批次展示「生成正式凭证」按钮（`command_key = fee-batch:{batch.id}:{draft.version}`，同一批次同一草稿版本连点是重放；`voucher_date = monthEnd(period)`）；已记账后改为只读提示「已生成正式凭证第 N 号；一张草稿只能记账一次，记错须另开冲销凭证」。

### 测试

- `nexus_backend/tests/test_tax06_fee_batch_posting.py` **3 passed**：①未复核草稿记账被拒且不留下任何 `vouchers` 行；②复核后记账得到借=贷=22500 分（=批次总额）、`command_key` 重放返回同一凭证、换键再记被拒（`已经生成过`）、视图投影 `posted_voucher_id`/`voucher_no` 正确；③期间关闭（`restore_period_state`，与内部锁期同一个 `_book(write=True)` 门禁）时记账被拒且无凭证落库。
- `src/tax-fee-batch-panel.test.ts` **6 passed**：已复核放行、未复核阻断、来源已变化阻断、草稿未批准阻断、已记账阻断、已失效 / 草稿缺失 / 空批次阻断。

## Step 1 门禁与回归（真实输出）

| 命令 | 结果 |
| --- | --- |
| `.venv/Scripts/python.exe scripts/check_test_env.py` | `environment matches lock`（36 packages），exit 0 |
| `.venv/Scripts/python.exe scripts/check_migration_idempotency.py` | exit 0（本步零新迁移；312/313/314/316/318 的 `EXECUTE`/`DO $$` 仍按既有白名单记「需专检」） |
| `pytest nexus_backend/tests/test_migration_structure.py test_pg_sql_hygiene.py test_write_scope_baseline.py -q` | **12 passed** |
| `pytest nexus_backend/tests -q -k "tax or posting or fee"` | **367 passed, 98 skipped, 1 error**（error 为 `test_task_evidence_postgres.py` 需 `ACCOUNTING_ADMIN_DATABASE_URL`，本机无 PG DSN，属环境缺失且与本次改动无关） |
| `pytest` 新增/相关套件 | `test_tax_accrual_posting(10)` + `test_tax_accrual_posting_http(2)` + `test_tax06_fee_batch_posting(3)` + `test_tax_api(3)` = **18 passed** |
| `ruff check nexus_backend/app services/api_gateway scripts` | `All checks passed!` |
| `ruff format --check nexus_backend/app services/api_gateway scripts` | `462 files already formatted`（与 CI 口径一致；`nexus_backend/tests` 全仓历史上未格式化，本步只格式化新增/改动的测试文件，未做全仓格式化） |
| `npm run typecheck` | exit 0 |
| `npm run lint` | exit 0 |
| `npx prettier --check`（改动文件） | 全部通过 |
| `npm test`（全量 vitest） | **163 files / 1460 passed** |
| `npm run build` | ✓ built in 3.62s（`TaxWorkpaperPanel-*.js` 138.31 kB） |
| `npm run test:frontend-budget` | `status: passed`、`violations: []`；initialJs 567,460/570,000、initialGzip 175,876/176,000、initialCss 98,906/110,000、homeJs 601,201/625,000、homeGzip 189,236/195,000、homeCss 144,715/145,000（未放宽任何上限；initial 闭包仍只有 `assets/index-*.js`，本次前端改动全在懒加载分块内） |
| `E2E_FRONTEND_MODE=preview npx playwright test e2e/tax-accrual-posting.spec.ts` | **1 passed (39.2s)** |

- 本机环境小坑（记录备查）：`npm run build` 在系统临时目录被安全软件占用时会报 `[vite:esbuild-transpile] remove ... Access is denied`。改用 `TMP/TEMP=<仓库>/.tmp-build` 即通过；这是本机 Windows 临时目录锁的问题，不是代码问题（同一命令在 E2E 的 preview 服务里也成功过三次）。

## Step 1 剩余未决项

- 计提入账目前只有「按已复核底稿 + 会计输入的累计利润」一条路径；累计利润仍靠人工输入，尚未从报表模块（利润表）自动取数。
- 印花税计提需要调用方显式传 `contracts`（合同幂等键 + 税目），面板暂未提供合同录入，故当前 UI 生成的计提里印花税恒为 0。
- 附加税与底稿登记值的一致性校验会在「档案税率在底稿生成之后被改过」时拒绝入账——这是有意的（宁可拒绝也不按两套依据记账），但需要运维口径知道：改档案后应重生成并重复核底稿。

## Step 2 · Batch 05 出纳与付款审批台闭环（完成）

- 工程状态：**完成**（角色/RLS、银行流水落库与核销绑定、懒加载出纳台与 E2E 全部落地；新增迁移 PG `321`/`322`、SQLite `228`/`229`）
- 外部状态：无需外部（真实银行前置机/云 KMS 仍不在本阶段）
- 与提示词要求的差异：一致

### 2a 出纳（cashier）与资金审批人（treasury_approver）接入访问控制与 RLS

**为什么不是往 `book_access.role` 里加两个值**：账套访问角色（editor/reviewer/viewer）回答「能不能看/改这本账」，付款岗位回答「能不能发起/放款」，两条轴正交。往 `book_access` 加值还要重建 SQLite 的核心身份表（CHECK 无法 ALTER），风险远大于收益。因此新增独立表 `book_payment_roles`：

- **迁移** `nexus_backend/migrations/postgres/321_b05_payment_roles.sql` / `sqlite/228_b05_payment_roles.sql`（均 `-- compatibility: incremental`）：`(book_id,user_id,role)` 唯一、`role ∈ cashier|treasury_approver`、`state ∈ active|revoked`、`CHECK(state='active' OR (revoked_by IS NOT NULL AND revoked_at IS NOT NULL))`；PG 侧 `ENABLE + FORCE RLS`、三条策略（读 `book_allowed(book_id)`；写与撤销要求 `firm_manager(firm_id) OR company_manager(company_id)`，插入还要求 `granted_by=actor_user()`）、`REVOKE ALL ... FROM PUBLIC/worker/system/projection` 后显式 `GRANT SELECT,INSERT,UPDATE TO accounting_app`、挂 `batch2_company_write_gate` 触发器（该表带 `company_id`）、**禁删触发器**（撤销只能改状态）。SQLite 侧镜像同表同约束 + 禁删触发器。
- **RLS 谓词函数**：`accounting.book_payment_role(book_id, role)` 与 `accounting.book_payment_member(book_id)`（SECURITY DEFINER，`REVOKE` 后仅授权 `accounting_app`）。`payment_orders` 读/插/改与 `payment_events` 读/插五条策略改为「账套权限 **或** 付款岗位」：制单只认 cashier（或账套编辑者），状态推进认任一付款岗位，`maker_user_id=actor_user()` 等既有约束不变。
- **Python 授权层** `app/accounting/access.py`：新增 `payment_roles(conn, actor, book_id)` 与两个能力 `pay` / `approve_payment`。`pay = edit or (read and cashier and 主体身份)`；`approve_payment = review or (read and treasury and 复核主体 and 未启用管理者分离)`。岗位**不**放宽账套编辑/复核权，且仍需要一条账套访问授权（否则 `read=false`，连书都看不到）。
- **付款服务** `app/treasury/service.py`：制单/提交/推进改用 `pay`，放款改用 `approve_payment`。修掉一个真漏洞：`_move()` 原先只校验 `read`，被撤销的出纳仍能推进别人的付款；现在提交/送银行/记退回都要 `pay`。
- **岗位授权服务** `app/treasury/roles.py`：`list/grant/revoke`，管理者专属；被授权人须已有账套访问（否则 409「请先授予账套访问再加付款岗位」）；重复授权返回原记录，撤销后再授权是**恢复同一条**（不会留下两条互相矛盾的有效授权），每次授权/撤销写 `audit_events`。
- **HTTP**：`GET /api/treasury/payment-roles?book_id=`、`PUT /api/treasury/payment-roles/{user_id}`、`POST /api/treasury/payment-roles/{role_id}/revoke`、`GET /api/treasury/payment-desk?book_id=`（岗位 + 能力 + 指令清单，只读投影；**没有**改 `payment-orders` 列表的返回形状，避免既有调用方静默读错）。

### 2b 银行导入落库与 `fee_receivable_allocations.bank_statement_line_id` 接线

- **迁移** `322_b05_bank_statement_persist.sql` / `229_b05_bank_statement_persist.sql`：`bank_statement_lines` 增 `source_id`（指向同一次导入生成的正式来源行）与 `external_ref`（`账号|流水号`），两个部分唯一索引保证「同一账套同一笔流水只落一次」「一个来源行只对应一条流水行」。
- **落库** `app/treasury/imports.py::BankStatementImportService.confirm` + `POST /api/treasury/bank-lines/import`：CSV 归一化后写入 `bank_statement_lines`（方向转符号、`external_ref` 幂等、坏文件整批 400 且一行都不落）。落库状态是 `unmatched`——流水行是银行事实，勾对是会计动作，不能为了好看直接标 matched。
- **核销绑定** `app/tax/fees.py`：`allocate_receivable(..., bank_statement_line_id=None)`；显式 id 必须属于同一账套（否则 404）、非 `excluded`、且流水行金额符号与银行来源一致、月份同期间（否则 409）；未给 id 时只接受「已由该银行来源绑定过」的那一行（`source_id` 反查），**同一笔到账有两份候选时留空而不猜**。写入列并把 `bank_statement_line_id` 加进分配视图。HTTP 侧 `FeeAllocationWrite` 增同名可选字段。

### 2c 懒加载出纳付款审批台 + E2E

- 新增 `src/CashierPaymentDesk.tsx`：读 `/api/treasury/payment-desk`，展示我的岗位与两项能力、创建付款指令（`idempotency_key` 由 账套+收款人+账号+金额 生成，连点即重放）、提交审批、批准放款；纯函数 `canApprove()` / `canSubmit()` 是按钮的唯一判据（本人制单不出现「批准放款」，而显示「本人制单，须由他人审批」）。
- 挂载：`src/FormalBooksWorkbench.tsx` 里用 `lazy()` + `<details className="review-section">`（沿用既有样式，**未新增任何 CSS 条目**）；`initial` 闭包仍只有 `assets/index-*.js`，本次前端改动全在懒加载分块内。
- `src/types.ts` 未改（`BookPermissions` 仍是 edit/review/export/manage，新能力只在本组件内从付款台接口读），避免影响其他调用方。

### 测试

| 层 | 文件 | 结果 |
| --- | --- | --- |
| 岗位/RLS/能力 | `nexus_backend/tests/test_b05_payment_roles.py` | **7 passed** |
| 银行流水落库与核销绑定 | `nexus_backend/tests/test_b05_bank_import_persist.py` | **5 passed** |
| 既有付款套件回归 | `test_b05_payment_service/http/tables/adapters` | **25 passed** |
| 规费套件回归 | `test_tax06_fees.py` | **21 passed** |
| 前端单测 | `src/cashier-payment-desk.test.ts` | **5 passed** |
| E2E | `e2e/cashier-payment-desk.spec.ts` | **1 passed (34.7s)** |

- 岗位用例覆盖：出纳能制单不能放款（`pay=true/edit=false/review=false/approve_payment=false`）；出纳与资金审批人是两个岗位且资金审批人不能制单；管理者分离政策下管理者不能靠岗位给自己解锁放款（纯函数断言）；撤销后**下一次请求**即失效且记录不删、再授权恢复同一条；非管理者（含账套编辑者）不能发岗位；没有账套授权的人即使被写进岗位表也读不到这本书。
- HTTP 用例覆盖：管理者发岗位 200 → 出纳台 `roles=['cashier']`、`permissions={pay:true,approve_payment:false}` → 出纳制单 200 → 自审 403 → 撤销后 `pay=false` 且制单 403 → 非管理者发岗位 403。
- 银行用例覆盖：CSV 落库两条并返回 `inserted=2`、重复导入 `inserted=0/skipped=2`（表内仍是 2 行）、坏方向整批 400 且零落库；核销显式绑定校验（金额不符 409、跨账套 404、被拒后不留分配行）、已绑定来源的流水行自动挂接。
- E2E 链路：真实身份建账 → 管理者把 cashier 授予独立复核身份 → 出纳身份在页面创建 1200.00 付款指令并提交（页面出现「本人制单，须由他人审批」，且**没有**批准按钮）→ 切换到管理者身份批准放款 → 页面显示「已批准」→ 接口复核 `approver_user_id != maker_user_id` 且状态为 `approved`。

### Step 2 门禁与回归（真实输出）

| 命令 | 结果 |
| --- | --- |
| `scripts/check_migration_idempotency.py` | exit 0（新增 321/322 与既有 316/318 同样记「dynamic_ddl 需专检」，该标记由 `EXECUTE FUNCTION` 字样触发，属已知口径） |
| `pytest test_migration_structure.py test_pg_sql_hygiene.py test_write_scope_baseline.py -q` | **12 passed** |
| `ruff check / format --check nexus_backend/app services/api_gateway scripts` | 全部通过（`464 files already formatted`） |
| `npm run typecheck` / `npm run lint` | exit 0 |
| `npm test`（全量 vitest） | **163 files / 1465 passed** |
| `npm run build` | ✓ built in 3.52s |
| `npm run test:frontend-budget` | `status: passed`、`violations: []`；initialJs 567,460/570,000、initialGzip 175,865/176,000、initialCss 98,906/110,000、homeJs 601,201/625,000、homeGzip 189,226/195,000、homeCss 144,715/145,000（未放宽任何上限） |
| `E2E_FRONTEND_MODE=preview npx playwright test e2e/cashier-payment-desk.spec.ts` | **1 passed (34.7s)** |

## Step 2 剩余未决项

- **PG 实例实跑未做**：321/322 只过了静态结构与 SQL 卫生检查，本机无 DSN。新增的 `book_payment_role`/`book_payment_member` 谓词与五条付款策略改写，必须在真实 PostgreSQL 上跑一次「出纳写入 / 撤销后拒绝 / 管理者发岗位」才算实证。
- 银行导入目前只落 **流水侧（CSV）**；电子回单（receipts）没有独立落表，仍只做「五字段唯一匹配」的预览与核销绑定。导入向导（`import_wizard`）确认时**没有**同时生成 `bank_statement_lines`——那会给每个既有导入流程新增待勾对流水，进而改变月结银企对账结果；这条要连同「勾对工作台」一起做，不能只补一半。
- `fee_receivable_allocations.bank_statement_line_id` 只在调用方显式给 id 或流水行已由该银行来源绑定时写入；**不会**按金额/日期自动挑一条（同一到账两份候选时留空）。
- 出纳台尚未提供「银行结果推进（送银行/成功/退票）」与付款批量的前端入口，后端 `advance` 已按 `pay` 能力受控。

### Step 2 追加：既有断言的同步与最终回归

- `permissions` 字典新增 `pay` / `approve_payment` 两键后，三处**精确相等**断言同步扩展到新契约（不是放宽）：
  - `nexus_backend/tests/test_access_scope_policy.py::test_all_firm_read_is_explicit_and_covers_new_books`：新账套上 `pay=False`/`approve_payment=False`；
  - `nexus_backend/tests/test_directory.py::test_member_identity_and_book_authorization_are_both_required`：账套编辑者 `pay=True`、`approve_payment=False`；
  - `nexus_backend/tests/test_n04_tax_contract.py` **未改**：`/api/tax/context` 本来就只投影 `edit/review/export/manage` 四键（`app/tax/context.py:77`），付款岗位不进税务上下文。
- 口径说明（有意保留的既有行为）：`pay = edit or cashier`，即**账套编辑者仍可制单**（与改造前一致），出纳岗位解决的是「没有账套编辑权的人如何合法制单」；`approve_payment` 才真正收紧——改造前任何人只要有读权限且不是制单人都能批款，现在必须同时具备复核权或资金审批人岗位。
- 最终相关回归：`test_n04_tax_contract.py + test_directory.py + test_access_scope_policy.py` **42 passed**；`test_b05_payment_*` **25 passed**；`test_tax06_fees.py` **21 passed**；迁移三件套 **12 passed**；`test_b05_payment_roles.py` **7 passed**；`test_b05_bank_import_persist.py` **5 passed**。
- 运行方式提醒：`pytest.ini` 的 `pythonpath = nexus_backend` 要求从**仓库根目录**跑；在 `nexus_backend/` 目录里跑会让 `from tools... import` 的既有模块收集失败（20 个 collection error，与本次改动无关）。
- 已知与本步无关的环境性失败：任何需要 `ACCOUNTING_ADMIN_DATABASE_URL` / `ACCOUNTING_TEST_POSTGRES_URL` 的 PG 用例在本机无 DSN 时报 **error**（不是 skip），例如 `test_access_scope_postgres.py`、`test_task_evidence_postgres.py`。

### Step 1 + Step 2 独立验收加固与远端 PostgreSQL 同步（封账基线）

1. **查询预算修复（`nexus_backend/app/accounting/access.py`）**：将 `AccessControl.book()` 中额外调用 `payment_roles()` 产生的第 2 条 SQL 合并为单条 `SELECT` 内的两个 `EXISTS(...)` 子查询（`_access_pay_cashier` / `_access_pay_approver`），修复 `test_stage25_assistant_efficiency.py` 与 `test_stage19_request_reads.py` 的请求语句数预算回归。
2. **PostgreSQL 321 纵深加固（`nexus_backend/migrations/postgres/321_b05_payment_roles.sql`）**：在 `accounting.book_payment_role()` 中补齐 `b.company_id = r.company_id` 跨法人绑定校验；将两个 `SECURITY DEFINER` 函数的 `REVOKE ALL` 显式覆盖至 `PUBLIC, accounting_worker, accounting_system, accounting_projection`。
3. **恢复授权审计补齐（`nexus_backend/app/treasury/roles.py`）**：在撤销后重新授予（`state='active'` 恢复分支）补写 `payment_role.grant` 审计事件，与首次授予保持一致。
4. **远端 Supabase 生产库（`qgzketopnkzquotddwha`）同步实证**：已依次应用 `321_b05_payment_roles.sql` 与 `322_b05_bank_statement_persist.sql` 并写入 `accounting.schema_migrations`（`total_migrations=309`、`latest_version=322`），实测验证 `book_payment_roles` 存在且 `accounting_app` 仅具备 `SELECT/INSERT/UPDATE`（禁 `DELETE`）、`batch2_company_write_gate` 触发器生效、`bank_statement_lines.external_ref` 就绪。


## Step 3a · Batch 06 三家行业专用台账处理器（完成）

- 工程状态：**完成**（三家处理器 + 患者隐私红线 + 双库迁移 `323`/`230` + 13 例测试全部落地）
- 外部状态：无需外部
- 与提示词要求的差异：一致（沿用 `contracts.py` 既有 `PROCESSORS`/`CAPABILITIES` 枚举，未重命名任何既有契约）

### 迁移（双库，均 `-- compatibility: incremental`）

- `nexus_backend/migrations/postgres/323_b06_industry_drafts.sql` / `sqlite/230_b06_industry_drafts.sql`：新增 `fsc_industry_drafts`，用 `processor_code + kind` 判别三种产物（`property_amortization` / `grocery_movement` / `trade_purchase_match` / `trade_cost_carry` / `hospital_department_summary`），`UNIQUE(envelope_id,contract_id,processor_code,kind)` 保证同一信封同一契约同一产物只有一份草稿；PG 侧 `ENABLE + FORCE RLS`、读写分离策略（读 `company_visible`，写还要求 `created_by=actor_user()`）、`REVOKE ALL ... FROM PUBLIC, accounting_worker, accounting_system, accounting_projection` 后显式 `GRANT SELECT,INSERT TO accounting_app`、挂 `batch2_company_write_gate`；SQLite 侧禁改禁删触发器（草稿只能追加）。
- **为什么用一张表而不是三张**：三家的产物形状完全一致（绑定信封 + 契约 + 行号 + fingerprint 的可复核投影），拆三张只会把同一份 RLS/策略/写门抄三遍、把漏挂一处门禁的风险放大三倍；`processor_code` + `kind` 的 CHECK 白名单与唯一键给出的约束是等价的。这是取舍，不是图省事。

### 三家处理器（`nexus_backend/app/fsc/industry.py`）

共同纪律沿用 `AdvertisingRevenueService`：只消费**已确认**信封、必须有一条**生效**的同处理器契约、产物带 `fingerprint`（信封指纹 + 契约指纹 + 源版本 + 行明细）、同键重放、信封或契约失效即 `stale`、全程整数分、不写凭证不生成应收。

1. **物业与食材集采 `PropertyServiceDrafts`**：
   - `property_amortization`：只收 `target_kind='service_contract'` 的摊销行，按合同汇总金额并保留来源行号与最大摊销月数；缺目标/负金额/非法月数 422。
   - `grocery_movement`：按动作（收货/退货/价差）分组，退货取负号；收货与退货必须 `quantity_milli>0` 且 `unit_price_cents` 为非负整数分，并校验 `amount_cents ≈ round(单价×数量/1000)`（容差 1 分）——**不允许用金额倒推单价**；价差行可数量为 0。跨期行在信封入口即 422。
2. **贸易存货 `TradeInventoryDrafts`**：
   - 三单核对：同一 `match_key` 必须同时有 contract / inbound（可多批到货，按合计参与核对）/ invoice / outbound；缺一类 409「三单核对不全」，合同/入库合计/发票的数量或金额不一致 409「三单核对不一致」，出库数量必须 > 0。
   - 成本结转：计价方法**只能取自生效契约的 `evidence_policy.inventory_costing_method`**，取值必须为 `moving_weighted_average` / `fifo`，否则**直接 409**「企业尚未批准存货计价方法」并且不留下任何草稿（有断言）。`moving_weighted_average` 按入库合计加权，`fifo` 按入库批行号先进先出逐批取数，出库超过入库合计 409。
3. **内部医院 `HospitalSummaryDrafts`**：只收 `target_kind='department'` 的科室汇总，按科室汇总金额与工作量；非科室目标 422、负金额 422；生成前对每一行再跑一次隐私拦截（第二层）。

### 患者隐私红线（`nexus_backend/app/fsc/privacy.py`）

- 拦截器 `assert_no_patient_privacy()` 挂在 **`FscImportService.import_envelope()` 的第一行**——早于任何事务与任何写入，因此不存在「先落盘再回滚、日志/审计已经带过敏感值」的窗口；`HospitalSummaryDrafts.generate()` 再校验一次（纵深防御，覆盖历史数据或迁移脚本绕过入口的情形）。
- 判定口径：字段名命中 `patient_name/patient_id/id_card/medical_record/visit_card/diagnosis/insurance_detail/患者/病人/病历/就诊卡/身份证/医保明细/诊断`（聚合计数类后缀 `_count/_total/_sum/quantity/amount/cents` 豁免，避免误伤「患者人次」这类汇总数）；值命中 18 位公民身份号码正则（前后不紧邻数字）或 `身份证号/病历号/就诊卡号/患者姓名/医保明细` 等字样即拒收。命中位置写进错误文案，**命中值一律不回显**。
- 拒收为 **422 整包**，且对**任何来源**都生效——把患者明细伪装成 `property_service` 来源同样被拦。

### 测试（`nexus_backend/tests/test_fsc06_industry_processors.py` **13 passed**）

- 物业正例：摊销两合同合计 500.00、食材三动作（价差 +15.00 / 收货 +200.00 / 退货 −50.00）净额 165.00，重复生成 `replayed=True` 且 id 与 fingerprint 一致。
- 物业阻断：负单价 422「单价」、金额与单价×数量不一致 422、跨期行在信封 422；未确认信封 409、无生效契约 409。
- 贸易：未批准计价方法 409「计价方法」且草稿表为空；同一批数据下 `moving_weighted_average=750.00` 与 `fifo=800.00` 各自正确（先建契约再失效、逐个方法验证）；缺发票 409「三单核对不全」、发票金额差 1 分 409「不一致」。
- 医院：两科室汇总（内科 2000.00/50、外科 600.00/15）合计 2600.00；绕过入口手工插入含「患者姓名」的已确认信封后，处理器仍 422 且文案不含患者姓名。
- 隐私强负向 5 组参数化（`patient_name` / `id_card` / `病历号` / `就诊卡号` / 摘要里的身份证号）：均 422、文案含「整包已拒收」但**不含患者姓名与身份证号**、`fsc_import_envelopes` / `fsc_import_envelope_events` / `audit_events` / `fsc_industry_drafts` 四张表计数与调用前完全一致（0 落盘）、`caplog` 中不含患者姓名/身份证号/病历号（0 日志泄漏）。
- 伪装来源：把身份证号写进 `property_service` 来源的备注，同样 422 且零落盘。

### Step 3a 门禁（真实输出）

| 命令 | 结果 |
| --- | --- |
| `pytest test_fsc06_industry_processors.py` | **13 passed** |
| `pytest -k fsc`（全量 FSC 域回归） | **42 passed, 15 skipped**（skip 为无 PG DSN 的 PG 用例） |
| `pytest test_migration_structure.py test_pg_sql_hygiene.py test_write_scope_baseline.py` | **12 passed** |
| `ruff check nexus_backend/app services/api_gateway scripts` | `All checks passed!` |
| `ruff format --check nexus_backend/app services/api_gateway scripts` | `466 files already formatted` |

## Step 3b 精确起点（未开始）

1. **B8 只读统一链路视图**：在 `app/tax/fees.py` 旁新增只读聚合（建议 `fee_chain(actor, book_id, batch_id)`），一线串联 `tax_fee_batches → fee_receivable_items → fee_receivable_allocations（含 317 的 bank_statement_line_id）→ bank_source 的 draft → voucher_sources(source_kind='draft') 对应正式凭证 → 当期报表快照`，逐段给出「有/缺失」与 evidence ref；HTTP 建议 `GET /api/tax/books/{book_id}/fee-chain/{batch_id}`；前端在 `src/TaxFeeBatchPanel.tsx`（已是懒加载块）里用 `<details>` 折叠查看，**复用 `tax-fee-*` 既有类，禁止新增首屏 CSS**。
2. **PLT05-C/D/E**：先读 `docs/pilot/PLT05_EXTERNAL_ROUNDTRIP.md` 与 `app/integrations/service.py`；C 为「仅确认失败且仍有权限的凭证可重走预览补导」，D 为「状态未知禁止重发」，E 为「跨期/错法人拦截 + 集团汇总与月结预检接入外部正式账三态提示」。
3. Step 4（Batch 02 交互、Batch 07 三条配额接线、Batch 08 告警与 Runbook）尚未开始，起点见本文档 Step 4 相关条目与 `docs/upgrade_85/UPG02/07/08`。

## Step 3b-1 · Batch 06 B8 规费链路只读统一视图（完成）

- 工程状态：**完成**
- 外部状态：无需外部
- 与提示词要求的差异：一致（只读聚合 API + 懒加载前端折叠入口，未增加首屏 JS/CSS）

### 后端 `nexus_backend/app/tax/chain.py`

- `FeeChainView.chain(actor, book_id, batch_id)` 在**一个只读事务**里串联六段：
  `tax_fee_batches`（批次）→ `fee_receivable_items`（驾驶员应收）→ `fee_receivable_allocations`
  （核销，含 317 的 `bank_statement_line_id`）→ `bank_statement_lines`（流水行）→
  `sources JOIN drafts LEFT JOIN voucher_sources/vouchers`（正式凭证）→ `report_runs`（报表快照）。
- 每一段返回 `{key,label,state:present|missing,count,items,reason}`；缺失段必须写清原因
  （「批次尚未复核，驾驶员应收还未生成」/「还没有登记任何银行到账核销」/「核销没有绑定银行流水行…」/
  「银行来源草稿尚未记账（或尚未生成）」/「本期还没有报表运行记录」），并在 `gaps` 里汇总。
  另返回 `pending_drafts`（未记账的银行草稿清单）与固定 `readonly: true`。
- **不做推断连接**：段与段之间只用真实外键/证据引用（`bank_source_id`、`bank_statement_line_id`、
  `voucher_sources.source_kind='draft'`），不按金额相近「猜」匹配——那会把两笔相似业务连成假链路。
- HTTP：`GET /api/tax/books/{book_id}/fee-chain/{batch_id}`；跨账套批次 404。

### 前端（`src/TaxFeeBatchPanel.tsx`，已在懒加载块内）

- 批次摘要下新增 `<details className="tax-fee-history">`「查看业务链路（只读：批次 → 应收 → 银行核销 → 凭证 → 报表）」，展开时才请求链路；表格列出「环节 / 状态 / 条数 / 说明」，并在标题行用 `chainSummary()` 给出「N/M 段已就绪，缺 K 段：…」；未记账银行草稿单列。
- 纯函数 `segmentState()` / `chainSummary()` 导出以便单测；**零新增 CSS**（复用 `tax-fee-history` / `tax-fee-note` / `tax-fee-table-scroll`），生产包 `initial` 闭包不变。

### 测试

| 层 | 文件 | 结果 |
| --- | --- | --- |
| 后端链路 | `nexus_backend/tests/test_b06_fee_chain.py` | **3 passed** |
| 前端单测 | `src/tax-fee-batch-panel.test.ts` | **8 passed**（新增段状态与缺失摘要两组） |

- 链路用例：六段顺序与状态、核销绑定的 `bank_statement_line_id` 出现在流水行段、未记账时凭证段 `missing` 且 `pending_drafts` 点名、`gaps` 恰为 2 条；银行草稿经独立复核并 `post_draft` 后凭证段转 `present` 且 `pending_drafts` 清空；跨账套 404。
- 顺带修正夹具：`test_tax06_fee_batch_posting.py::_frozen_opening` 补上 `1002` 科目映射（原夹具只映射 1122/6001，导致银行草稿无法记账；只增映射，不改任何断言）。

### Step 3b-1 门禁（真实输出）

| 命令 | 结果 |
| --- | --- |
| `scripts/check_migration_idempotency.py` | exit 0 |
| `scripts/check_secrets.py` | `no secrets found` |
| `uvx ruff==0.17.0 check nexus_backend/app services/api_gateway scripts` | `All checks passed!` |
| `uvx ruff==0.17.0 format --check nexus_backend/app services/api_gateway scripts` | `467 files already formatted` |
| `pytest test_stage25_assistant_efficiency.py test_stage19_request_reads.py` | **14 passed**（AccessControl.book 仍为单条 SELECT，未触发 SQL 预算回归） |
| `pytest test_b06_fee_chain.py test_tax06_fee_batch_posting.py` | **6 passed** |
| `pytest test_migration_structure.py test_pg_sql_hygiene.py test_write_scope_baseline.py` | **12 passed** |
| `npm run typecheck / lint / build` | 全部 exit 0（✓ built in 3.54s） |
| `npm test` | **163 files / 1467 passed** |
| `npm run test:frontend-budget` | `status: passed`、`violations: []`（initialJs 567,460/570,000、initialGzip 175,881/176,000、initialCss 98,906/110,000、homeJs 601,201/625,000、homeGzip 189,247/195,000、homeCss 144,715/145,000） |

## Step 3b-2 精确起点（未开始）

**PLT05-C/D/E**（见 `docs/pilot/PLT05_EXTERNAL_ROUNDTRIP.md` 与 `app/integrations/service.py`）：

1. **C**：仅「确认失败 + 当前仍有权限」的凭证可重走预览补导；重走前重新取权限与来源指纹（撤权后立即 403/404）。
2. **D**：状态 `unknown` 一律禁止重发，必须人工核定回执终态（现有 N03 围栏需与预览补导入口打通，避免从补导旁路绕过）。
3. **E**：跨期与错法人严格拦截；集团汇总与月结预检接入「外部正式账三态」（未接入 / 已接入一致 / 已接入有差异）提示。
4. 每项都要补单测与回归；参照既有 `test_plt05_return_import.py` / `test_plt05_return_http.py` 的断言风格。

## Step 4 精确起点（未开始）

- **4a Batch 02**：`src/WorkspaceContextBar.tsx` 切换单位/期间要接入「未保存离开拦截 + 在途请求中止」；逐页清理与顶部重复的月份输入（需在台账列出逐页移除/保留清单）；E2E 覆盖延迟响应防串数、不可逆锁期二次确认、`A` 键 400ms 防误触。
- **4b Batch 07**：`QuotaService.ensure_daily_jobs`（异步入队）、`ensure_storage`（原件上传）、`ensure_concurrent`（请求级）三条接线，沿用「事务内校验 + 结构化告警日志、不写审计表」的既有取舍。
- **4c Batch 08**：新增 `app/observability/alerting.py`（Webhook/钉钉/企业微信负载、指纹去重、静默窗口、退避重试，测试只用本地桩）+ `docs/ops/PRODUCTION_RUNBOOK.md`（500 故障秒级定位、连接池打满与慢查询、银行/税局 unknown 冻结、PG 备份还原与跨版本回滚检查表）。

### Step 3a + Step 3b-1 独立验收加固与远端 PostgreSQL 同步（封账基线）

1. **隐私拦截器安全漏洞修复（`nexus_backend/app/fsc/privacy.py`）**：
   - 修复原实现中“敏感词（如 `医保明细`）若同时伴随 15 位数字会被错误 `continue` 放行”的绕过漏洞，以及 15 位旧版身份证号在身份键语境下未被拦截的漏检问题；
   - 收紧键名后缀豁免，仅对宽泛词（`patient/患者/病人`）的纯数字计数后缀（`count/qty/quantity`）放行，强标识词（身份证/病历/就诊卡/诊断/医保明细）与非数字取值一律不豁免；补齐列表内原始字符串元素扫描；新增 4 组绕过负向测试 + `patient_count=30` 合法计数正例。
2. **贸易存货移动加权法负库存阻断（`nexus_backend/app/fsc/industry.py`）**：`moving_weighted_average` 分支补齐 `required > total_quantity` 拦截（409），与 `fifo` 分支一致禁止超库存结转，并新增回归用例。
3. **B8 规费链路视图衔接 B1 批次过账凭证（`nexus_backend/app/tax/chain.py`）**：`vouchers` 段同时串联 `batch.draft_id`（`source_role="fee_batch"`）与银行核销来源草稿（`source_role="bank_receipt"`）过账生成的正式凭证，保留 `pending_drafts` 既有口径，新增 B1 过账可见性测试。
4. **PostgreSQL 323 纵深加固与远端 Supabase 生产库（`qgzketopnkzquotddwha`）同步实证**：
   - `nexus_backend/migrations/postgres/323_b06_industry_drafts.sql` 补齐 6 条外键约束，将 `SELECT`/`INSERT` RLS 策略收紧至 `firm_id=actor_firm() AND company_visible(company_id)` + `book_allowed(book_id)` / `book_review_allowed(book_id)`，并补齐 `fsc_industry_drafts_append_only` 禁改禁删触发器（与 SQLite `230` 对齐）。
   - 已应用至远端生产库并实测验证：`total_migrations=310`、`latest_version=323`、`checksum=9dd47a92bbfc9d1d201e4283fa7be5dc3966933d1dbcdee0cf57f8db80fb9ff4`、`rls_enabled=true`、`force_rls=true`、`policy_count=2`、`trigger_count=2`、`fk_count=6`、`accounting_app` 仅具 `SELECT/INSERT`（禁 `UPDATE/DELETE`）。


## Step 3b-2 · PLT05-C/D/E（部分完成，诚实边界如下）

- 工程状态：**部分完成**（映射变更拦截与冲销标注已交付并通过测试；**补导未实现**，原因见下）
- 外部状态：无需外部
- 与提示词要求的差异：**补导（只重发失败凭证）本轮未交付**、`ROUNDTRIP_RUNBOOK.md` 未写、前端未新增标记；其余已按现有点位核对后交付

### 已交付：PLT05-C 映射变更（`mapping_changed`）

- `app/integrations/service.py` 新增 `_mapping_state(conn, ledger_id, outbox_row)`：只比对 `bridge_configurations` 当前版本与导出时冻结的 `configuration_version`，不做金额/科目推断。
- `get_outbox()` 返回 `mapping_changed / mapping_version / current_mapping_version / confirmation_required`；**已导出未接收**时 `record_receipt()` 直接 409「导出后科目映射已变更（导出用版本 N，当前 M）：请先确认映射变化并重新准备导出，不能直接登记接收」；已接收批次不受影响，变更仍可查（版本字段常驻）。
- 测试 `nexus_backend/tests/test_plt05_export_change_guards.py::test_mapping_change_after_export_blocks_receipt_and_stays_visible`：导出后 `mapping_changed=False` → 重新核定映射（版本 1→2）→ `mapping_changed=True`、`confirmation_required=True`、`mapping_version=1 / current=2`、`statuses.received/posted` 仍为 `None`、`result_unknown=True`（**只导出不会被读成已接收/已过账**）→ 直接登记接收被拒且文案含「映射已变更」「不能直接登记接收」。

### 已交付：PLT05-C 导出后被冲销凭证标注

- 新增 `_voided_draft_ids(conn, book_id)`（`drafts → voucher_sources(source_kind='draft') → vouchers → voucher_relations(kind='reversal')`）与 `_draft_by_external_code(conn, artifact_id, book_id)`（从冻结包的 `vouchers[].external_code → draft_id` 反查）；`_return_view()` 的逐笔行新增 `voided_after_export`，核对时可直接看到「这笔在导出后被冲销」。
- 测试 `test_voided_draft_annotation_query_contract`：用最小内存库验证只认「导出草稿 → 正式凭证 → 反向冲销」这条链，`adjustment` 不算冲销、另一账套的草稿不会被串进来。
- **诚实边界**：该标注的 True 分支目前只有查询级单测，没有端到端夹具（既有 tplus 夹具只走到「草稿已审核并导出」，没有已记账凭证可冲销）；端到端断言需要一个「已记账 + 已导出 + 已冲销」的夹具，留给下一轮。

### 未交付：PLT05-C 补导（只重发失败凭证），及为什么不能在本轮硬做

`bridge_outbox` 的两个唯一约束与冻结清单守卫堵死了服务层的子集补导：

1. `bridge_outbox.export_id TEXT NOT NULL UNIQUE`——一次导出（delivery export）只能对应一份交付意图，补导必须新建第二份意图；
2. `bridge_outbox.external_code TEXT NOT NULL UNIQUE`——同一导出的外部编号也会撞；
3. `accounting.bridge_scope_guard()` 在写入时按冻结清单逐条校验 `vouchers` 行与映射，**子集包会直接违反守卫**。

因此补导必须先做一次结构性迁移（放宽 `export_id` 唯一性为 `(export_id, reissue_seq)` 或引入 `reissue_of`/`root_export_id` 并相应改写 `bridge_scope_guard`，同时同步 SQLite 侧与 RLS/写门/外键），属于「改安全守卫」级别的工作，不适合在上下文余量不足时开工。**下一轮起点**：新建 PG `324` / SQLite `231`，把上述三项落成迁移 + 守卫改写 + `reissue()` 服务方法（只收 `bridge_return_rows.status IN ('rejected','missing','amount_mismatch','account_mismatch')` 且未被 `bridge_return_row_handlers` 处置为 `resolved` 的凭证编码；重走前实时重验 `_scope(write=True)` 权限、`_frozen_package` 指纹与 `mapping_changed`；`result_unknown` 一律 409），并补「新批次引用原批次且不含已成功凭证」的断言。

### PLT05-D 核对结论（无需重复造轮子）

- 现有 `src/IntegrationWorkbench.tsx` 已按三个独立事实显示：`对方已接收 · 日期`、`对方已过账 · 日期`、`人工声明已过账（未逐笔核对）`，并在 `result_unknown` 时显示「尚无对方结果，不自动重发」；逐笔差异清单与处置入口（`bridge_return_row_handlers`）已存在。
- 「仅导出不得显示成已入账/已过账」由既有 `test_plt05_external_states.py` 逐状态断言（本轮一并回归通过）。**因此 PLT05-D 本轮未改前端**，避免重复实现与触碰 `homeCss` 余量（仅 285B）。
- 未做的 D 残余：把本轮新增的 `mapping_changed` / `voided_after_export` 显示到工作台（属小改，需复用 `integration-*` 既有类）。

### PLT05-E（未交付）

`docs/pilot/ROUNDTRIP_RUNBOOK.md` 未写。起点已定：客户待确认清单（哪套系统是正式账、谁导入谁过账、哪份结果是最终依据、对方能导出什么格式）、测试账套联调步骤（导出 → 客户导入 → 取回结果 → 逐笔核对，每步记人/时间/文件摘要）、四类必演练情形（重复导入、部分失败、映射变更、无结果）、格式差异记录法；状态最高写「工具就绪待客户输入」。

### 顺带修复（既有红测）

- `nexus_backend/tests/test_plt05_return_workspace.py` 构造 `BridgeReturnRowHandle` 时缺必填 `reason_code`（PLT05-B 加字段后遗留），补 `reason_code="amount_mismatch"`；该文件 **3 passed**。

### Step 3b-2 门禁（真实输出）

| 命令 | 结果 |
| --- | --- |
| `pytest test_plt05_export_change_guards.py test_plt05_external_states.py` | **7 passed** |
| `pytest -k "plt05 or integrations or bridge or tplus"` | **61 passed, 19 skipped, 3 errors**（error 均为无 PG DSN 的 `test_c01_remaining_postgres.py` 夹具，环境性） |
| `check_migration_idempotency.py` / `check_secrets.py` | exit 0 / `no secrets found` |
| `uvx ruff==0.17.0 check` / `format --check nexus_backend/app services/api_gateway scripts` | `All checks passed!` / `467 files already formatted` |

## Step 4 精确起点（未开始，按序执行）

1. **4a Batch 02**：`src/WorkspaceContextBar.tsx` 切换单位/期间接入未保存离开拦截（复用 `requestConfirm`，禁新增 `window.confirm`）与在途请求中止/序号防串数；逐页清理与顶部重复的月份输入并出清单；E2E 覆盖延迟响应防串数、锁期二次确认、`A` 键 400ms 防误触。
2. **4b Batch 07**：`QuotaService.ensure_daily_jobs`（异步入队）、`ensure_storage`（原件上传）、`ensure_concurrent`（请求级）三条接线，保持「事务内校验 + 结构化告警日志、不写回滚审计表」。
3. **4c Batch 08**：`app/observability/alerting.py`（Webhook/钉钉/企业微信负载、指纹去重、静默窗口、退避重试，全部本地桩）+ `docs/ops/PRODUCTION_RUNBOOK.md`。
4. 红线提醒：`AccessControl.book()` 保持单条 SELECT；每步跑 `test_stage25_assistant_efficiency.py` 与 `test_stage19_request_reads.py`。

### Step 3b-2（第一块）独立验收加固（封账基线）

1. **堵死映射变更拦截旁路（`nexus_backend/app/integrations/service.py`）**：抽统一守卫 `_assert_mapping_valid_for_receipt()`，将未接收批次的映射变更 409 拦截同步覆盖至 `record_receipt()`、`record_return()`、`record_manual_return()` 三条接收入口；同时依据 `_external_state()["statuses"]["received"]["at"]` 判定已接收状态，确保已接收批次在后续变更映射时 `mapping_changed=True` 常驻可查但 `confirmation_required=False`、不受影响。
2. **修复 `voided_after_export` 永远为 `False` 的键列错配**：`_return_view()` 原先拿对方外部凭证号 `item["external_code"]` 去查冻结包哈希字典导致永不命中；已改为 `_draft_by_voucher_code()`（兼容 PG `JSONB`/`TEXT` 并同时映射原编码与 `T54-{code}`），并用 `item["voucher_code"]` 精准匹配。
3. **补齐端到端回归测试（`nexus_backend/tests/test_plt05_export_change_guards.py`）**：新增 `record_return` / `record_manual_return` 映射变更 409 拦截、已接收后变更映射保持已接收且 `confirmation_required=False`、以及走真实 `PostingService.post_draft` + `reverse_reviewed` 独立复核反向冲销后经 `bridge.list_returns()` 端到端断言 `voided_after_export=True`（原“无端到端夹具”的诚实边界已补齐）。
4. **PLT05-C 补导方案拍板**：**禁止修改或放宽现有 `bridge_outbox` 唯一约束及 `bridge_scope_guard()` 触发器**；下一轮新建独立只追加子集补导表 `bridge_reissue_batches`（PG `324` / SQLite `231`，含 `FORCE RLS`、`batch2_company_write_gate`、外键绑定父交付包与父导出文件、append-only 触发器），仅抽取失败/差异且未 `resolved` 的凭证子集并复用 `tplus54.render` 生成补导 CSV，实现零削弱旧守卫的受控补导闭环。

