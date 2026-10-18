# 账本优先：代码改进分阶段与任务清单

日期：2026-10-18　对应业务计划：[6 家客户、6 个月双轨执行计划](LEDGER_FIRST_6_CLIENT_EXECUTION_PLAN.md)
性质：工程规划，不含代码改动。估算单位：人日（pd），含开发+自测，不含评审与缓冲。

---

## 0. 三条设计原则

1. **不重复建设**：复核、证据、科目、长期账套、月结预检、往来核销、银行匹配、外部余额、审计、RLS 全部复用现有模块；新账本只补“过账、余额、结账、报表、税务、固定资产”。
2. **账本为核心，导出为旁路**：`drafts` 仍是复核对象；审核通过后过账到 `journal_entries`；导出/回执/T+ 接入降为旁路与对照。
3. **可回退**：每个阶段都能停在“双轨”，不切外部软件也能运行；账本数据可导出、可重建、可对照。

---

## 1. 目标架构

```
┌─────────────────────────────────────────────────────────────┐
│ 现有：sources / OCR / 银行 / 发票 / 业务事项 / 资料清单        │
└───────────────────────────┬─────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│ 现有：drafts + draft_lines（复核对象，证据链、版本、审核）      │
└───────────────────────────┬─────────────────────────────────┘
                            ↓ approve + post
┌─────────────────────────────────────────────────────────────┐
│ 新增：ledger 核心                                             │
│  journal_entries / journal_lines / voucher_sequences          │
│  opening_balances / account_balances / closing_runs           │
│  report_mappings / report_snapshots / tax_workpapers          │
│  fixed_assets / depreciation_schedules                        │
└───────────────────────────┬─────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│ 现有 + 扩展：closing 预检 / settlements 往来 / reconciliation │
│ 现有：delivery 回执 / exports 导出（旁路）                     │
└─────────────────────────────────────────────────────────────┘
```

**模块落点**：

```
nexus_backend/app/ledger/
  __init__.py
  models.py          # 请求/响应模型
  router.py          # /api/books/{book_id}/journal 等
  service.py         # 编排：post / reverse / query
  posting.py         # 过账引擎与不变量
  balances.py        # 余额、试算、明细账
  closing.py         # 结账、结转、反结账
  reports.py         # 报表映射、快照、三表
  tax.py             # 税务底稿
  assets.py          # 固定资产、折旧
  migration.py       # 期初、历史凭证导入
  sequences.py       # 凭证号
  rules.py           # 共用校验（借贷、期间、科目、辅助）
```

---

## 2. 复用矩阵（不要重复造）

| 能力 | 现有模块/表 | 新账本怎么用 |
|---|---|---|
| 科目/模板 | `accounting/catalog.py`、`accounts`、`journal_templates` | 直接复用；报表映射新增，不改科目体系 |
| 长期账套 | `ledgers`、`ledger_periods` | 账本表带 `ledger_id`，跨月连续性用它 |
| 月度账 | `books`、`periods` | 账本表以 `book_id` 为期间主键；`period` 冗余便于查询 |
| 复核/证据 | `drafts`、`draft_lines`、`sources`、`source_evidence` | 过账从已审核草稿读取；不新建复核体系 |
| 月结预检 | `closing/service.py`、`closing_issues` | 复用预检；新增“执行结账/反结账”调用账本引擎 |
| 外部余额 | `monthly_baselines`、`monthly_checks` | 作为期初/期末对照来源；`opening_balances` 关联 baseline id |
| 往来核销 | `settlements` | 扩展账龄/子账查询；不新建第二套应收应付 |
| 银行匹配 | `reconciliation`、`sources`（银行流水） | 扩展“流水↔凭证”匹配；不重复导入流水 |
| 交付/导出 | `delivery`、`exports` | 保留为旁路；报表导出新增，不替换回执核验 |
| 审计 | `audit_events` | 过账/红冲/结账/反结账全部写审计 |
| 权限/RLS | `access.py`、迁移模板 | 新表沿用 RLS + FORCE RLS + 写门 + 最小 GRANT |
| 任务/投影 | `workspace` | 后期把“待过账/待结账”接入投影，不另建待办 |

---

## 3. 阶段划分

