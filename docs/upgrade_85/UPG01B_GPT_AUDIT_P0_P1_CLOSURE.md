# Batch 01B：GPT 审计 P0/P1 残余财务正确性清零与同候选校验

- **先读**：`docs/upgrade_85/UPG00_MASTER.md`、`docs/rework/UPGRADE_85_STATUS.md`、`nexus_backend/migrations/postgres/300_plt05_return_files.sql`、`309_plt05_return_rls_outer_qualify.sql`、`293_plt02_reconciliation_notes.sql`、`307_plt04_source_relations.sql`、`nexus_backend/app/integrations/router.py`、`nexus_backend/app/tax/relations.py`、`nexus_backend/app/tax/variances.py`、`e2e/plt04-fee-variance-target.spec.ts`
- **前置**：Batch 01（全量非 PG 重跑尾部失败清零后）立即执行，**必须在本批全部通过后再进入 Batch 02**
- **定位**：先彻底焊死 GPT 独立审计指出的 6 项 P0/P1 残余财务正确性与权限缝隙，再推进 Batch 02–10 冲刺全维度 8.5+

---

## 1. P0 级残余漏洞彻底闭环（优先执行）

### 1.1 P0-1：收紧回执主表/明细表 RLS 与四维度隔离实测
- **已补现状**：`304` + `309` 已收紧 `bridge_return_evidence` 和 `bridge_return_row_handlers` 至账套级外层列限定。
- **残余缺口修复**：
  - 查询 PG 最新迁移号，新增前向迁移（带 `-- compatibility: incremental`），把父表 `accounting.bridge_return_imports` 与 `accounting.bridge_return_rows` 的 RLS 从仅校验 `company_visible(company_id)` 收紧为：
    - `SELECT`：`accounting.book_allowed(book_id)` + `firm_id = accounting.actor_firm()`；
    - `INSERT` / `UPDATE`：`accounting.book_allowed(book_id, true)` + `firm_id = accounting.actor_firm()` + 校验父 `artifact_id` 同账套/同企业 + `created_by = accounting.actor_user()`（`bridge_return_rows` 校验父 `import_id` 所属账套可读/可写）；
  - 扩展 `nexus_backend/tests/test_plt05_return_rls_postgres.py`，用普通运行角色（`accounting_app`）在 PG16/17 验证全部四类边界：**同所跨法人、同法人跨账套（同一企业账套 A/B 仅授 A）、实时撤权、以及授权复核员对 imports / rows / handlers / evidence 四张表的正常读写**。

### 1.2 P0-2：外部过账状态与中文下载头零死角收口
- **已补现状**：`305` 已区分 `posted_declared` 与 `evidence_verified`，`artifact_return_evidence_download` 已修复 `客户回执.pdf` 中文下载 500。
- **残余缺口修复**：
  - 将 `nexus_backend/app/integrations/router.py::download_artifact`（及其他导出下载头）一并改为 RFC 5987 双头（ASCII fallback + `filename*=UTF-8''`），补测含中文文件名的导出包与 `客户回执.pdf` 下载均返回 200；
  - 补强 HTTP + 服务级测试：手工先声明过账（无原件时有效过账必须为 `false`）→ 补传有效 PDF 原件后变为 `evidence_verified=true` 且有效过账成立；再次验证错批次、缺原件、金额/科目不全、部分失败与重复登记边界。

### 1.3 P0-3：出租车差额来源关系（退款/同额多笔/换版拦截独立复核）
- **已补现状**：`307/308` + `tax/relations.py` 已落地 `fee_source_relations` 并接入 `analysis.py` 与 `variances.py`。
- **残余缺口修复**：
  - 在前向迁移中补强 `accounting.fee_source_relations` 的 RLS：显式增加 `firm_id = accounting.actor_firm()` 与 `INSERT` 校验 `verified_by = accounting.actor_user()`；
  - 在 `tax/variances.py::handle` 增加来源关系新鲜度校验：若差异绑定了 `relation_id` 且对应 `settlement/bank/fee` 任一来源已换版，禁止生成差异草稿并返回 409；
  - 在 `test_assistant_settlement_difference.py` 与 `test_plt04_fee_variances.py` 补齐三项场景测试：**退款冲减关联场景、同月同额多笔独立流水并存防串笔场景、以及绑定 `relation_id` 的差异草稿走独立复核（处理人自审 403、另一复核员通过）**。

---

## 2. P1 级残余缺口闭环

