# 本轮交付报告（包 01 / 包 02 · 阶段 1–4）

- 仓库：`C:/Users/Fei/Desktop/AI应用/nexus-ai-kuaiji`
- 起点 HEAD：`97b40943`（工作树干净）
- 本轮结束时：**未提交**（按约定由架构师独立审计后同步/推送），工作树 43 项改动
- 新增迁移：`nexus_backend/migrations/postgres/329_workflow_business_binding.sql`、`nexus_backend/migrations/sqlite/236_workflow_business_binding.sql`（历史迁移 `<=328` / `<=235` 未改）

## 阶段 1：修正三种可能误导财务判断的结果

### 1A T+ 回执过账误判

| 文件 | 取舍 |
| --- | --- |
| `nexus_backend/app/integrations/roundtrip.py` | 科目比对由「有交集即通过」改为**严格集合相等**；`complete` 增加 `bool(row["external_code"])`；公式注入前缀检查覆盖对方凭证号；缺凭证号时原因写明「对方未提供凭证号，只能确认接收」 |
| `nexus_backend/app/integrations/service.py` | 冻结凭证金额聚合删掉 `float(...) * 100`，改 `money_to_cents()`（`Decimal`→整数分），格式损坏直接 409；`evidence_verified` 由恒 `True` 改为 `bool(parsed["posted"])`；`verification_label` 收敛为三档（已核实过账 / 凭据待核 / 对方已接收） |
| `src/IntegrationWorkbench.tsx`、`src/integration.css` | 回执徽章带 `data-tier`（posted / pending / received）三色区分，人工声明不再显示为「已核实过账」 |
| `nexus_backend/tests/test_plt05_roundtrip_contract.py`、`test_plt05_return_import.py` | 覆盖完整一致、缺行、多行、少一科目、脏科目、对方凭证号为空、公式前缀注入、手工登记不得冒充逐笔核实 |

### 1B 集团首页法人分母与合并试算口径

- `nexus_backend/app/observability/insights.py`：先取事务所全部纳管法人，再关联账套与已复核报表；`kpis` 新增「已纳管 / 已开账 / 已报送」三档（多账套法人只算 1 家，另报账套数）；未开账法人留在分母并点名（`reason="not_opened"`，提示「本期未开账」）；`mixed_standards=True` 时顶层金额置 `None`（不可比），数字只留在 `standards_breakdown`。
- `nexus_backend/app/reports/consolidation.py`：新增 `worksheet_totals()`，顶层 `totals` 只汇总同准则对，并输出 `totals_by_standard` 与 `not_merged_totals`（异准则未合并往来，明确「未计入同口径可比合计」）。
- 前端：`src/firm-insights-coverage.tsx`（三档计数、缺报点名、分准则小计）、`src/FirmInsights.tsx` 接入；新增 `src/firm-insights-coverage.render.test.ts`。
- 测试：`test_plt07_queue_and_insights.py` 新增 5 法人场景（1 家未开账、1 家双账套、2 家不同准则）；`test_consolidation_worksheet.py` 新增可复算断言。

### 1C 印花税三态

- `nexus_backend/app/tax/four_tax.py`：`stamp_contracts` / `stamp_rates` / `stamp_zero_confirmed`；三态 `unprovided`（`total_cents=None`，不生成分录）、`zero_confirmed`（0 元，明确留痕）、`computed`（走 `engines.stamp_duty()`）；零业务确认 + 合同同传 → 422。
- `nexus_backend/app/tax/accrual.py`：`unprovided` 不再把 `None` 当 0，回执透传 `stamp_duty_status` / `stamp_duty_notice` / `notices`。
- `nexus_backend/app/tax/router.py`：`FourTaxIn` 与 `/four-tax`、计提预览/申请全链路透传。
- `src/TaxAccrualPanel.tsx`：状态提示 + 「已核实本期无应税合同（确认零业务）」勾选（默认不勾，勾选后按 0 元计提并留痕），空明细也不隐藏提示。
- 测试：`test_four_tax_stamp_status.py`（7 例）、`test_tax_accrual_posting_http.py` 新增接口三态用例、`src/tax-accrual-panel.test.ts` 追加 5 例。

## 阶段 2：首屏瘦身 + 第一张真实业务单与异常办理

### 2A 首屏瘦身（未改预算阈值）