| 阶段 | 周次 | 主题 | 退出标准 |
|---|---|---|---|
| **A 设计定稿** | W1–W2 | ADR、迁移模板、模块骨架、金标准框架 | 设计签字；骨架可启动；映射 v0 签字 |
| **B 账本核心 v1** | W3–W10 | 分录、过账、余额、结账、三表、前端 | 2 户影子 1 个月；核心测试通过 |
| **C 影子与辅助** | W11–W16 | 期初/历史导入、往来、银行、固定资产、税务、双轨 | 6 户连续 2 个月零未解释差异 |
| **D 客户可见** | W17–W22 | 报表签收、切换工具、回退、UAT | 2 户签收；回退演练通过 |
| **E 全量收尾** | W23–W26 | 6 户切换、SOP、监控、验收 | 6 户签收；G5 决策 |
| **F 单轨与 Agent** | W27+ | 单轨月结、Agent 工具、计费 | 4 月账期单轨成功 |

---

## 4. WBS（可执行任务）

### 阶段 A：设计定稿（W1–W2，约 22 pd）

| ID | 任务 | 产出 | pd | 负责 | 依赖 |
|---|---|---|---|---|---|
| A1 | ADR-001 账本模型：book/ledger/period/entry 关系 | ADR + ERD | 2 | BE+CA | — |
| A2 | ADR-002 过账/红冲/结账/反结账规则 | ADR + 状态机 | 2 | BE+CA | A1 |
| A3 | ADR-003 报表映射与口径版本 | ADR + 映射表 | 2 | CA+BE | A1 |
| A4 | 迁移 102–104 模板：RLS/写门/GRANT/兼容头 | SQL 草案 | 3 | BE | A1 |
| A5 | `ledger` 模块骨架、路由、权限位 | 可启动空模块 | 2 | BE | A4 |
| A6 | API 契约 v0（OpenAPI） | 契约文档 | 1 | BE | A5 |
| A7 | 金标准数据集框架 + 2 户样本 | 测试夹具 | 3 | QA | A1 |
| A8 | 前端路由/空页面/导航 | 可进入空页 | 2 | FE | A6 |
| A9 | 双轨差异日志表 + 模板 | 表 + CSV 模板 | 2 | BE+CA | A2 |
| A10 | 科目表/报表映射 v0 评审签字 | 签字记录 | 3 | CA | A3 |
| **合计** | | | **22** | | |

### 阶段 B：账本核心 v1（W3–W10，约 104 pd）

#### B1 数据模型与迁移

| ID | 任务 | 产出 | pd | 负责 |
|---|---|---|---|---|
| B1 | `journal_entries` / `journal_lines`（含科目属性快照） | 迁移 102 | 2 | BE |
| B2 | `voucher_sequences` + 并发生成 | 迁移 102 | 2 | BE |
| B3 | `opening_balances`（关联 baseline） | 迁移 103 | 2 | BE |
| B4 | `account_balances` + 重建命令 | 迁移 103 | 3 | BE |
| B5 | `closing_runs` + 期间锁集成 | 迁移 103 | 2 | BE |
| B6 | `report_mappings` + 小企业准则种子 | 迁移 104 | 3 | BE+CA |
| B7 | `report_snapshots` | 迁移 104 | 2 | BE |
| B8 | `drafts.journal_entry_id` / `posted_at` + 回填 | 迁移 105 | 2 | BE |

#### B2 过账与余额

| ID | 任务 | 产出 | pd | 负责 |
|---|---|---|---|---|
| B9 | 过账引擎 + 不变量（借贷、期间、科目、辅助） | `posting.py` | 5 | BE |
| B10 | 幂等键 + 并发（同草稿只过一次） | 测试 | 3 | BE |
| B11 | 红冲/调整（原凭证关联） | `reverse` | 3 | BE |
| B12 | 手工凭证流程（草稿→审核→过账） | API+测试 | 3 | BE |
| B13 | 过账/红冲审计与权限（post/close/reopen） | 权限位 | 2 | BE |
| B14 | 试算平衡（期初/本期/期末） | `trial_balance` | 3 | BE |
| B15 | 明细账/辅助账查询 | `ledger_detail` | 3 | BE |
| B16 | 分页与性能（索引、余额快照） | 基准 | 2 | BE |

#### B3 结账与报表

