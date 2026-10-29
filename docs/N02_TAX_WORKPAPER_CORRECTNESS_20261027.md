# N02 税务底稿正确性与数据库复核约束（2026-10-27）

范围：在既有 `TaxWorkpaperService` 上收紧正确性与权限，不新建税务总账、审核引擎或账簿算法。未提交、未推送。

## 1. 已有能力与本次修改范围

| 既有能力 | 本次 |
| --- | --- |
| 税务档案（`tax_profiles`，税率只能来自档案） | 保留，不改口径 |
| 底稿生成/复核/CSV（`app/tax/workpaper.py`） | 修四类缺陷 |
| 独立复核与期间控制（`access.book(...,'review')`、`periods.status`） | 复用，复核前重算来源 |
| 审计事件、来源指纹 | 沿用，指纹纳入科目映射与正式账 |
| PG/SQLite 双迁移 | 追加 170 / 107，不改历史 |

## 2. 问题与红测证据

先写用例（`nexus_backend/tests/test_tax_workpaper_guards.py`、`test_tax_workpaper_postgres.py`），在修复前运行：

| 用例 | 修复前 | 失败原因 |
| --- | --- | --- |
| 直接 INSERT `status='reviewed'` | 通过（未拦） | 无插入守卫 |
| 复核时来源已变 | 通过（未拦） | 复核不重算指纹 |
| 本期含红字发票 | 通过（静默丢弃） | `_invoice_rows` 直接 `continue` |
| 无复核权限者复核 | 通过（未拦） | 仅校验 `created_by` |
| 已复核金额改写 / 删除 | 已被既有触发器拦 | 既有 106 迁移守卫 |

命令：`./.venv/Scripts/python.exe -m pytest nexus_backend/tests/test_tax_workpaper_guards.py -q`
修复前：`4 failed, 2 passed`（退出码 1）。

## 3. 修复

### 3.1 账面基准改为正式凭证

`_ledger_vat` 原读 `draft_lines`（`approved`/`exported` 草稿）并按 `2221%` 全前缀汇总。现改为：

```sql
FROM voucher_lines l JOIN vouchers v ON v.id=l.voucher_id
WHERE v.book_id=? AND v.period=? AND v.status='posted' AND l.code_snapshot LIKE ?
```

且只汇总显式映射 `VAT_ACCOUNTS = {"2221.01": "应交增值税", "2221.02": "未交增值税"}`；出现其它 `2221.*` 明细时抛 409 并列出金额，不再把附加税/所得税混入增值税基准。

### 3.2 不支持场景阻断而非静默丢弃

`_invoice_rows` 返回 `(rows, rejected)`，按原因计数：红字、作废、重复、方向未知、金额缺失或非法、价税分离不成立。任一原因 > 0 时生成被 409 阻断，错误文案列出「红字发票 N 张」等，符合「不得静默丢弃后仍显示完整」。

### 3.3 来源快照与复核失效

新增 `_snapshot(conn, book, period, profile)`，生成与复核共用同一计算；指纹纳入 `period/profile/VAT_ACCOUNTS/rows/totals(含正式账)`。复核前重算：

- 档案变化 → 409「来源税务档案已变化」；
- 发票、税率或正式账变化 → 409「底稿来源已变化（发票、税率或正式账），请重新生成后再复核」；
- 原快照仍保留（不上写、不冒充最新）。

### 3.4 数据库守卫（PG 170 / SQLite 107）

- 插入只能是不带复核痕迹的草稿（`status='draft'`、`reviewed_by/at` 为空、`review_reason` 空）。
- 身份列不可改写：`firm_id/company_id/book_id/period/version/profile_id/created_by/created_at/source_digest`。
- 复核必须带独立复核人、时间与 ≥3 字意见，且 `reviewed_by <> created_by`。
- 已复核金额、分录与差异不可改；删除一律拒绝（沿用既有只增语义）。

