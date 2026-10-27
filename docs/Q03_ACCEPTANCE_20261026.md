# Q03 验收：连续期间账簿（2026-10-26）

本报告只覆盖用户下发的 Q03 工作包：在既有正式账簿查询与 CSV 导出上增加「跨月连续期间」能力。
不改核算引擎、不改写历史凭证、不加新的产品入口。

- 起点：`HEAD 3fc1ab6e`（`git log -1`），工作区另有其他智能体的未提交后端文件，本次未触碰。
- 结论：**本地可重复候选通过**；PostgreSQL 16/17 专项门禁与远端 CI 未执行（见 §7）。
- 迁移文件零改动：`git status --porcelain nexus_backend/migrations` 为空。

## 1. 计算的契约

连续账簿不是把每月余额表 UNION 起来，而是：

1. **范围以长期账套（`ledger_id`）+ 起止月份表达**，月份只能是 `YYYY-MM`，`start <= end`，最多 240 个月。
2. **期初只取区间起点那一期**绑定并冻结的期初包；后续月份自己的期初包是「上月期末的重复」，累加即双计，因此一律忽略（`test_range_does_not_add_later_opening_packages`）。
3. **期间逐月判定**，每期被归为 `ok / missing / unauthorized / unbound`，并单独记录该期的期初、借方、贷方、期末。
4. **期初与上期期末按科目逐项比对**（不是比合计）。期初包必须自身平衡，所以「包内平衡但科目间腾挪」只能逐科目发现（`test_range_reports_opening_discontinuity`）。
5. **不完整必须显式**：`complete=false` 时返回 `coverage` + `blockers`（`missing_period`/`unauthorized`/`opening_missing`/`period_open`/`opening_mismatch`），合计照常给出但标注不可当作已结账账簿。
6. **未关账的月份不让合计冒充结账数**：`periods.status != 'closed'` 一律产出 `period_open`。
7. **一次查询一个指纹**：`fingerprint = sha256(规范化 JSON)`，页面与 CSV 首部同值，CSV 另带 `X-Source-Fingerprint`。
8. **权限逐期复核**：查询用 `read`、导出用 `export`；完全不可见的账套直接 403/404，不做「部分可见就返回」。

## 2. 后端实现

`nexus_backend/app/posting/service.py`（+380 行）

- 抽出 `_accounts(conn, ledger_id)`（当前版本科目）与 `_balance_rows(accounts, own)`（子科目递归汇总、环检测 409、零行跳过、叶子合计）；单账套路径改为复用它们，行为不变（§5 回归）。
- `_months(start, end)`：解析与上限（>240 月报「连续账簿最多查询 240 个月」）。
- `_range_coverage(conn, actor, ledger_id, start, end, capability)`：逐月 `access.book(...)`，取 `period_status`、该期绑定版本、期初行、凭证发生额；跨期按科目比对 `opening_mismatch`；产出 `items/blockers/complete`。
- `_coverage_view`（对外视图，去掉内部 `_` 前缀字段）、`_range_scope`、`_range_shape`（指纹载荷）、`_snapshot_names`（凭证行 `name_snapshot`，用于「更名」标记）。
- `range_trial_balance(_in_transaction)`：`rows + totals + scope + coverage + blockers + complete + fingerprint`；行内附 `names`（历史名称）与 `renamed`。
- `range_account_ledger(_in_transaction)`：科目 404 校验、逐期取凭证行、`balance_cents` 跨月递推、`entries[].period`。

`nexus_backend/app/posting/presentation.py`（+158 行）

- `_range_meta`：CSV 前两行为「列名行 + 取值行」，取值行末列即指纹；`_range_company` 先以 `export` 能力取企业，再在 `write=True, company_id=..., write_scope="company"` 事务内重算。
- `export_range_trial_balance` / `export_range_account_ledger`：50,000 行、16MB 上限；`safe_csv_text` 防公式注入；写 `formal_book.range_export` 审计（含 `fingerprint` 与内容 `sha256`）。