| ID | 任务 | 产出 | pd | 负责 |
|---|---|---|---|---|
| B17 | 与现有 `closing` 预检集成 | `precheck` | 3 | BE |
| B18 | 损益结转 | `close_period` | 4 | BE+CA |
| B19 | 年末结转（未分配利润） | `close_year` | 3 | BE+CA |
| B20 | 反结账（权限+原因+审计） | `reopen` | 2 | BE |
| B21 | 资产负债表 | `balance_sheet` | 3 | BE+CA |
| B22 | 利润表 | `income_statement` | 3 | BE+CA |
| B23 | 现金流量表（间接法，管理参考） | `cash_flow` | 4 | BE+CA |
| B24 | 报表下钻（行→科目→凭证→证据） | drill-down | 3 | BE |
| B25 | 报表导出 PDF/Excel | export | 2 | BE |

#### B4 前端与测试

| ID | 任务 | 产出 | pd | 负责 |
|---|---|---|---|---|
| B26 | 凭证列表/详情 + 过账/红冲 | 页面 | 5 | FE |
| B27 | 试算平衡页 | 页面 | 3 | FE |
| B28 | 明细账页 | 页面 | 3 | FE |
| B29 | 三表页面 + 期间选择 + 下钻 | 页面 | 5 | FE |
| B30 | 结账/反结账对话框 | 页面 | 2 | FE |
| B31 | 单元/属性测试（借贷、序列、幂等、红冲） | 测试 | 4 | QA+BE |
| B32 | 集成测试（过账→余额→报表；结账→拒写） | 测试 | 4 | QA+BE |
| B33 | 2 户影子 1 个月金标准 | 报告 | 4 | QA+CA |
| B34 | 性能基线（1 万/10 万行） | 报告 | 2 | BE |

### 阶段 C：影子与辅助模块（W11–W16，约 83 pd）

| ID | 任务 | 产出 | pd | 负责 |
|---|---|---|---|---|
| C1 | 期初余额导入 + 勾稽 | 导入器 | 4 | BE |
| C2 | 历史凭证导入/回填（迁移标记） | 导入器 | 4 | BE |
| C3 | 迁移映射 UI（科目/辅助/往来） | 页面 | 4 | FE |
| C4 | 迁移验证报告（差异清单） | 报告 | 3 | BE+CA |
| C5 | 应收应付子账 + 账龄 | 查询/页面 | 5 | BE |
| C6 | 核销与总账勾稽 | 扩展 settlements | 4 | BE |
| C7 | 银行流水导入 + 匹配 | 扩展 reconciliation | 5 | BE |
| C8 | 未达项/余额调节表 | 页面 | 3 | BE+FE |
| C9 | 银行对账报表 | 报表 | 2 | BE |
| C10 | 固定资产卡片/登记 | 页面+API | 4 | BE+FE |
| C11 | 折旧计提/批量运行 | 引擎 | 4 | BE |
| C12 | 处置/清理 | 功能 | 2 | BE |
| C13 | 税务底稿：增值税/附加税 | 模板+页面 | 4 | BE+CA |
| C14 | 企业所得税预缴底稿 | 模板 | 3 | CA+BE |
| C15 | 印花税底稿 | 模板 | 2 | CA+BE |
| C16 | 税务差异报告 | 报告 | 2 | BE |
| C17 | 外部试算平衡导入 | 导入器 | 3 | BE |
| C18 | 差异引擎（科目/辅助/期间） | 引擎 | 4 | BE |
| C19 | 差异日志 + 签字 | 页面+表 | 3 | FE+CA |
| C20 | 双轨看板（进度/差异/风险） | 页面 | 3 | FE |
| C21 | 6 户 × 2 个月金标准 | 报告 | 6 | QA+CA |
| C22 | 回归与缺陷修复 | 测试 | 3 | QA |

### 阶段 D：客户可见与切换（W17–W22，约 40 pd）

