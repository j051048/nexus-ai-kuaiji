# P03 税务准备工作台（A 档：底稿流水线）

日期：2026-10-27 · 候选工作区：`tmp_test/qr01-integration` · 状态：**本地完成、待验收**
基线：`32e3042d` + Q-R01/Q-R02/Q-S00–S04 + P01/P02（全部未提交、未推送）

---

## 1. 本批做了什么（一句话）

把 `app/tax/` 从「79 行纯函数、无路由、无落库」变成**可审计的增值税底稿流水线**：纳税人档案 → 从发票导入批次与账簿 2221 科目取数 → 生成版本化底稿 → 差异必须解释 → 独立复核 → 带来源指纹的 CSV 导出；**不申报、不缴款、不写凭证、不影响报表**。

## 2. 范围与红线（逐条落点）

| 要求 | 落点 | 证据 |
| --- | --- | --- |
| 税率必须来自企业档案，缺档案拒绝生成而不是猜 | `TaxWorkpaperService.generate` 无有效 `tax_profiles` 抛 409「缺少{period}生效的纳税人档案…」 | `tests/test_tax_workpaper.py::test_generate_without_profile_refuses_instead_of_guessing` |
| 无发票数据拒绝生成 | `_invoice_rows()` 为空抛 409「没有可用的发票导入行…」 | `::test_generate_without_invoice_rows_refuses` |
| 底稿不写凭证、不改 2221、不影响报表 | 服务只 `INSERT INTO tax_workpapers` 与 `audit_events`；全文无 `posting`/`vouchers` 写入 | `app/tax/workpaper.py` 无 posting 引用；报告口径即代码边界 |
| 制单人不得复核自己的底稿 | 服务层 `review()` + 数据库触发器双保险（`accounting.tax_workpaper_guard`，ERRCODE 42501） | `::test_own_workpaper_cannot_be_reviewed_by_its_maker`、`migrations/postgres/169_tax_workpapers.sql` |
| 差异未写说明不得 reviewed | 触发器 `variance_cents<>0 AND variance_reason=''` → 23514；服务层同样先校验 | `::test_variance_requires_explanation_before_review` |
| 导出复用「来源指纹」思路 | `source_digest = sha256(period+profile+rows+totals)`；CSV 响应头 `X-Content-Sha256` | `::test_draft_workpaper_cannot_be_exported`（服务层）+ `tests/test_tax_api.py`（HTTP 409）、`app/tax/router.py` |
| 只导出已复核底稿 | `csv()` 遇 `draft` 抛 409「只能导出已复核的底稿」 | `tests/test_tax_api.py`（draft 导出 409） |
| 期间关闭不得生成 | `_guard_period()` 校验 `period_status='open'` 与 `internal_period_locks` | `::test_period_cannot_be_closed_by_a_plain_update`（关期守卫）+ `_guard_period()` |

## 3. 数据与状态模型

- `accounting.tax_profiles`：按 `company_id + effective_from` 版本化（`taxpayer_type small_scale|general`、`vat_rate_bp`、`surtax_rate_bp`、`filing_cycle monthly|quarterly`、`reason`、`created_by`）。**只增不改**（无 UPDATE 授权），换率＝新增一条生效日更晚的档案。
- `accounting.tax_workpapers`：`book_id + period + version` 唯一，`status draft|reviewed`；金额全部整数分（`output_net_cents/output_tax_cents/input_net_cents/input_tax_cents/tax_cents/surtax_cents/credit_carry_cents/ledger_cents/variance_cents`），`lines_json` 存逐票行，`source_digest` 存来源指纹；复核后除 `reviewed_by/reviewed_at/reason` 外**全部列不可变**（触发器逐列比对）。
- 计算口径：
  - 小规模：`tax = round(output_net × vat_rate_bp / 10000)`，`credit_carry = 0`；
  - 一般纳税人：`gross = output_tax − input_tax`，`tax = max(gross,0)`，`credit_carry = max(−gross,0)`；
  - 附加税：`surtax = round(tax × surtax_rate_bp / 10000)`；
  - 账税差异：`variance = tax − ledger`，`ledger` 取该账套该期间 `2221*`（应交税费）科目净发生额；
  - 全程整数分与四舍五入到分，无 float。

## 4. 接口契约（已接线，可直接被界面消费）

```
PUT  /api/tax/profile                      {book_id,taxpayer_type,vat_rate_bp,surtax_rate_bp,filing_cycle,effective_from,reason}
GET  /api/tax/profile?book_id&period        → {profile|null}
POST /api/tax/workpapers                   {book_id,period?,reason}        → 201 底稿（draft）
GET  /api/tax/workpapers?book_id&period     → {workpapers:[{id,version,status,tax,variance,…}]}
GET  /api/tax/workpapers/{id}?book_id       → 明细（含 lines）
POST /api/tax/workpapers/{id}/review        {book_id,reason,expected_version} → 409 版本前移 / 403 自审
GET  /api/tax/workpapers/{id}/csv?book_id   → text/csv + X-Content-Sha256；draft 409
```

路由挂在 `app/main.py`（`build_tax_router(actor)`），服务注册为 `app.state.tax`；权限沿用 `AccessControl.book()`：生成需 `edit`、复核需 `review`、读与导出需 `read`。

## 5. 数据库守卫（本批新增）