`nexus_backend/app/posting/router.py`（+61 行）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/ledgers/{ledger_id}/trial-balance?start&end` | 连续科目余额表 |
| GET | `/api/ledgers/{ledger_id}/ledger/{account_id}?start&end` | 连续科目明细账 |
| GET | `/api/ledgers/{ledger_id}/books/export?kind&start&end&account_id` | CSV，头 `X-Source-Fingerprint`、`Cache-Control: private, no-store`、`X-Content-Type-Options: nosniff` |

## 3. 前端实现

- `src/formal-books-range.ts`（342 行）：类型 + **严格解析器**。`parseRangeTrialBalance` 校验 scope 的 ledger/firm/company/start/end、月份格式、64 位十六进制指纹、`coverage.closing = opening + debit - credit`；`parseRangeAccountLedger` 额外校验逐行余额递推与期末一致。另有 `csvFingerprint`（在前 3 行内找 64-hex）、`blockerLines`、`rangeSummary`。
- `src/FormalBooksRangePanel.tsx`（468 行）：起始/截止月份下拉来自 `GET /books/{id}/ledger` 的 `periods`（不用前端造月份）；「完整 / 不完整 + 阻断清单 + 指纹前 16 位」；按月覆盖表；科目余额表（更名标签）；点行下钻连续明细账；导出后用 `csvFingerprint(blob)` 与页面指纹比对，不一致就**拒绝交付该文件**。
- `src/FormalBooksWorkbench.tsx`（+24 行）：头部「连续期间账簿」入口 + 返回工作台。
- `src/formal-books-range.css`；`src/formal-books.css` 增加 `.formal-books-heading-actions`。

## 4. 先写失败测试，再改实现

`nexus_backend/tests/test_q03_continuous_books.py` 首轮 **8 passed / 4 failed**，四个失败都是契约问题而不是测试噪声：

| 失败用例 | 根因 | 处理 |
| --- | --- | --- |
| 未关账月份 | `UPDATE periods SET status='closed'` 触发 `period_reviewed_close`（缺回执/快照/依据） | 改用 `period_fixtures.restore_period_state` 走测试专用关账 |
| 缺月 | `idempotency_key="q03-dec"` 只有 7 位 | 改为 ≥8 位 |
| 期初不衔接 | 不均衡期初包过不了 `_checks`（期初包必须内部平衡） | 改为「合计一致、科目腾挪」，并**把续接判定从比合计改成按科目比** |
| 期间越权 | 删授权少传参数 | 改由「事务所 reviewer 仅授 2026-10」构造 |

最终 13 个用例（含跨年月份枚举）：

| 用例 | 断言要点 |
| --- | --- |
| `test_range_rejects_invalid_and_inverted_periods` | `2026-9`/`2026-13`/倒置 → 422 语义错误 |
| `test_range_enumerates_months_across_a_year_boundary` | `2025-12..2026-02` 连续三个月；241 个月拒绝 |
| `test_range_opens_from_first_period_and_sums_activity` | 期初 10,000.00；借 1,230 分；期末 10,012.30 |
| `test_range_does_not_add_later_opening_packages` | 次月期初包不重复计入 |
| `test_range_reports_open_period_without_hiding_it` | 未关账 → `complete=false` + 两个 `period_open`；关账后 `complete=true` |
| `test_range_reports_missing_month` | 中间缺月 → `missing_period`，覆盖表仍列三个月 |
| `test_range_reports_opening_discontinuity` | 合计一致、科目不一致 → `opening_mismatch` |
| `test_range_reports_unauthorized_period_and_hides_fully_invisible_ledger` | 仅可见 10 月时 11 月标 `unauthorized` |
| `test_range_rejects_ledger_the_actor_cannot_see` | 完全不可见 → 403/404 |
| `test_range_fingerprint_is_stable_and_scoped` | 同参同指纹、不同区间不同指纹、长度 64 |
| `test_account_ledger_range_carries_balance_across_months` | 余额跨月递推 10,000.30 → 10,012.30 |
| `test_range_csv_matches_page_and_escapes_formulas` | CSV 指纹 = 页面指纹；`=HYPERLINK` 被前缀转义 |
| `test_http_range_endpoints_require_period_bounds` | HTTP 层缺参/倒置 → 409/422 |

前端 `src/formal-books-range.test.ts` 10 个用例同样先行：scope 不符、指纹格式、覆盖不闭合、缺月不冒称完整、明细余额断链、他人科目、CSV 指纹解析、比 CSV 列名行、路径与非法月份。

## 5. 门禁证据（本机实跑）

| 门禁 | 命令 | 结果 |
| --- | --- | --- |
| 后端定向 | `pytest test_q03_continuous_books.py -q` | **13 passed**（36s） |
| 后端回归 | `pytest test_posting_service test_posting_api test_q03_continuous_books test_stage10_ledgers -q` | **28 passed**（72s） |
| 前端单测 | `npx vitest run src/formal-books` | **44 passed / 4 files** |
| 类型 | `npx tsc -p tsconfig.app.json --noEmit` | 0 错 |
| 前端规范 | `npx eslint`（改动文件）、`npx prettier --check` | 通过 |
| 后端规范 | `ruff check nexus_backend/app/posting`、`ruff format --check` | 通过（顺手把 2 个文件按锁定版本格式化） |
| 浏览器 dev | `npx playwright test e2e/formal-books-range.spec.ts` | **2 passed**（1.9m，桌面 + 手机） |
| 浏览器 preview | `E2E_FRONTEND_MODE=preview` 跑新用例 + 既有月度交付用例 | **9 passed**（50s） |
| 既有用例回归 | `npx playwright test e2e/formal-books-monthly-package.spec.ts`（拆分夹具后） | **7 passed**（2.3m） |

浏览器用例的真实链路：API 建企业 → 10 月账套 → 独立核准并冻结期初 → 绑定 → 记一笔 100 元凭证 → **用产品自己的「创建期间」接口**生成 11 月账套 → 11 月期初（=10 月期末 10,100.00）→ 绑定 → 记一笔 200 元凭证 → 页面选「2026-10 至 2026-11」，断言：合计 10,300.00（期初只算一次）、两个月各自 `period_open`、按月覆盖两行「可用」、下钻同时看到两个月凭证与 10,300.00 余额、导出 CSV 首部指纹与页面一致、手机端无横向溢出、`employee` 身份读范围与导出均 404、倒置区间 422。

## 6. e2e 抓到的真实缺陷（已修）

CSV 首行是**给人看的列名行**，取值范围行在第二行；前端指纹解析只读首行，取不到 64-hex → 判定「CSV 与页面来源不一致」并拒绝交付文件。

- 失败方式是正确的（fail-closed：宁可不出文件），但正常导出也被拦住了。
- 修法：`csvFingerprint` 在前 3 行内找末列为 64-hex 的行；补一条「跳过列名行」的单测。
- 这类问题只有真跑浏览器 + 真下载文件才暴露，单测与接口测试都看不见。

## 7. 未覆盖与边界（不得当成已完成）

1. **PostgreSQL 16/17 未验证**：本机无 docker/psql（WSL 内只有 PG 14.24），范围查询的 SQL 与授权函数只在 SQLite 上跑过；`scripts/ak05_postgres_gate.py` 未执行。
2. **跨年整链未端到端**：只单测了月份枚举跨年；「2025-12 与 2026-01 两个物理账套 + 跨年期初政策」没有构造。
3. **90+ 个 postgres 标记用例本机跳过**，`pytest nexus_backend/tests` 全量未跑完（3,985 条）。
4. **全量 106 条核心 e2e 未重跑**：只跑了本次相关的两个 spec 与既有月度交付回归。
5. **明细账 CSV 导出**（`kind=account_ledger`）只在后端用例里验过指纹与公式转义，浏览器未点击该按钮。
6. **远端 CI 未执行**：所有结论仅代表本机。

## 8. 变更清单

```text
新增  nexus_backend/tests/test_q03_continuous_books.py   394
新增  src/formal-books-range.ts                          342
新增  src/FormalBooksRangePanel.tsx                      468
新增  src/formal-books-range.test.ts                     195
新增  src/formal-books-range.css
新增  e2e/formal-books-range.spec.ts                     106
新增  e2e/formal-fixtures.ts                             348   （从既有 spec 抽出的共享夹具）
修改  nexus_backend/app/posting/service.py              +380
修改  nexus_backend/app/posting/presentation.py         +158
修改  nexus_backend/app/posting/router.py                +61
修改  src/FormalBooksWorkbench.tsx                       +24
修改  e2e/formal-books-monthly-package.spec.ts          -252   （夹具外移，用例本身未改）
```

合计：5 个新增源文件 + 2 个新增测试夹具/样式 + 6 个修改文件；`nexus_backend/migrations` 字节零改动。