PostgreSQL 侧重定义 `accounting.tax_workpaper_guard()`（既有触发器指向该函数，无需重建），SQLite 侧新增三个触发器。两份迁移首行为 `-- compatibility: incremental`，未修改 169 及以前任何文件。

## 4. 测试与门禁

| 项 | 命令 | 结果 | 退出码 |
| --- | --- | --- | --- |
| SQLite 税务（含既有） | `pytest test_tax_workpaper.py test_tax_workpaper_guards.py` | 15 passed | 0 |
| PG16 税务守卫 | `ACCOUNTING_TEST_*` 指向 25531，`pytest test_tax_workpaper_postgres.py` | 4 passed | 0 |
| PG17 税务守卫 | 同上指向 25532 | 4 passed | 0 |
| 迁移结构与幂等 | `pytest test_migration_structure test_qs01_migration_integrity test_migration_idempotency_scanner test_migration_idempotency_ledger` | 22 passed（合并跑 59 passed） | 0 |
| 空库迁移 | `migrate_accounting.py migrate` 后 `status --strict`（PG16/17 新建 `n02_fresh`） | 170 应用成功，strict 通过 | 0 |
| 静态检查 | `ruff check` + `ruff format --check`（3 个 Python 文件） | 通过 | 0 |

数据库与角色：PG16.15 / PG17.11，库 `n01_gate`（升级路径 169→170）与 `n02_fresh`（空库 0→170）；业务用例走 `n01_app_pg16/17`（NOSUPERUSER、NOBYPASSRLS）与 `n01_wrk_pg16/17`，管理员连接仅用于建身份与执行迁移。

## 5. 源码指纹

| 文件 | sha256 前 16 位 |
| --- | --- |
| `nexus_backend/app/tax/workpaper.py` | `691eae62c81535c5` |
| `nexus_backend/tests/test_tax_workpaper_guards.py` | `e11c778f3d2e99a4` |
| `nexus_backend/tests/test_tax_workpaper_postgres.py` | `62d528f0aeb33715` |
| `nexus_backend/migrations/postgres/170_tax_workpaper_hardening.sql` | `79ade6f056a134f9` |
| `nexus_backend/migrations/sqlite/107_tax_workpaper_hardening.sql` | `fe5434b093936034` |

`git diff --stat`：`workpaper.py` +123/−44；其余为新增文件。历史迁移与新表均未改动。

## 6. 未验证与残留风险

1. **正式凭证基准缺少轻量红测**：`_ledger_vat` 已改为只读 `vouchers`，但要构造合法正式凭证需期初绑定、凭证号与复核策略，成本高；当前证据是 SQL 与 PG 上的端到端复核（4 passed），没有「草稿不计入」的直接用例。列为缺口，不用算术推断替代。
2. **并发未覆盖**：未写「撤权与复核」「来源变化与复核」两会话竞争用例；服务端已用 `UPDATE ... WHERE version=? AND status='draft'` 与重算指纹，但缺并发证据。
3. **CSV 导出未绑定复核版本**：`csv()` 复核后即时渲染，仍可能读到更新的复核版本；N02 要求「导出重新授权并绑定确切复核版本」未完成。
4. **税率与口径仍由人工档案决定**：不联网取政策、不猜优惠；`filing` 恒为 `not_filed`，不宣称已申报或合规。
5. **红字/季度/跨期/期初留抵**：按阻断处理，未实现计算；下一步需会计确认口径。

## 7. 文件归属与环境清理

- 修改：`nexus_backend/app/tax/workpaper.py`（本会话）。
- 新增：两份迁移、两个测试文件、三份文档（`N01_BASELINE_20261027.md`、本文件、上一轮部署记录）。
- 未触碰：`services/paddle_ocr`、正式记账内核、Worker、双数据库架构、历史迁移。
- 临时对象：`n02_fresh` 为一次性库，保留供复核；未停止他人进程与端口（25531/25532 为 N01 建的本任务集群）。
- 未提交、未推送、未部署。
