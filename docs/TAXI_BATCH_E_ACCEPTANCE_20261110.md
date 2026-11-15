# 出租车代账批次 E 验收记录（TAX10–TAX12）

日期：2026-11-12
状态：**本地核心通过；TAX10 高级包干/实付冲回、TAX11 车辆折旧摊销绑定、TAX12 工资社保计息未完成，不宣称整批完成。**
候选：HEAD `781fd93499dcba7bb76056f0febe19e372202af2`，source 2121 / `2a4896b9ba9e3c3c5205069abf1f66b9b20f51197d927d3d312008db4b3328a2`，migration 371 / `317663028b53eea8cb439b776a62576a3a7322ebd59e03ba2acbba23f6cf7e1a`。

## 结论分级

| 范围 | 本地实现 | 离线验证 | PG16 | PG17 | 浏览器 | 外部验收 |
| --- | --- | --- | --- | --- | --- | --- |
| TAX10 维修/保险/OA 通用成本批次 | 核心完成 | 通过 | 通过 | 通过 | 通过 1280/390 | 不适用（仅本地） |
| TAX10 包干特殊项目切换/覆盖范围/实付冲回 | 未完成 | 未验证 | 未验证 | 未验证 | 未验证 | 未授权 |
| TAX11 保险直线/人工分摊 | 核心完成 | 通过 | 通过 | 通过 | 通过（与 OA/维修同链） | 真实保单/算法未验证 |
| TAX11 车辆/合同绑定的折旧、摊销、退保/处置/补提 | 未完成 | 未验证 | 未验证 | 未验证 | 未验证 | 未授权 |
| TAX12 OA 核定明细 | 核心完成 | 通过 | 通过 | 通过 | 通过（与维修/保险同链） | 真实 OA 未接通 |
| TAX12 工资汇总/社保/计息 | 未完成 | 未验证 | 未验证 | 未验证 | 未验证 | 未授权 |

## 已通过

### 通用成本批次（TAX10–TAX12 核心）

- 新增 `nexus_backend/app/tax/costs.py`、sqlite `149_tax_cost_batches.sql` / postgres `232_tax_cost_batches.sql`，表：
  - `tax_cost_sources`：类别 repair/insurance/oa、来源键/版本、期间、对象、原件引用、sha256、金额、核定 payload、active/superseded、fingerprint。
  - `tax_cost_batches`：状态 draft/reviewed/invalidated、版本、草稿、策略/来源/批次指纹、金额、行数、制单人/复核人。
  - `tax_cost_batch_rows`：逐来源 adopted/rejected、原因、草稿行号。
- 来源采用：
  - 维修：发票号=来源键，覆盖率 `package|per_vehicle|actual`；有费率×数量时强制等于金额；同发票同版本异内容 409。
  - 保险：保单、保费、起止期间、总月数、算法 `straight_line|manual`；直线法逐期商+末期尾差；人工法逐期金额且合计等于保费；当前期间必须在保单内。
  - OA：事件 ID/版本、核定人、核定期间、明细科目/金额；明细不重复、合计等于总额；同事件同版本异内容 409。
  - 新版本将旧 active 置 superseded；低版本 409；同版本同指纹幂等。
- 生成：
  - 显式策略版本、费用科目、贷方科目；不提供默认会计政策。
  - 同批次指纹幂等；合成来源 `tax-cost:<batch>` + 一张草稿；维修/保险费用借、贷方科目；OA 按明细借、贷方合计。
  - 写 `tax_cost_batch_rows.line_ordinal` 与草稿行来源关系；全部 rejected 时 409。
- 复核/失效：
  - `review` 在同一事务内重校验来源 active/期间/指纹，再调用既有独立复核；制单人自审 409。
  - 来源升级后 `stale=true`、`validate` 409、`review` 409。
  - `invalidate` 只改批次状态/版本，不删除历史。