| ID | 任务 | 产出 | pd | 负责 |
|---|---|---|---|---|
| D1 | 客户可见报表快照 | API | 3 | BE |
| D2 | PDF/Excel 交付格式 | 导出 | 3 | BE |
| D3 | 客户门户“报表”页签 | 页面 | 4 | FE |
| D4 | 客户签收/确认流程 | 功能 | 3 | BE+FE |
| D5 | 切换清单工具（逐户勾选） | 页面 | 3 | FE |
| D6 | 冻结/最终导出/期初锁定 | 脚本 | 2 | BE |
| D7 | 回退脚本与演练 | 脚本+记录 | 3 | BE+QA |
| D8 | 客户审计导出 | 功能 | 2 | BE |
| D9 | 报表性能与索引 | 优化 | 4 | BE |
| D10 | 2 户 UAT | 记录 | 5 | QA+CA |
| D11 | 缺陷修复包 | 修复 | 5 | BE+FE |
| D12 | 培训/SOP 初稿 | 文档 | 3 | PM+CA |

### 阶段 E：全量收尾（W23–W26，约 18 pd）

| ID | 任务 | 产出 | pd | 负责 |
|---|---|---|---|---|
| E1 | 剩余 4 户切换 | 记录 | 5 | BE+CA |
| E2 | SOP/培训定稿 | 文档 | 3 | PM+CA |
| E3 | 监控看板（过账失败/结账时长/差异） | 看板 | 3 | BE |
| E4 | 数据完整性巡检（试算/序列/孤儿） | 脚本 | 3 | BE |
| E5 | G5 评审与决策 | 记录 | 2 | PM |
| E6 | 外部软件退役/只读方案 | 方案 | 2 | PM+BE |

### 阶段 F：单轨与 Agent（W27+）

| ID | 任务 | 产出 | pd |
|---|---|---|---|
| F1 | 4 月账期单轨 | 验收 | 5 |
| F2 | Agent 工具：过账/结账/出表/对账/税务测算 | 工具层 | 10 |
| F3 | 计费/毛利 | 模块 | 8 |
| F4 | 规模化能力（批量结账、行业模板） | 按需 | 后置 |

---

## 5. 迭代计划（2 周一个 Sprint）

| Sprint | 周次 | 主要任务 | 演示 |
|---|---|---|---|
| S0 | W1–W2 | A1–A10 | 设计签字 + 空页面 |
| S1 | W3–W4 | B1–B8，B9 启动 | 迁移可跑，过账雏形 |
| S2 | W5–W6 | B9–B16 | 过账→试算平衡 |
| S3 | W7–W8 | B17–B25 | 结账→三表 |
| S4 | W9–W10 | B26–B34 | 2 户影子 1 个月 |
| S5 | W11–W12 | C1–C6 | 期初/往来 |
| S6 | W13–W14 | C7–C12 | 银行/固定资产 |
| S7 | W15–W16 | C13–C22 | 税务/双轨 2 个月 |
| S8 | W17–W18 | D1–D4，D9 | 客户报表 |
| S9 | W19–W20 | D5–D8，D10 | 切换/回退/UAT |
| S10 | W21–W22 | D11–D12 | 2 户签收 |
| S11 | W23–W24 | E1–E3 | 6 户切换 |
| S12 | W25–W26 | E4–E6 | G5 评审 |

---

## 6. 数据模型要点

### 6.1 与现有 `books/ledgers` 的关系

- `books` 是**月度账**（现有），`ledgers` 是**长期账套**（现有）。
- 新表统一带 `book_id` + `ledger_id`：
  - `journal_entries(book_id, ledger_id, period, ...)`
  - `opening_balances(book_id, ledger_id, ...)`
  - `account_balances(book_id, ledger_id, period, ...)`
  - `fixed_assets(ledger_id, ...)`，折旧按 `book_id` 生成
  - `report_snapshots(book_id, ledger_id, period, ...)`
  - `tax_workpapers(book_id, ledger_id, period, ...)`

### 6.2 关键表

