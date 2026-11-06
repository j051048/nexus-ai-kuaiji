# IMP08 验收记录：问会计助手的三类可执行证据

- 日期：2026-11-06
- 工程状态：本地离线/HTTP/UI 通过；PG16/17 与真实业务数据 pending。
- 外部状态：真实模型、真实客户数据、真人试用未授权，不冒充已验证。
- 候选：HEAD `143d4aea7154b1905b96ab757ecd14865354b641`，source 1927 / `e6a947031fe9e3b4bd23b522b29ef46bbfddd89232a5c9d989b70a170f44baa5`，migration 338 / `2c96acd52de191347a2365962e879340918fad013786faa260b4b13140646116`（IMP08 代码/测试/UI 计入候选；文档不计）。
- 范围：费用跨期差额、票据与凭证金额差异；交付阻断沿用既有 `investigation.py` 的三类只读调查。

## 已有能力（复用，不重建）

- `nexus_backend/app/assistant/investigation.py`：`voucher_status`、`close_blockers`、`delivery_status` 三类只读工具；每步重授权、最后重查、写审计。
- `POST /api/assistant/investigations`、`GET /api/assistant/investigations/capability`。
- `src/InvestigationEvidencePanel.tsx`：交付页“为什么本月还不能交付”，严格解析 findings/missing/coverage/fingerprint。
- 版本化凭证、月结、报表、月度包与签收均已有独立契约，IMP08 不修改这些状态机。

## 实际修改

后端：
- `nexus_backend/app/assistant/analysis.py`：新增 `KINDS=("expense_bridge","invoice_voucher_diff")`、`MAX_ROWS=100`。
  - `analyze(service, actor, book_id, period, kind, **params)`：kind 白名单、参数白名单、未知参数/类型 422。
  - `_expense_bridge`：按当前账套与同企业上期账套（或显式 `compare_period`）重授权；按 `voucher_lines.code_snapshot` 汇总借贷差；无上期返回 `partial` + `missing=["previous_period"]`。
  - `_invoice_voucher_diff`：目标凭证必须属于当前 book/period；比较借方合计与 `voucher_sources.amount_cents`；限制行数，超限 `partial` + `has_more`。
  - `validate_claim`：拒绝跨企业、错误金额、因果措辞、无 fact_ids 的叙述；金额只认 cents 事实集合。
  - `_result` 输出 `company_id`、`facts`、`coverage`、`missing`、`handoff`、`model_calls=0`、`real_model_evaluated=false`、`fingerprint`。
- `nexus_backend/app/assistant/router.py`：`POST /api/assistant/analyses`、`GET /api/assistant/analyses/capability`；受 `ACCOUNTING_ASSISTANT_INVESTIGATION` 开关控制，未开启返回 403。

前端：
- `src/AnalysisEvidencePanel.tsx`：只读差异核对面板；费用/票据两类；显示金额、覆盖、未检查、handoff、fingerprint 和回账套链接；无写操作。
- `src/analysis-evidence.css`；挂载于 `src/BookWorkspaceShell.tsx` 的账套工具栏下方。

## 08-T 对照

| 编号 | 要求 | 结果 |
| --- | --- | --- |
| 08-T1 | 错误金额、因果、主体拒绝 | 通过：claim 金额/因果/跨企业拒绝；错期 422 |
| 08-T2 | 跨企业/错账套/币种 | 跨企业凭证 404、错期 422；当前仅 CNY，未伪造多币种 |
| 08-T3 | 撤权/版本/租约 | 撤权 fail-closed 403 通过；版本/租约动态测试 pending |
| 08-T4 | 覆盖与未检查范围 | 无上期 partial + `previous_period`；票据分页 partial/has_more |
| 08-T5 | 注入/非法参数 | 非法 kind、注入科目代码 422 |
| 08-T6 | unknown/部分结果 | 无来源/无上期 partial；不生成 0 |
| 08-T7 | UI 无写入 | Playwright 监听 PUT/PATCH/DELETE=0；真实页面、只读响应替身 |
| 08-T8 | PG16/17 | PG17 smoke **1 passed/2.30s**（`ak05-pg-runs/7495257f…`）；PG16 **1 passed/5.98s**（`b8b4d1dc…`）：真实 PG 受限连接、`expense_bridge` 无上期 partial/current_total 0/fingerprint。正向票据差异仍只有 SQLite 证据 |

## 命令与结果

| 套件 | 命令 | 结果 | 证据 |
| --- | --- | --- | --- |
| 分析契约 | `.venv/Scripts/python.exe -m pytest -q nexus_backend/tests/test_imp08_analysis.py` | **8 passed / 24.53s** | 终端输出 |
| 助手回归 | 同命令加 `test_assistant_task_workflow.py test_assistant_task_baseline.py` | **35 passed / 44.26s** | 终端输出 |
| PG17 smoke | `scripts/test_accounting_postgres_isolated.py --config …/pg17-recovery_source.json tmp_test/imp/imp-20261106-a/test_imp08_analysis_postgres.py` | **1 passed / 2.30s** | `tmp_test/ak05-pg-runs/7495257f071e40389dffa9a4915e05b0/results.xml` |
| PG16 smoke | 同上，`pg16-recovery_source.json` | **1 passed / 5.98s** | `tmp_test/ak05-pg-runs/b8b4d1dc2b15404fa5d2b7ce2e036f68/results.xml` |
| UI | `npx playwright test --config=playwright.imp08.config.ts` | **1 passed / 11.4s** | `tmp_test/imp/imp-20261106-a/imp08-analysis.png` |
| 类型/构建 | `npm run typecheck`；`TMP=tmp_test/tmp TEMP=tmp_test/tmp npm run build` | exit 0 / 0 | `/tmp/imp08-tsc.log`、`/tmp/imp08-build.log` |
| 预算 | `node scripts/frontend_budget.mjs dist` | **passed**；initial 565,956B/gzip 175,047；home 593,479B/gzip 185,602；CSS 144,373B | `tmp_test/imp/imp-20261106-a/imp08-budget.json` |

## 诚实限制

- 未接真实模型；`real_model_evaluated=false`，所有金额/差额来自 SQL 聚合，不来自模型。
- UI 浏览器测试用了只读响应替身（capability + POST），用于验证渲染/无写入；真实 HTTP 路径已由 pytest 的 flag 测试覆盖。
- PG16/17、真实多币种、租约/版本并发、真实业务账套未验证；不得声称生产可用。
- `expense_bridge` 明细分页、总额全量；报告已显式写 `coverage.rows_read` 当前页，避免“全量已核对”的误读。