- `src/App.tsx`：`WorkspaceRouteContent`、`ClientPortalShell`、`AssistantDrawer`、`AgentNavigation`、`ActivityTimer`、`AccountIdentity`、`RoleQuickStart` 全部改 `React.lazy` + `<Suspense>`（工作台/客户门户有加载文案，导航与抽屉 `fallback={null}`）。
- `src/styles.css` → `src/queue-panels.css`：队列面板样式（含 @media）迁出，由 6 个懒加载面板 `import`，首屏 CSS 不再包含这些规则。
- 实测（未改 `scripts/frontend_budget.mjs`）：

| 指标 | 数值 | 上限 | 余量 |
| --- | --- | --- | --- |
| initialGzip | 165,053 B | 176,000 B | 10,947 B（≥10 KB） |
| homeCss | 130,426 B | 145,000 B | 14,574 B（≥10 KB） |

### 2B 报销单与工作流同事务绑定

- 迁移 `postgres/329_workflow_business_binding.sql` / `sqlite/236_workflow_business_binding.sql`：
  - `workflow_instances.book_id` 放宽为可空，仅 `subject_type='business_event'` 允许为空；新增 `subject_version`；
  - 两条 CHECK 约束 + 部分唯一索引 `(firm_id, company_id, subject_type, subject_id, subject_version)` 防重复绑定；
  - RLS 三条策略按 `book_id IS NOT NULL` 分支，`book_id IS NULL` 需 `firm_id=actor_firm() AND company_visible(company_id)`，INSERT 仍强制 `created_by=actor_user()`；
  - 新触发器函数 `workflow_instance_subject_lock()`：`subject_id` / `subject_version` 绑定后不可改；
  - SQLite 走重建表（`workflow_instances_n01` → 复制 → 删旧 → 改名），保留原索引与两条禁改禁删触发器；
  - PG 侧沿用 `batch2_company_write_gate`，`REVOKE/GRANT` 与 `FORCE RLS` 齐备。
- `nexus_backend/app/workflow/binding.py`（新增）：同事务绑定层；`bind()` 在提交那一刻锁定 `definition_id` / `definition_version` / `path_json`；部门审批只推进 `role='manager'` 节点（无该节点时只写跳过日志），财务审批推进到终态；自审 403、终态/挂起/已过账 409；退回补件挂起、重新提交恢复同一实例。
- `nexus_backend/app/business/service.py`：`submit()` 在 `_bump` 之后调用 `_bind_workflow(..., new_version)`；`approve_department/finance` 同步 `_advance_workflow`；`return_event` 挂起实例；`_event_json()` 附带只读 `workflow_instance` 摘要；无生效定义时完全保留默认审批流，且**员工事务从不写 `workflow_definitions`**。
- 测试：`test_workflow_business_binding.py` 9 例（绑定生效版本、重复提交不建第二实例、并发审批只一人成功、审批同步原单与实例、退回挂起再重提恢复、发版后存量单锁旧版、无定义兼容、绑定不写定义表、低金额自动通过不阻塞业务审批）。

### 2C「待我处理」死循环与月结阻断直达

- `src/WorkspaceHome.tsx::openLatest()`：`410` → `completed`（带服务端 detail）并清空旧详情、刷新队列；`403/404/401` → `forbidden`/`missing` 同样清空刷新；全部加 `alive.current` 与 `scope.current === started` 竞态保护。
- 月结阻断直达复用既有 `action_target` + `CloseCheckAction`（有权限直达、无权限显示处理岗位），未越权泄露。
- E2E：`e2e/task-unavailable-terminal.spec.ts`（1366×900 与 390×844：陈旧 → 办结 → 打开最新内容 → 「待办已办完」并刷新；筛选保留；无横向溢出）。

## 阶段 3：累计利润可溯源 + 合并试算懒加载入口

### 3A 企业所得税累计利润来源

