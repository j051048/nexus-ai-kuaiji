# TAXI 批次 B 验收记录：TAX03–TAX04

- 执行日期：2026-11-10
- 批次：B（TAX03 运输主数据与期初承接；TAX04 来源文件适配与消费者）
- 工程结论：**本地工程通过（TAX03 + TAX04 来源台账/贡献契约）**。TAX03 本地后端/PG/浏览器通过；TAX04 上传、版本、冻结保护、逐行核定、一源拆多行、多源绑定/贡献聚合、批量局部失败与 SQLite 全库恢复通过；贡献自动回写正式底稿行与贡献差异 UI 未实现，完整消费者闭环仍不通过。
- 外部结论：**未执行**。没有客户真实原始台账、真实 T+ / OA / 出管、真实通知或真人操作证据。
- 候选 HEAD：`781fd93499dcba7bb76056f0febe19e372202af2`
- 候选 source：`2087 / 20fee19dc83c8a694bd48e2b0674e084895076b95650c59d765db8e8c86b5a28`
- 候选 migration：`363 / a45387bc4b928cf8964b9cba71e0f3dccc2f165bf09fda716fbf0b14cb9b72bd`
- 检查点：`docs/TAXI_BATCH_B_CHECKPOINT_20261110.md`
- 机器证据：`docs/TAXI_BATCH_B_EVIDENCE_20261110.json`

## 1. 归属与改动范围

本批新增/修改：

- 迁移：`140/223`（试点边界，批次 A）、`141/224`（运输关系）、`142/225`（来源文件）、`143/226`（来源绑定）、`144/227`（来源贡献）、`145/228`（贡献输出行拆分）、`146/229`（多源绑定与贡献版本隔离）。
- 后端：`app/ledgers/{models,service,router}.py`、`app/tax/{pilot,sources,router}.py`、`app/import_wizard/parser.py`、`app/main.py`。
- 前端：`src/TransportMasterPanel.tsx`、`src/TaxSourcePanel.tsx`、`src/transport-master.css`、`src/tax-source.css`、`src/LedgerWorkspace.tsx`、`src/TaxWorkpaperPanel.tsx`。
- 测试：`test_tax03_transport.py`、`test_tax03_transport_postgres.py`、`test_tax04_sources.py`、`test_tax04_sources_postgres.py`、`test_tax04_recovery.py`、`e2e/tax03-transport.spec.ts`、`e2e/tax04-sources.spec.ts` 及配置。
- 未改历史迁移/checksum、未改 C01、未覆盖其他智能体修改。

## 2. 复用能力

- 主数据复用 `company_master_entities` / alias / history 与企业隔离。
- 期初复用既有 `opening`/`subledger`，未新建第二套。
- 上传复用既有 `import_wizard` 的 xlsx 安全解析（公式不执行、zip 限制、宏拒绝）。
- 独立复核、来源版本、企业隔离、复核后导出均保持。

## 3. 红测与修复

### TAX03

- 重名/同脱敏车牌不合并：稳定 `external_id` 为键，`display_name` 可重复；历史别名独立。
- 双班/换车/非法重叠：同车白班+夜班允许；同一驾驶员跨车重叠 409；有效期边界与停用主体/项目 409。
- 跨企业：另一企业 ID 不可解析，视图/写入被拒。
- 迁移首跑失败：`UNIQUE(..., external_id),` 尾逗号、对 `companies(id,firm_id)` 复合外键无唯一键；已删除尾逗号与无效复合外键，引用完整性改由服务层校验。

### TAX04

- 多工作表：`parse_xlsx(data, sheet_name=...)`，缺页签 422，未指定仍拒绝多 sheet。
- 同版本同内容幂等；同版本异内容 409；低版本 409；新版本 supersede 旧版。
- 行状态：零额、非本账期、重复 `(external_id,date,amount)`、未知目标不进入 adopted；金额/数量分别汇总。
- 来源→输出绑定：未冻结绑定随新版本或逐行核定失效并 409；冻结绑定保留历史指纹；来源内容未变时返回 `historical=False`，内容升级后返回 `historical=True`。
- 贡献持久化：`tax_source_contributions`（144/227）记录 adopted 行到 workpaper/preview/approval 的金额/数量，支持幂等重录、缺失/重复/错金额 409；一源拆多行见 `tax_source_contribution_lines`（145/228）。
- 逐行核定：`review_row` + PATCH 路由 + 差异表“改为采用/排除”；仅 manager、活动版本；排除必须原因；写审计并失效未冻结绑定。
- 贡献校验：`validate_contributions` 要求每条 adopted 行恰好贡献一次、金额/数量与原始采用值一致、合计一致；缺行/重复/错金额/排除行均 409。
- 多源合并：146/229 允许同一输出版本绑定多个来源；贡献按 `output_version` 隔离；`aggregate_contributions`/GET `/sources/contributions/aggregate` 汇总来源数、行数、金额/数量及输出行。PG16/17 已验证。