- RLS：`tax_profiles` 读/写按 `company_visible(company_id)`；`tax_workpapers` 读按 `book_allowed(book_id)`、写与复核按 `book_allowed(book_id,true)`，`WITH CHECK` 绑定 `created_by/reviewed_by = actor_user()`。
- 授权：`accounting_app` 对档案只有 `SELECT,INSERT`，对底稿 `SELECT,INSERT,UPDATE`；无 DELETE。
- 行级围栏 `accounting.tax_workpaper_guard()`（SECURITY INVOKER，`BEFORE INSERT OR UPDATE OR DELETE`，两张表共用）：
  1. 先 `accounting.lock_company_write(firm_id, company_id)` 取公司写锁（无公司作用域时退化为 legacy 全局锁），保证与记账写路径同一把锁；
  2. `DELETE` 一律 42501（append-only）；
  3. `tax_workpapers` 的 `UPDATE` 只允许 `draft → reviewed`，逐列比对金额/期间/版本/指纹，`reviewed_by = created_by` 抛 42501，差异无说明抛 23514。

> 结构门禁：`nexus_backend/tests/migration_gate_scan.py` 的 `RECOGNIZED_GATES` 新增 `tax_workpaper_guard`，并在同一处写明入册前提（函数体必须真调用 `lock_company_write`，真锁由 PG 运行时脚本实证）。这是**放宽扫描器的识别名单**，没有删改任何断言；`test_new_tables_carry_rls_and_write_gate` 仍对真实仓库全量扫描。

## 6. 证据（可复跑）

| 项 | 命令 | 结果 |
| --- | --- | --- |
| 服务单测 | `pytest nexus_backend/tests/test_tax_workpaper.py -q` | 9 passed |
| HTTP 契约 | `pytest nexus_backend/tests/test_tax_api.py -q` | 1 passed（含 409/403/CSV 拒绝） |
| 结构门禁 | `pytest nexus_backend/tests/test_migration_structure.py nexus_backend/tests/test_qs01_migration_integrity.py -q` | 7 + 9 passed |
| 门禁合计 | 上述 5 个测试文件 | **35 passed in 32.48s** |
| PostgreSQL 16/17 | `python tmp_test/p03_pg_workpaper.py` | 两集群均 `PASS`：157 个迁移、幂等重跑 0、底稿 30.00 元 |
| 静态检查 | `ruff check nexus_backend/app nexus_backend/tests`；`ruff format --check nexus_backend/app services/api_gateway scripts` | 全绿（312 文件） |

PG 脚本做的是**真库实证**，不是模拟：临时库上跑全量迁移，以受限角色 `accounting_app`（`SET LOCAL ROLE` + `app.firm_id/company_id`）建档案、生成底稿、复核（换一个用户）、导出，并断言：跨企业不可见、制单人复核被拒、复核后改金额被拒、重跑幂等。

## 7. 明确未做（避免误解）

| 未做 | 原因 |
| --- | --- |
| 申报表套打、申报端口、缴款 | 工作令排除；且无口径来源（税率/征收率/优惠备案都以税局为准），硬编码等于制造错误 |
| 企业所得税、个税、印花税、房产税 | 需要工资、固定资产、合同等模块配合，不在 A 档 |
| 进项认证清单（专票勾选）作为数据源 | A 档只认 `invoice_import_batches`；勾选结果需要新的导入通道（P03-B 候选） |
| 界面页面 | 本批交付 API 契约；界面（税务底稿页、差异说明抽屉、导出）留 P03-B，理由：需要与现有 `ReviewWorkbench` 交互一致，且必须先有服务端口径被验收 |
| 多年度结转、期末留抵跨期自动结转 | `credit_carry_cents` 只记录本期留抵，跨期结转需在期间链条上定义规则 |

## 8. 已知限制与风险

1. **账税差异基准较窄**：`ledger` 只取 `2221*` 净发生额；若企业把增值税记到 `2221.01/2221.02` 之外（如小规模直接记 `应交税费—应交增值税`），需确认科目映射。当前以「代码前缀 2221」为准，已在报告标注。
2. **附加税只按增值税×率**：未区分城建税/教育费附加/地方教育附加的分档与减免，`surtax_rate_bp` 由录入人负责（档案必须写 `reason`）。
3. **一般纳税人取数依赖发票税额**：`input_tax` 直接取发票行的 `tax_cents`；若发票行税额缺失或有差额征税，需人工在导入前修正。
4. **CSV 无分页**：底稿行数上限未设；单月发票行通常 <2000，但极端情况需加 `ROW_LIMIT`（可复用 Q05 的 50,000 行/16MB 思路）。
5. **界面未接**：后端已可用，但事务所人员目前只能通过 API/SDK 使用；这是本批最大的产品缺口。

## 9. 下一步（按工作令）

- **P03-B（可选）**：税务底稿页 + 差异说明抽屉 + CSV 下载 + 空态引导（「先登记纳税人档案」/「先导入发票」）；复用 `src/api.ts` 的 scope/abort 模型与 `ReviewWorkbench` 的复核交互。
- **E01（工作令最后一批）**：性能与可维护性——目前已知热点：`app/main.py` 1,118 行单函数装配、165 处 `write=True` 仅 73 处公司作用域、`is_postgres` 分支 185 处、`App.tsx` 1,784 行/34 `useState`。
- 仍然：**未提交、未推送**，等验收。

## 10. 复现命令

```bash
cd tmp_test/qr01-integration
../../.venv/Scripts/python.exe -m pytest nexus_backend/tests/test_tax_workpaper.py \
  nexus_backend/tests/test_tax_api.py nexus_backend/tests/test_tax_estimate.py \
  nexus_backend/tests/test_qs01_migration_integrity.py \
  nexus_backend/tests/test_migration_structure.py -q
../../.venv/Scripts/python.exe tmp_test/p03_pg_workpaper.py
```
