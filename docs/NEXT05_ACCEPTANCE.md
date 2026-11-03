# NEXT05 验收记录：税务办理证据闭环

- 状态：后端与离线验收通过；浏览器提交/下载未验证（本地进行中）。
- 外部验收：不适用（不连接真实电子税务局，不自动申报/缴款）。
- 候选（ddb 最终重跑）：`source_files=1816`，`source_sha256=dddef1a88eee0bfdc2c2db37d09560dc0290b434fe800993c05b52b7ba2a963e`；迁移 325 个，`migration_sha256=48fc7b47abd864e39ec6701939dd42f0bb65c1bc16732bcff2c6686350a0c8e1`；HEAD `df3fface95d1f69c28e859e89015465955c517b4`。该候选含并发任务的 `TaxFilingRecords.tsx` 修改，本轮未改写其内容。
- 受控运行：`tmp_test/next/next05-acceptance-20261103-final3`（`status=passed`，候选前后一致，cleanup passed）：`filing-regression` **70 passed／197.06s**，`frontend-typecheck` passed。历史 r3（1803/320）与 54d9 final 证据保留但不作为 ddb 最终候选依据。

## 实现

- 迁移 `nexus_backend/migrations/sqlite/127_tax_filing_evidence.sql`、`nexus_backend/migrations/postgres/203_tax_filing_evidence.sql`：回执入口类型、前驱、更正/作废原因、底稿 id/版本/digest/状态、金额核对；`tax_filing_attachments` 原件表；PG RLS 读 `firm_id`+`book_allowed`，写 `book_review_allowed`+`created_by=actor_user()`；`dcp_filing_guard()` 校验同账套/期间/税种前驱、未作废/未替代、底稿版本与摘要一致。
- 迁移 `sqlite/128`、`postgres/204`：月度包 schema v2 允许 `tax_filings.json`，v1 仍严格 11 个文件；旧包保持冻结可读。
- `nexus_backend/app/tax/filings.py`：登记绑定最新底稿版本；金额 `match/mismatch/no_workpaper`；列表返回 `current` 与历史；更正/作废追加且旧行不变；`current_for`/`evidence_for` 供月度包；`attach()` 通过上传凭证绑定单个原件；`attachment_for()` 同时校验事务所与账套读权限。
- `nexus_backend/app/tax/router.py`：登记接受 `entry_kind/supersedes_id/correction_reason`；新增 `POST /api/tax/filings/{id}/attachments` 与 `GET /api/tax/filings/{id}/attachments/{attachment_id}`；下载带 `Content-Disposition: attachment`、`CSP: sandbox`、`Cache-Control: private, no-store`、`X-Content-Sha256`。
- `nexus_backend/app/delivery/monthly_package.py`：`FILE_PATHS_V1`/`FILE_PATHS_V2`，新包 `schema_version=2` 并写入 `tax_filings.json`（当前回执、原件 hash、底稿版本/状态、金额核对、历史）；`_snapshot` 按包 schema 校验。
- 前端 `src/tax-filings.ts`、`src/TaxFilingRecords.tsx`、`src/upload-client.ts`：展示当前/历史、底稿版本、金额核对、原件 SHA-256 与下载；更正/作废原因；登记后上传回执原件。

## 验证

- 新增 `nexus_backend/tests/test_next05_filing_evidence.py`，6 项：登记绑定底稿版本与原件 hash；重复回执 409；跨企业下载/列表拒绝；更正/作废保留历史且旧行冻结；新底稿版本重新绑定；非复核人拒绝；v1/v2 包快照校验。单独运行 `6 passed in 16.36s`，JUnit `tmp_test/next/next05-focused.xml`。
- 定向回归 8 个文件（filing/readiness/tax api/本月包/上传绑定）：`70 passed, 0 failed, 0 skipped`，167.80s，JUnit `tmp_test/next/next05-regression.xml`；harness 内 `filing-regression` 70 项 220.227s。
- 前端：`node node_modules/typescript/bin/tsc --noEmit` 通过；`node node_modules/vitest/vitest.mjs run src/tax-filings.render.test.ts` → `3 passed`（空列表边界、已登记证据、金额格式）。
- PG17：`test_accounting_postgres_isolated.py --config …/pg17-recovery_source.json nexus_backend/tests/test_monthly_delivery_postgres.py` → `4 passed`，JUnit `tmp_test/next/next05-pg17.xml`。
- PG16：同一命令换 `pg16-recovery_source.json` → `4 passed`，JUnit `tmp_test/next/next05-pg16.xml`。
- PG 运行配置来自 `tmp_test/next02-env/runs/4fb7aceea75d4232a2aaeb99c00705c9`（本轮隔离端口 25496–25499）；`test_p0_close_upload_postgres.py` 需要独立 `test_p0_` 账号契约，本环境未配置，46 项按夹具要求跳过，未计入通过。

## 失败与修复

1. `sqlite3.IntegrityError: CHECK constraint failed: purpose IN(...)`：新增 purpose 后旧表 CHECK 不接受；迁移 127 重建 `upload_intents`，保留 `binding_json`、7 路绑定视图与上传触发器。
2. 重建漏列 → `no such column: binding_json`；补齐列并重建视图/触发器。
3. heredoc 截断 → `incomplete input`；补全触发器。
4. 包测试 15 项 `IntegrityError: Frozen snapshot scope differs from package`：095 触发器硬编码 11 文件；新增 128/204 支持 schema v2；JSON path 中带点的键需 `$.files."tax_filings.json"`。
5. 旧上传凭证迁移测试在会话模板下失败（模板已应用 127，历史库跳过 088/127 后出现未知版本）；测试构造历史库时禁用模板复制，按真实 088→127 升级路径验证。
6. 测试夹具误把 `accountant` 当作无读权限，实际事务所会计可读；改用 `employee` 验证越权，属夹具假设错误。

## 剩余

- 浏览器实际登记、上传原件、下载与月度包更新的端到端证据未采集；当前仅有组件级渲染与后端 API 测试。
- 未验证真实电子税务局回执格式/签章；本轮只登记人工回执。
- PG 负向（跨企业/错期间/越权下载）目前在 SQLite 测试覆盖，未新增 PG 专项用例。