```sql
journal_entries(
  id, firm_id, company_id, book_id, ledger_id, period,
  voucher_type, voucher_no, entry_date, description,
  source_type, source_id, draft_id, evidence_ref,
  status, version, idempotency_key, reversal_of,
  posted_by, posted_at, created_at
)
journal_lines(
  id, entry_id, book_id, line_no, account_code, account_name,
  category, normal_balance, debit_cents, credit_cents,
  aux_json, description
)
opening_balances(
  id, book_id, ledger_id, account_code, aux_json,
  amount_cents, direction, baseline_id, confirmed_by, confirmed_at
)
account_balances(
  book_id, ledger_id, period, account_code,
  opening_debit, opening_credit, period_debit, period_credit,
  closing_debit, closing_credit, updated_at
)
voucher_sequences(book_id, voucher_type, next_no, version)
closing_runs(id, book_id, period, status, rules_version, snapshot_hash, approved_by, ...)
report_mappings(version, account_code, report_type, report_line, sign, formula, effective_from)
report_snapshots(id, book_id, period, report_type, mapping_version, source_hash, payload_json, ...)
tax_workpapers(id, book_id, period, tax_type, payload_json, variance_json, reviewed_by, ...)
fixed_assets(id, ledger_id, name, category, original_cost_cents, salvage_cents, useful_life_months, method, start_date, status)
depreciation_schedules(asset_id, book_id, period, amount_cents, accumulated_cents, status)
```

### 6.3 关键不变量

1. 借贷平衡；金额整数分；非负。
2. 期间必须 `open`；`entry_date` 落在 `book.period`。
3. 科目属于当前 `book`、启用、方向正确；辅助核算齐全。
4. 凭证号 `book_id + voucher_type` 连续唯一；并发生成用序列 + 唯一约束。
5. 幂等键唯一；重复请求返回同一凭证。
6. 红冲必须引用原凭证；原凭证不可删除。
7. 已过账不可原位改金额/科目；只能红冲。
8. 结账前试算平衡；结账后拒写；反结账需权限+原因+审计。
9. 过账与草稿状态更新同事务；外部副作用走 outbox。
10. 报表快照记录 `mapping_version` 与 `source_hash`。

### 6.4 科目属性快照

月度账的 `accounts` 可能随月调整；为保证金历史报表稳定，`journal_lines` 冗余保存 `account_name/category/normal_balance`。报表与余额按快照计算，不随科目后续改名而变化。

---

## 7. API 清单（建议）

> 现有 `/api/books/{book_id}/ledger` 已被“长期账套与主数据”占用，新账本用 `/journal`、`/statements`、`/trial-balance` 等路径，避免冲突。

| 方法 | 路径 | 用途 |
|---|---|---|
| POST | `/api/books/{book_id}/journal/from-draft/{draft_id}` | 已审草稿过账 |
| POST | `/api/books/{book_id}/journal/manual` | 手工凭证（草稿→审核→过账） |
| POST | `/api/books/{book_id}/journal/{entry_id}/reverse` | 红冲 |
| GET | `/api/books/{book_id}/journal` | 凭证列表（分页/筛选） |
| GET | `/api/books/{book_id}/journal/{entry_id}` | 凭证详情（含证据） |
| GET | `/api/books/{book_id}/trial-balance` | 试算平衡 |
| GET | `/api/books/{book_id}/ledger-detail` | 明细账/辅助账 |
| GET | `/api/books/{book_id}/statements/{type}` | BS/IS/CF |
| POST | `/api/books/{book_id}/statements/{type}/snapshot` | 生成快照 |
| GET | `/api/books/{book_id}/statements/{type}/export` | 导出 PDF/Excel |
| POST | `/api/books/{book_id}/closing/execute` | 执行结账 |
| POST | `/api/books/{book_id}/closing/reopen` | 反结账 |
| GET/PUT | `/api/firm/report-mappings` | 报表映射 |
| GET/POST | `/api/books/{book_id}/tax-workpapers` | 税务底稿 |
| GET/POST | `/api/books/{book_id}/fixed-assets` | 固定资产 |
| POST | `/api/books/{book_id}/depreciation-runs` | 折旧 |
| POST | `/api/books/{book_id}/opening-balances/import` | 期初导入 |
| GET/POST | `/api/books/{book_id}/dual-run/differences` | 双轨差异 |

**权限**：新增 `ledger.post`、`ledger.close`、`ledger.reopen`、`ledger.report`、`ledger.tax`、`ledger.asset`；审核人与过账人分离；反结账仅 owner/admin。

---

## 8. 前端清单

### 8.1 页面落点