- `nexus_backend/app/tax/profit_source.py`（新增）：只读 `report_runs`，优先已复核/已发布利润表快照，其次试算快照；返回 `suggested_profit_cents` / `source_kind` / `source_label` / `source_period` / `source_version` / `is_final_report` / `report_run_id` / `notice`；无来源时 `null` 并说明「不猜测、不拿账载税额代替」。
- `nexus_backend/app/tax/context.py` 返回 `cumulative_profit`；`nexus_backend/app/tax/router.py` 新增 `GET /api/tax/books/{book_id}/cumulative-profit`（跨期 409）。
- `src/TaxAccrualPanel.tsx`：来源口径/期间/版本显著展示，一键预填；人工改金额必须填「人工调整原因」（≥4 字）才允许提交，原因随申请 `reason` 透传。
- 测试：`test_tax_cumulative_profit_source.py` 6 例（已复核优先、试算标注、superseded/expired 排除、缺利润不猜、无来源说明、跨期不串）+ 前端 4 例。

### 3B 合并试算懒加载视图

- `src/firm-insights-consolidation.tsx`（新增）：折叠面板按需请求 `GET /api/consolidation/worksheet`，展示纳入/缺报/无权法人、按准则分组的同口径可比合计、异准则未合并往来、未达差额明细，并固定展示「集团管理合并试算（非经审计法定合并报表）」；顶层数字绝不把两套准则相加。
- 接入 `src/FirmInsights.tsx`（本身已是懒加载块），新增 `src/firm-insights-consolidation.render.test.ts`（5 例，含「不出现 140,000.00 混算值」）。

## 阶段 4：同候选统一回归（顺序执行，无并发）

| # | 命令 | 结果 |
| --- | --- | --- |
| 1 | `scripts/check_test_env.py` | `environment matches lock`，exit 0 |
| 2 | `scripts/check_migration_idempotency.py` | exit 0（327/328/329 列「需专项检查：dynamic_ddl（EXECUTE）」，与既有 318/321/324 同类） |
| 3 | `scripts/check_secrets.py` | `no secrets found` |
| 4 | `uvx ruff==0.17.0 check nexus_backend/app services/api_gateway scripts` | 修掉 `binding.py` 未用 import 后 `All checks passed!` |
| 5 | `uvx ruff==0.17.0 format --check`（同路径） | 重排 2 个文件后 `482 files already formatted` |
| 6 | 受影响后端回归（migration/PG 卫生/写作用域/stage25/stage19 + integrations/insights/consolidation/tax/business/workflow/b04/value_baseline/plt09 + workspace/business 补充） | **185 passed** 与 **78 passed, 1 skipped**（合计 263 通过） |
| 7 | `npm run typecheck`、`npm run lint`、`npm run format:check` | 全部 exit 0，`All matched files use Prettier code style!` |
| 8 | `npm test -- --run` | **168 文件 / 1503 用例通过**（3.93s） |
| 9 | `TEMP/TMP=tmp_test/tmp npm run build && npm run test:frontend-budget` | `status: passed`、`violations: []`；initialGzip **165,053 / 176,000**、homeCss **130,426 / 145,000** |
| 10 | `npx playwright test e2e/workflow-studio.spec.ts e2e/task-unavailable-terminal.spec.ts e2e/reimbursement-workflow-binding.spec.ts` | **6 passed (33.1s)** |

## 文件清单（43 项）

后端（14）：`app/business/service.py`、`app/integrations/roundtrip.py`、`app/integrations/service.py`、`app/observability/insights.py`、`app/reports/consolidation.py`、`app/tax/accrual.py`、`app/tax/context.py`、`app/tax/four_tax.py`、`app/tax/profit_source.py`（新）、`app/tax/router.py`、`app/workflow/binding.py`（新）、迁移 `postgres/329_*.sql`（新）、`sqlite/236_*.sql`（新）、测试 10 个（含 4 个新增）。

前端（19）：`src/App.tsx`、`src/FirmInsights.tsx`、`src/IntegrationWorkbench.tsx`、`src/TaxAccrualPanel.tsx`、`src/WorkspaceHome.tsx`、`src/firm-insights-consolidation.tsx`（新）、`src/firm-insights-coverage.tsx`（新）、`src/queue-panels.css`（新）、`src/integration.css`、`src/firm-insights.css`、`src/styles.css`、6 个队列/复核/情报面板（各加一行 css import）、`src/firm-insights-consolidation.render.test.ts`（新）、`src/firm-insights-coverage.render.test.ts`（新）、`src/tax-accrual-panel.test.ts`。

E2E（2）：`e2e/task-unavailable-terminal.spec.ts`（新）、`e2e/reimbursement-workflow-binding.spec.ts`（新）。

## 边界声明