- API：`/api/tax/books/{book_id}/cost-sources`、`/cost-batches`、`/{batch_id}`、`/review|validate|invalidate`。
- UI：`src/TaxCostBatchPanel.tsx` + `tax-cost.css`，接入 `TaxWorkpaperPanel`；类别、来源、依据 JSON、策略、批次汇总/行、独立复核、失效、历史批次；390 无横向溢出。

### 测试与浏览器

- `nexus_backend/tests/test_tax09_costs.py` + `test_tax09_recovery.py`：12 passed/16.03s。
- 组合 `large_voucher + tax06 + tax06_recovery + tax04_sources + tax09_costs + tax09_recovery`：**46 passed/55.78s**，JUnit `tmp_test/tax-e/tax-e-combined-final.xml`。
- PG17 5 passed/3.77s（`tmp_test/ak05-pg-runs/8945a6a3a46a47cd90d5c83ebc9603b1`）；PG16 5 passed/3.77s（`217a46c1246e4288aa3dfa98a5e0a02d`）。
- 浏览器 `e2e/tax10-costs.spec.ts`：**2 passed/25.1s**（1280/390）。真实 API 登记维修、生成维修/保险/OA 三批、自审 409、reviewer 独立复核、打开 OA v2 已复核历史。
- 截图：`tmp_test/tax10-costs-1280.png`、`tmp_test/tax10-costs-390.png`。
- 前端 `npm run typecheck` 0；`TEMP/TMP=tmp_test/esbuild-tmp npm run build` 成功：index JS 565.54 kB/gzip 175.18 kB，CSS 99.72 kB/gzip 20.27 kB，`TaxWorkpaperPanel` 59.97 kB/gzip 18.92 kB。

## 明确未完成 / 不冒充

1. **TAX10 包干切换与实付冲回**：`coverage` 只是来源字段，没有“包干特殊项目→按车”切换点、覆盖范围判定、实际维修与包干重复校验、实付冲回一次/重复冲回；也没有跨期费率表、月中停用/新增的按日分摊。不能宣称这些负向场景已通过。
2. **TAX11 车辆折旧/摊销**：只做了保险月摊；没有把保单/资产/合同分别绑定车辆、原件、历史累计、有效期、算法版本，也没有退保/增批/处置/漏提补提/尾差更正预览。未复用或扩展 `PeriodicService` 的车辆维度。
3. **TAX12 工资/社保/计息**：只做了 OA 核定明细；没有工资汇总=明细校验、社保、计息、审批撤回/跨人泄露专项。没有通用薪资引擎。
4. **E4 全类“来源采用→预览→差异/人工核定→独立复核→可导出”**：维修/保险/OA 有批量草稿和复核；没有差异/人工核定页面、导出产物、负责人/下一步字段的完整链路。
5. **恢复**：P01 只验证模式与既有种子；E 三表在源/目标存在，目标行数为 0。E 行级恢复仅由 SQLite `test_tax09_recovery.py` 覆盖，未做 PG 目标库行级恢复。
6. **真实 OA/工资底稿、真实保险算法、客户数据、真机、远端 CI、生产发布**：均未验证；不得用合成规则反推真实金额。

## 恢复与迁移

- P01 `tmp_test/tax-e/p01-final2/reports/summary.json` **passed**：candidate 2121/`2a4896...`，regression 188/149.964s、joint 2/89.255s、identity 4/55.903s，0 skipped。
- PG16 16.15、PG17 17.11；migrations 221，schema max 233；originals 4；source users 6；restored capability grants 987；column grants 55；九项 p01_checks 全 true。
- 目标库 `accounting.tax_cost_sources/tax_cost_batches/tax_cost_batch_rows` 存在，行数均为 0；仅模式恢复。

## 限制

- 只使用合成数据、本地身份、私有原件存储；未提交、未推送、未部署、未执行线上 SQL、未调用真实模型/通知/OA/T+。
- 本轮未改历史迁移 checksum；新增迁移为 sqlite 149/postgres 232。