| 页面 | 路由 | 归属 |
|---|---|---|
| 凭证列表/详情 | `/workspace/journal` | 账套第③步 |
| 试算平衡 | `/workspace/trial-balance` | 账套第④步 |
| 三表 | `/workspace/statements` | 账套第⑤步/客户门户 |
| 结账/反结账 | 现有 `/workspace/closing` 扩展 | 账套第④步 |
| 固定资产 | `/workspace/fixed-assets` | 专家模式 |
| 税务底稿 | `/workspace/tax-workpapers` | 专家模式 |
| 报表映射 | `/workspace/report-mappings` | 专家模式 |
| 迁移/期初 | 现有 `/workspace/migration` 扩展 | 专家模式 |
| 双轨看板 | `/workspace/dual-run` | 专家模式 |

### 8.2 交互要求

- 凭证详情：左侧分录、右侧证据/审核记录/过账记录；已过账只读，红冲按钮显式提示。
- 试算平衡：期初/本期/期末三列；借贷不平红色阻断；点击行下钻明细。
- 三表：期间选择、口径版本、快照时间、导出；行可下钻到科目/凭证。
- 结账：预检清单（未审、未过账、银行未达、税务差异、固定资产未计提）→ 确认 → 锁期。
- 客户门户：只显示结论、报表、下载，不显示科目/分录/凭证号。

---

## 9. 测试与验收

### 9.1 测试层次

| 层次 | 内容 |
|---|---|
| 单元 | 金额、借贷、期间、科目、辅助、凭证号、幂等、红冲 |
| 属性 | 任意合法分录过账后借贷平衡；红冲后余额归零；序列连续 |
| 集成 | 过账→余额→报表；结账→拒写；反结账→恢复；导出→对照 |
| 金标准 | 2 户 1 个月（B）、6 户 2 个月（C）；与外部软件逐科目对照 |
| 并发 | 同草稿并发过账、同期间并发凭证号、结账与过账竞争 |
| 性能 | 1 万/10 万行；试算 P95 < 500ms；报表 < 2s |
| 回退 | 单户回退 ≤ 1 小时；数据不丢 |

### 9.2 关键验收

- 连续 2 个账期，6 户科目余额差异为 0（或全部解释并签字）。
- 资产负债表平衡；利润表净利润 = 未分配利润变动（考虑分配）。
- 现金流量表期末现金 = 资产负债表货币资金。
- 结账后任何写入被拒绝；反结账留痕。
- 任一张报表可下钻到凭证与原件。

---

## 10. 风险与对策

| 风险 | 对策 |
|---|---|
| 与现有 `closing` 重复 | 预检复用，账本只提供 `execute/reopen`；一个结账入口 |
| 与 `settlements` 重复 | 扩展账龄/子账，不新建应收应付 |
| 与 `reconciliation` 重复 | 扩展“流水↔凭证”匹配，不重复导入 |
| 科目属性跨月漂移 | `journal_lines` 快照科目属性 |
| 报表口径反复 | 版本化 + 首席会计师签字 + 快照 |
| 历史数据质量 | 期初勾稽、双轨、差异清零才切换 |
| 性能 | 余额快照、索引、批量结账后置 |
| 范围蔓延 | 本阶段冻结非账本功能；6 户定制进 backlog |
| 单点人 | 首席会计师 + 外部 CPA 备份；文档化 |

---

## 11. 第一周就做的 10 件事

1. 写 ADR-001/002/003（账本模型、过账规则、报表口径）。
2. 画 ERD：`journal_entries/lines`、`opening_balances`、`account_balances`、`closing_runs`、`report_snapshots`。
3. 起草迁移 102–105（含 RLS、FORCE RLS、写门、GRANT、兼容头）。
4. 建 `app/ledger/` 骨架与 `build_ledger_router`，在 `main.py` 注册空服务。
5. 定义权限位 `ledger.post/close/reopen/report/tax/asset`。
6. 建金标准数据集目录与 2 户样本（脱敏）。
7. 与首席会计师开报表映射工作坊，产出 v0。
8. 确认 6 户画像与现用软件数据导出方式。
9. 定双轨差异日志字段与月度签字流程。
10. 排 S1/S2 任务，明确“本周不做 UI，只做骨架与过账雏形”。

---

## 12. 一句话

**先把“过账、余额、结账、三表”做成可验证的闭环，再把往来、银行、固定资产、税务挂上去，最后才让客户看见；每一步都能停在双轨，不赌一次性切换。**
