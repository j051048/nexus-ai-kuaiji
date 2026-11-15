# TAXI 批次 A 验收记录：TAX01–TAX02

- 执行日期：2026-11-10
- 批次：A（TAX01 版本化试点边界与客户资料依赖；TAX02 双向损益结转）
- 工程结论：**本地工程通过；TAX01/TAX02 的 SQLite、PG16、PG17、桌面与手机真实浏览器成功链均通过**
- 外部结论：**未执行**。没有真实客户资料、真实 T+ / OA / 出管、税务申报、真实通知、真人或真机证据。
- 候选 HEAD：`781fd93499dcba7bb76056f0febe19e372202af2`
- 候选 source：`2062 / 2bb9f3866781d7fd0fdf34d20ad55bd94828bdf011b3623f7e7d830510edcaa0`
- 候选 migration：`351 / 298bb66c91b7393c5efb8965ec007c709315a3e48015c87c29d326c5f24ef5c0`
- 机器证据：`tmp_test/tax-batch-a-evidence.json`、`docs/TAXI_BATCH_A_EVIDENCE_20261110.json`
- 细分验收：`docs/TAXI_BATCH_A_TAX01_ACCEPTANCE_20261110.md`、`docs/TAXI_BATCH_A_TAX02_ACCEPTANCE_20261110.md`

## 1. 归属与改动范围

本批只改：

- `nexus_backend/app/periodic/service.py`：双向损益结转、辅助维度、源 fingerprint/失效、审批记账前重校验。
- `nexus_backend/app/tax/pilot.py`、`nexus_backend/app/tax/router.py`、`nexus_backend/app/main.py`：版本化试点边界、依赖、预览。
- `nexus_backend/migrations/sqlite/140_tax_pilot_boundaries.sql`
- `nexus_backend/migrations/postgres/223_tax_pilot_boundaries.sql`
- `src/FinanceOperationsPanel.tsx`、`src/TaxWorkpaperPanel.tsx`、`src/tax-workpapers.css`、`src/PilotBoundaryPanel.tsx`、`src/closing-command.ts` 及其测试。
- 新增测试/浏览器：`test_tax01_pilot.py`、`test_tax01_pilot_postgres.py`、`test_tax02_closing.py`、`test_tax02_closing_postgres.py`、`e2e/tax01-pilot.spec.ts`、`e2e/tax02-closing.spec.ts`、两个 Playwright 配置。
- 未改历史迁移、历史 checksum、C01 或已冻结账务；未覆盖其他智能体修改。

## 2. 已有能力与复用

- 既有税务底稿、税务档案、月度交付/签收、人工外部办理记录。
- 既有政策/主档/服务计划、企业隔离、实时授权、来源版本、独立复核后导出。
- 既有 `periodic` 草稿/审批/记账、`audit_events`、统一错误语义。
- 本批没有新建第二套税务工作台或第二套审批引擎。

## 3. 红测与修复

### TAX01

红测：

- 未确认边界不能生成预览；
- 未决依赖不能确认；
- 非负责人不能改/确认；
- 跨企业 404；
- T+ 模式缺模板 422；
- 更新 draft 后旧预览必须失效。

修复：新增版本化 profile/dependency/preview；服务端强制 10 项覆盖、证据引用、权限与版本；draft/confirm 变更失效旧预览。

### TAX02

红测：

- 收入 100 与费用贷方 10，结转利润应为 110，原实现只结转 100；
- 收入借方冲减被遗漏；
- 辅助项目在科目汇总时丢失；
- 源凭证后补费用后旧草稿仍可审批。

修复：按科目 + 分类 + 辅助分组双向结转；closing 分录带 `aux`；源 fingerprint 变化 void 旧 run；approve/post 重校验源余额。

## 4. 分层结果

| 层 | TAX01 | TAX02 | 证据 |
| --- | --- | --- | --- |
| SQLite/HTTP | 7 passed | 9 passed | `tmp_test/tax-backend.xml`，合计 16 passed / 31.47s |
| PG17 | 1 passed / 2.57s | 2 passed / 108.34s | `ak05-pg-runs/0371191696…`、`bf2408e81f…` |
| PG16 | 1 passed / 1.59s | 2 passed / 106.44s | `ak05-pg-runs/a087570bc5…`、`b2a3e24811…` |
| 桌面浏览器 | 2 passed 汇总 | 2 passed 汇总 | 1280×900、1365×900；真实 GoTrue/API/页面 |
| 手机浏览器 | 390×844 | 390×844 | 无横向溢出；截图见证据 JSON |
| 既有结转/底稿回归 | — | 12 passed / 22.76s | `test_b04_pilot.py` + `test_tax_workpaper.py` |
| 前端单测 | — | 8 passed | closing-command + finance-continuation |
| 类型/构建/预算 | — | passed | `tmp_test/tax-budget.json` |
| 独立复核 | 保留 | 保留 | 页面“待独立复核”；制单人不能自审 |
| 真实 T+ / 申报 | 未执行 | 未执行 | 无外部环境与授权 |

## 5. 客户资料与规则台账

以下 10 项默认全部 `unresolved`，不得为客户签认：

| code | 客户需提供 |
| --- | --- |
| income_336_335 | 336/335 行收入逐行参与/排除及依据 |
| prior_period_residue | 旧期间残留行的处理与证据 |
| imbalance_832_19 | 832.19 摊销不平来源 |
| rent_3550 | 租赁收入 3,550 差额依据 |
| insurance_formula | 保险月摊算法与取整规则 |
| tax_base_5990 | 5,990 计税基数出处 |
| tax_rounding | 税额分位差处理 |
| tplus_template | T+ 版本、54 列模板与回执 |
| source_files | 完整月源文件与相邻期 |
| payroll_scope | 工资/社保数据范围 |

浏览器里的“客户确认”是合成夹具，只证明控制可用，不代表真实客户已确认。缺失累计/所得税保持未知，不补零。

## 6. 候选、环境与限制

- 未提交、未推送、未部署；未执行线上 SQL；未调用真实模型/通知/T+。
- `npm run typecheck` 0；build 0；budget passed（initial 565,455 B / gzip 175,163 B；home 593,019 B / gzip 185,750 B）。
- 已知与本批无关的旧失败：`test_stage13_migration_workflow.py` 8 failed / 8 passed；本批未改其任务路由/迁移工作流，未纳入本批验收。需要在下一批或独立修复中处理。
- 未做真人、真机、微信、屏幕阅读器验证。
- 本批不扩展税种计算、自动申报、跨年结转、集团合并。

## 7. 下一批准入判断

允许进入批次 B 的**本地通用工作**：主数据/期初/来源接入的代码、合成夹具、PG16/17 与浏览器验收。

不得进入或宣称：

- 真实客户规则已确认；
- 真实 T+ / OA / 出管已接通；
- 真实申报/缴款/通知已发生；
- 生产可用或 200–500 家容量已证明。

批次 B 如涉及客户编码、车牌、驾驶员、合同等真实数据，只能用合成/脱敏样本；真实接入仍需客户资料与授权。