### 2.1 P1-4：报表差异解释数据库级复核权/报告/账套强绑定
- **已补现状**：`reports/reconciliation.py` 已修正利润差异下钻科目明细，映射变更旧报表失效已测。
- **残余缺口修复**：
  - 在本批新增的 PG 前向迁移中收紧 `accounting.report_reconciliation_notes` 的 `INSERT` RLS 策略：
    - 由 `accounting.book_allowed(book_id, TRUE)` 升级为 **`accounting.book_review_allowed(report_reconciliation_notes.book_id)`**；
    - 显式绑定 `created_by = accounting.actor_user()` 与 `EXISTS (SELECT 1 FROM accounting.report_runs r WHERE r.id = report_reconciliation_notes.report_id AND r.book_id = report_reconciliation_notes.book_id AND r.firm_id = report_reconciliation_notes.firm_id AND r.company_id = report_reconciliation_notes.company_id)`；
  - 在 `nexus_backend/tests/test_plt02_reference_reconciliation_postgres.py` 补测 PG 数据库层：普通编辑员（无复核权）直接插入被拒、伪造跨账套 `report_id` 插入被拒、授权复核员正常写入且不可 UPDATE/DELETE。

### 2.2 P1-5：待办到办理、办理后返回全链路稳态验收
- **已补现状**：`e2e/plt04-fee-variance-target.spec.ts` 已验证桌面/手机点击进入高亮与直接返回。
- **残余缺口修复**：
  - 扩展 `e2e/plt04-fee-variance-target.spec.ts`，完整覆盖桌面（1366×768）与手机（390×844）：从待办点击进入定位目标行 → **真实完成差异办理或转交** → 返回 `/workspace/home` 恢复原月份、原筛选与滚动位置（并确认已办结项消失/转交项更新），同时覆盖他人已办（410）、撤权（403/404）与旧版本（stale）弹窗拦截；
  - 检查并加固 `e2e/plt03-monthly-return.spec.ts`、`e2e/plt03-task-matrix.spec.ts`、`e2e/plt03-task-navigation.spec.ts` 夹具（修复选错客户或等待旧页面容器的脆弱点），确保连续 2 次运行 0 偶发失败。

### 2.3 P1-6：Batch 01 + 01B 同一候选完整证据打包
- 确保 Batch 01 尾部剩余的 `RWK02-E` 非 PG 全量失败全部清零（0 failed, 0 error）；
- 在**同一工作区候选指纹**下，完整运行并记录：
  1. 后端非 PG 全量 + `test_pg_sql_hygiene.py` + `test_migration_structure.py` + `test_write_scope_baseline.py`；
  2. PG16 与 PG17 双库迁移至最新并跑通 `test_plt05_return_rls_postgres.py`、`test_plt02_reference_reconciliation_postgres.py`、`test_plt04_fee_variances_postgres.py`、`test_stage10_integrations_postgres.py`、`test_stage12_workspace_postgres.py`；
  3. 前端六项全门禁（`typecheck / lint / format:check / test / build / test:frontend-budget`，不改上限）；
  4. 桌面与手机 Playwright E2E（上述 PLT03/PLT04/PLT05 套件）。

---

## 3. 验收命令与通过标准

```text
.venv/Scripts/python.exe scripts/check_test_env.py
.venv/Scripts/python.exe -m pytest nexus_backend/tests/test_pg_sql_hygiene.py nexus_backend/tests/test_migration_structure.py nexus_backend/tests/test_write_scope_baseline.py -q
.venv/Scripts/python.exe -m pytest nexus_backend/tests/test_plt05_return_import.py nexus_backend/tests/test_plt05_return_http.py nexus_backend/tests/test_assistant_settlement_difference.py nexus_backend/tests/test_plt04_fee_variances.py nexus_backend/tests/test_plt02_reference_reconciliation.py -q
.venv/Scripts/python.exe -m pytest nexus_backend/tests -q -p no:cacheprovider --ignore-glob="*_postgres.py"
npm run typecheck && npm run lint && npm run format:check && npm test -- --run && npm run build && npm run test:frontend-budget
npx playwright test e2e/plt04-fee-variance-target.spec.ts e2e/plt03-monthly-return.spec.ts e2e/plt03-task-matrix.spec.ts e2e/plt03-task-navigation.spec.ts e2e/stage10-integrations.spec.ts --reporter=line
```

- 上述全部命令 0 失败（若本机无 PG 实例须明确写明 PG 待远端验证，有隔离 PG16/17 则必须双库跑绿并贴输出）；
- 将统一候选指纹与全部命令输出追加至 `docs/rework/UPGRADE_85_STATUS.md` 后，立即无缝启动 **Batch 02**。