- 未执行 `git commit` / `git push`，未连接远端 Supabase；PG 迁移只在 SQLite 上实跑结构校验，**未连真实 PG 库执行**。
- 全部评测与 E2E 使用本地演示身份与合成数据（虚构企业名/金额），未调用真实模型或外网。
- E2E 的报销单绑定用例复用本地演示企业（员工/部门/财务三位演示身份），未新增客户数据。
- 「集团管理合并试算」相关页面与接口均固定标注「非经审计法定合并报表」。

## 架构师独立审计修补与生产库同步记录（2026-11-24）

独立逐行审计发现并直接修补了 7 处生产/口径/内控隐患：

1. **PostgreSQL 布尔类型不匹配修复**：`nexus_backend/app/workflow/binding.py` 插入 `workflow_instances` 的 `terminal_external_locked`/`irreversible_reached` 由整数 `0, 0` 改为 `FALSE, FALSE`，避免 PG 报 `DatatypeMismatch`。
2. **合并试算前端真实接口与契约对齐**：`src/firm-insights-consolidation.tsx` 修正请求路径 `/reports/consolidation/worksheet` → `/consolidation/worksheet`；后端 `consolidation.py` 补充 `left_company_name`/`right_company_name`；前端兼容后端真实 `included_companies[].name`、`missing_companies`/`denied_companies` 字符串列表并通过 `companyNames` 展示中文名。
3. **合并试算多准则分组严格禁混加**：`consolidation.py::worksheet_totals()` 当存在 2 种及以上准则分组时，`totals.comparable=False` 且顶层 `matched_cents`/`difference_cents` 置 `None`，前端隐藏 `<tfoot>` 混算行，仅保留分准则可比小计。
4. **报销工作流内控加固**：`binding.py::advance()` 移除财务审批 `while True` 连续穿过多个审批节点的漏洞（单次审批只推进一个审批节点），补齐超额双人审批要求两位不同审批人（同人兼任部门/财务被 403 拒绝）与真实 `to_node` 日志；`business/service.py::_bind_workflow()` 在退回补件修改金额后将旧挂起实例留痕作废并按新版本重路由，防止低金额路径被跨门槛沿用。
5. **双库迁移加固**：PG `329` 收紧企业级实例 RLS，必须通过 `accounting.business_event_visible(subject_id, firm_id, company_id)`（读取与管理接管含 `firm_manager`/`company_manager`）；SQLite `236` 顶部加 `PRAGMA defer_foreign_keys=ON;` 保护存量日志库升级。
6. **经营看板双账套完备性修复**：`insights.py` 不再用 `setdefault` 吞掉同一法人的其余未复核账套，且 `complete` 必须同时满足 `not missing_companies and not pending_book_reports`。
7. **计提面板切账套防串数**：`src/TaxAccrualPanel.tsx` 在 `bookId`/`period` 切换时立即清空旧账套累计利润、调整原因与申请状态。

### 审计后复验与远端 Supabase 同步结果

- 后端规范与核心回归：`ruff check` / `format --check` 全绿（482 files）；受影响 10 个核心后端测试文件 **134 passed**（含新增双人审批 403、退回改金额重路由、多准则不混加与双账套完备性断言）。
- 前端规范、单测、构建与预算：`typecheck` / `lint` / `format:check` 全绿；`vitest` **168 文件 / 1504 用例通过**；`build` + `test:frontend-budget` 通过（`initialGzip 165,044 / 176,000 B` 余量 `10,956 B`，`homeCss 130,426 / 145,000 B` 余量 `14,574 B`，`violations: []`）。
- 浏览器端到端：`npx playwright test` 三组双视口（1366/390）**6 passed (24.6s)**。
- 远端 Supabase 生产库（项目 `qgzketopnkzquotddwha`）：已事务原子同步 `v329`（累计 `316` 条迁移，校验和 `d52a24881db906ba3432a8def99528cf00bd01cc4b8888a9acf0153ff37dc18f` 逐字节一致），实测 `book_id` 可空、`subject_version` 字段、2 条 `CHECK`、唯一索引 `idx_workflow_instance_business_subject`、`FORCE RLS`、3 条收紧策略及 `workflow_instance_subject_lock` 触发器全部就位，且策略引用的 7 个函数均对 `accounting_app` 具备执行权限。