## 4. 分层结果

| 层 | TAX03 | TAX04 | 证据 |
| --- | --- | --- | --- |
| SQLite/HTTP | 7 passed / 13.89s | 16 passed / 27.26s | `tmp_test/tax03.xml`、`tmp_test/tax04-final.xml` |
| PG17 | 2 passed / 1.50s | 1 passed / 1.15s | `tmp_test/ak05-pg-runs/7465b6a1c1e4495aad2683f885c6a027/results.xml` |
| PG16 | 2 passed / 1.84s | 1 passed / 1.10s | `tmp_test/ak05-pg-runs/db088bb0916b4c2280b1d98d2f3a41cf/results.xml` |
| 桌面浏览器 | 1 passed / 15.7s | 2 passed / 24.4s（含逐行核定） | 1280×900；真实 GoTrue/API |
| 手机浏览器 | 390×844 打开 | 390×844 上传/展示 | 无横向溢出；截图 `tmp_test/tax03-transport-390.png`、`tax04-source-390.png` |
| 期初承接回归 | 5 passed / 11.25s | — | `test_opening_service.py` |
| 类型/构建/预算 | — | passed | initial 565,539 B / gzip 175,193；home gzip 185,770；组合 46 passed/85.63s |
| 恢复演练 | 表结构随全库恢复 | SQLite 全库 + P01 PG16/17 完整恢复 passed | `test_tax04_recovery.py` 1 passed/4.31s；P01 `batch-b-p01-2087-final3` **passed**：regression 188/167.761s、joint 2/90.789s、identity 4/61.140s，0 skipped，PG16/17 九项 checks（含 finance_resume）全 true；migration_max 229；table_count 289 |
| 真实 T+ / 客户原件 | 未执行 | 未执行 | 无外部环境/资料 |

## 5. 明确缺口

1. TAX04 已支持多源绑定、一源拆多行、批量局部失败（API）与贡献汇总，但还没有把贡献自动回写正式底稿行、也没有贡献差异 UI，不能宣称消费者闭环完成。
2. SQLite 全库与 P01 PG16/17 全量恢复均已通过；新增表的对象级专项恢复断言只在 SQLite 完成，P01 未单独 seed B 批新表。
3. 期初承接只验证既有 opening 服务（应收/资产/预收），不包含运输行业期初台账导入器。
4. 客户真实原始文件未提供；公式、外链、损坏、压缩超限只用合成夹具验证。
5. 真实 T+、通知、模型、真人、真机、远端 CI 均 pending。

## 6. 下一批准入判断

- 若只做“主数据维护 + 来源上传与版本留痕”试点：TAX03 可进入受限试点；TAX04 只能作为“来源台账”，不得作为税务底稿自动生成依据。
- 若要求 TAX04 完整消费者闭环：必须先补贡献自动回写正式底稿行/差异 UI、PG 新表 seed 的专项恢复断言、客户脱敏原件复验。

## P01 完整恢复

- 运行 `p01_recovery_gate.py --all`，候选 2087/`20fee19d…`。
- regression **188 passed/167.761s**、joint **2 passed/90.789s**、identity **4 passed/61.140s**，0 skipped。
- PG16/17 的 database/originals/identity/permissions/hold/review_export/tax_delivery/no_replay/finance_resume 全 true；originals 4；migration_max 229；originals/source 证据来自 `joint-private/*/pg1*-full-recovery-evidence.json`。migration/grants 以满足恢复后读取/写入/复核/导出为准。
- 报告 `tmp_test/tax-b/batch-b-p01-2087-final3/reports/summary.json`。
