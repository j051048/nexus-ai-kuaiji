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
