# NEXT 修改归属台账

## 基线

- 记录时间：2026-11-02 19:18（Asia/Shanghai），HEAD `df3fface…`，`git status --porcelain` 为空。
- 基线候选：1790/`3fc6fc89…`，迁移 316/`f07ba1cd…`。

## 最终候选与工作区

- 冻结：`tmp_test/next/next11-final/candidate.json`，source 1816/`dddef1a88eee0bfdc2c2db37d09560dc0290b434fe800993c05b52b7ba2a963e`，迁移 325/`48fc7b47…`。
- `git status --porcelain` 共 134 项；包含本轮 NEXT 文件、并发任务文件、文档与私有运行材料。**不把所有未提交文件认作本轮成果**；本表只列可归属文件。
- `docs/*` 不在候选指纹前缀内；新增 `playwright.next07.config.ts` 等根配置会改变候选。

## 本轮可归属修改

| 路径 | 包 | 性质 |
| --- | --- | --- |
| `scripts/next_harness.py`、`scripts/next_profiles/next01.json`、`nexus_backend/tests/test_next_harness.py` | NEXT01 | 运行器、配置文件、14 项测试 |
| `e2e/next02-isolation.spec.ts`、`playwright.p02.config.ts`、`vite.config.ts` | NEXT02 | 权限负向浏览器用例与 P02 构建入口 |
| `scripts/next03_security_probe.py`、`scripts/next_profiles/next03.json` | NEXT03 | 动态探测与安全回归 profile |
| `nexus_backend/app/handoff/service.py`、`nexus_backend/tests/test_dcp05_handoff.py`、`scripts/next_profiles/next04.json` | NEXT04 | 税审接手最新版本/旧版本拒绝 |
| `nexus_backend/migrations/sqlite/127_tax_filing_evidence.sql`、`128_monthly_package_tax_evidence.sql`、`postgres/203_tax_filing_evidence.sql`、`204_monthly_package_tax_evidence.sql` | NEXT05 | 回执证据与月度包 v2 |
| `nexus_backend/app/tax/filings.py`、`app/tax/router.py`、`app/delivery/monthly_package.py`、`app/uploads.py`、`app/upload_bindings.py` | NEXT05 | 登记/原件/更正作废/包快照 |
| `src/tax-filings.ts`、`src/TaxFilingRecords.tsx`、`src/upload-client.ts`、`src/tax-filings.render.test.ts` | NEXT05 | 前端展示/上传/下载/渲染测试 |
| `nexus_backend/tests/test_next05_filing_evidence.py`、`test_upload_reference_binding.py`、`scripts/next_profiles/next05.json` | NEXT05 | 专项与升级回归 |
| `nexus_backend/migrations/sqlite/129_delivery_provider_events.sql`、`postgres/205_delivery_provider_events.sql`、`206_delivery_worker_read.sql`、`207_delivery_worker_event_read.sql` | NEXT06 | 通知回执事件、worker 最小读授权 |
| `nexus_backend/app/delivery/receipts.py`、`callback_router.py`、`router.py`、`worker.py`、`main.py` | NEXT06 | 回调验签/幂等/时间线、worker CLI、路由接线 |
| `nexus_backend/tests/test_next06_provider_receipts.py`、`test_n03_notification_postgres.py`、`scripts/next_profiles/next06.json`、`docs/NEXT06_DEPLOYMENT.md` | NEXT06 | 9 项专项 + PG worker 读写测试、部署说明 |
| `src/RevisionWorkbench.tsx`、`e2e/stage10-revisions.spec.ts`、`e2e/v07-context-recovery.spec.ts`、`playwright.next07.config.ts` | NEXT07 | 迟到读取不覆盖人工修改；跨引擎恢复/修订/手机/axe |
| `scripts/backup_accounting.py`、`nexus_backend/tests/test_batch_review.py`、`test_p0_f10_manifest_routes.py`、`test_stage11_review_packages_postgres.py` | NEXT08 | 恢复注册表补 tax 附件；夹具角色/独立复核修正 |
| `scripts/next09_http_capacity.py`、`tools/capacity/qa_mixed.py`（如本轮修改）、`tmp_test/next/next09-*` | NEXT09 | HTTP/PG 容量脚本与报告 |
| `docs/NEXT10_ACCEPTANCE.md` | NEXT10 | 离线助手验收记录（工具与夹具复用既有） |
| `docs/NEXT_EXECUTION_STATE.md`、`NEXT_ACCEPTANCE_MATRIX.md`、`NEXT_OWNERSHIP.md`、`NEXT01..11_ACCEPTANCE.md` | NEXT01–11 | 台账与逐包记录 |

## 并发任务文件（非本轮所有，保留不动）

- `nexus_backend/migrations/postgres/208_notification_recipient_projection.sql`：另一任务在 21:40 新增，重定义 `begin_delivery_notification` 以投影收件人；它使 NEXT06 r2 候选漂移，r2 只作诊断。
- `nexus_backend/app/intelligence/ocr.py`、`nexus_backend/tests/test_paddle_execution_boundaries.py`、`docs/CI_OCR_BOUNDARY_20261102.md`：另一任务的 OCR 边界修改。
- 上述文件不删除、不覆盖；其存在使最终候选包含非本轮修改，发布评审需单独确认。

## 私有运行材料

- `tmp_test/next/next01-acceptance-20261103-final`
- `tmp_test/next/next03-security-final.xml`
- `tmp_test/next/next04-acceptance-20261103-final`
- `tmp_test/next/next05-acceptance-20261103-final`
- `tmp_test/next/next06-acceptance-20261103-final2`
- `tmp_test/next/next07-browser`、`next07-browser-report-under-load.json`
- `tmp_test/next/next08-sqlite-final2.xml`、`tmp_test/ak05-pg-runs/01c19e2a…`、`cd95176b…`
- `tmp_test/next/next09-capacity-pg17-final`
- `tmp_test/next/next10-assistant-final2`
- `tmp_test/next/next11-final/candidate.json`

## 规则

1. 共享文件只做可归属的最小增量；发现并发修改先记录并避开冲突点。
2. 候选变化后旧证据保留但相关门槛失效；不同候选的通过记录不得拼成一次验收。
3. 私有配置与运行材料不外发；公开文档不含密码、DSN、令牌或真实财务原文。
4. 未执行 `git reset`、`git clean`、批量删除或全局环境修改。
