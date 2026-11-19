# 出租车规费业务链（PLT04-A 只读盘点）

- 盘点日期：2026-11-18
- 范围：出租车规费从来源台账 → 规费批次 → 独立复核 → 应收 → 银行核销/退款 → 正式记账 → 报表/下钻 → 冲销与退回 → 原件与回执。
- 方式：只读代码与既有测试，未改产品代码、未提交、未使用客户真实数据。
- 合成口径：金额整数分；示例单价均为虚构；来源为仓库内合成 CSV/测试夹具。文中的 ID 是字段关系，不是生产数据。
- 相关模块：`nexus_backend/app/tax/fees.py`、`app/tax/sources.py`、`app/posting/service.py`、`app/reports/profit.py`、`app/reports/service.py`、`src/TaxSourcePanel.tsx`、`src/TaxFeeBatchPanel.tsx`、`src/TaxFPanel.tsx`。

## 1. 合成参考对象

以一行出租车规费为例：

| 来源行 | 日期 | 摘要 | 车辆 | 驾驶员 | 毛额 | 优惠 | 调整 | 净额 | 状态 |
|---|---|---|---|---|---|---|---|---|---|
| L1 | 2026-10-01 | 机场接送 | 京A·E2E01 | 张师傅 | 100.00 | 10.00 | 0.00 | 90.00 | adopted |

- 来源文件：`tax_source_files.id = <source_file_id>`，`external_id = S-FEE`，`version = 1`，`sha256 = <file_sha256>`，`rows_json[1]` 为 L1，`source_row_number = 2`（第 2 行，含表头）。
- 规费批次：`tax_fee_batches.id = <batch_id>`，`source_file_id = <source_file_id>`，`source_row_number` 由行表保存。
- 预期批次金额：`90.00`（整数分 `9000`）。

## 2. 分层链路

```text
原件/CSV
  └─ tax_source_files (sha256, size, sheet, rows_json, fingerprint, version)
      └─ tax_fee_batches (period, version, status, fingerprint)
          ├─ tax_fee_batch_rows (source_file_id, source_row_number, net_cents)
          ├─ sources (external_id = tax-fee:<batch_id>, amount_cents)
          ├─ drafts / draft_lines / draft_line_sources / draft_line_auxiliaries
          │     └─ fee_receivable_items (batch_row_id, open/settled)
          │           ├─ fee_receivable_allocations (bank_source_id, amount_cents)
          │           └─ fee_receivable_refunds (bank_source_id, amount_cents, reason)
          └─ 独立复核（reviewed）
                └─ PostingService.post_draft
                      ├─ vouchers / voucher_lines (source_ref = sources.id)
                      └─ voucher_sources (source_kind = draft, source_id = draft_id)
                            └─ report_runs.content_json（报表快照）
                                  └─ profit overview → voucher_lines（实时，不是快照）
```

## 3. 正向链路

### 3.1 来源导入与逐行核定

1. `TaxSourceService.import_source` 解析 CSV/XLSX，保存 `tax_source_files`：文件 hash、大小、页签、行 JSON、金额合计、数量、版本、指纹。
2. 每行 `status` 为 `adopted` / `excluded` / `needs_review`；缺 ID、金额、期间、重复或未知对象不得 adopted。
3. 行核定写 `tax.source.row_review` 审计；排除必须有原因。
4. 同 `(book_id, period, source_system, external_id)` 新版本会把旧 active 行置为 `superseded`；同版本不同 hash 409；旧版本 409。
5. 前端 `TaxSourcePanel` 展示页签、行号、状态、原因、金额/数量合计与指纹；原件按 hash/大小校验后下载。

**定位键**：`tax_source_files.id` + `source_row_number`；不是 CSV 行号，是数据行序号（含表头偏移）。

### 3.2 规费批次生成

1. `TaxFeeService.generate` 只读取 adopted 行；按车辆、驾驶员、班次、合同/经营权解析运输主数据。
2. 计算：`net = gross - discount + adjustment`，全部整数分；`net<=0` 跳过并写原因。
3. 写 `tax_fee_batches`（版本、状态、指纹、来源文件）、`tax_fee_batch_rows`（来源文件/行号、净额、原因）、`sources`（`external_id = tax-fee:<batch_id>`）、`drafts`、`draft_lines`（ordinal 从 0）、`draft_line_sources`、`draft_line_auxiliaries`。
4. 复核前再次核对来源行指纹、驾驶员/车辆身份、复核政策版本；任何一项变化，旧批次只能作废重生成，不得就地修改已复核批次。

**定位键**：`tax_fee_batch_rows.source_file_id + source_row_number` → `draft_line_sources.ordinal` → `draft_lines.ordinal` → `voucher_lines.line_no`（需人工 +1/映射；见断点 B2）。

### 3.3 独立复核

1. 入口：`POST /api/tax/books/{book_id}/fee-batches/{batch_id}/review`。
2. 必须由制单人之外的、当前账套有复核权限的人办理；同一人自审 409。
3. 复核前重算：来源指纹、运输关系有效期、司机/车辆身份、复核政策版本。
4. 通过后 `_approve_in_transaction` 只把 draft 置为已审，`_materialize_receivables` 写 `fee_receivable_items`，每行金额与 batch row 一致，状态 `open`。
5. 复核不生成凭证、不生成银行流水、不代表已收款；生成草稿不等于已复核。

**定位键**：`tax_fee_batches.id` → `drafts.id` → `fee_receivable_items.batch_row_id`。

### 3.4 银行到账与核销

1. 入口：`POST /api/tax/books/{book_id}/fee-receivables/{item_id}/allocations`。
2. 只接受已记账的银行来源（`bank_source_id` 指向 `sources`），校验来源方向、未审草稿、开放期、应收余额、银行余额。
3. 写 `fee_receivable_allocations`；部分核销保留 `open`，足额变 `settled`；重复请求按 idempotency key 返回原结果。
4. 撤销：`POST .../allocations/{allocation_id}/reverse`，写 `reversed_at/by/reason`，不删除历史，重算余额；重复撤销 409。
5. 退款：`POST .../refunds`，独立表 `fee_receivable_refunds`，必须有原因，不覆盖核销记录。
6. 银行到账不直接确认收入；规费收入仍以已复核规费批次和正式记账为准。

**定位键**：`fee_receivable_items.id` → `fee_receivable_allocations.receivable_id` → `bank_source_id` → `sources`；银行流水明细只能到 `sources`，不能精确回 `bank_statement_lines`（断点 B3）。

### 3.5 正式记账

1. 前置：已复核 draft、正式期初绑定、复核政策版本、科目映射、期间开放。
2. `PostingService.post_draft` 生成 `vouchers`、`voucher_lines`、`voucher_sources`、`posting_commands`。
3. `voucher_lines.source_ref = sources.id`（规费批次来源），`voucher_sources.source_kind='draft'`、`source_id=draft_id`。
4. 凭证号为账套/类型内递增，正式凭证 append-only；草稿、复核、记账三步分离。
5. 正式记账不自动发布报表、不自动通知客户。

**定位键**：`vouchers.id` → `voucher_lines.voucher_id`，`voucher_lines.source_ref` → `sources.id`；`voucher_sources.source_id` → `drafts.id`。

### 3.6 报表与利润下钻

1. `ReportService.create` 生成 `report_runs` 快照，`content_json` 含资产负债表/利润表/现金流量表与勾稽。
2. `ProfitDrilldownService.overview` 当前按期间实时查 `voucher_lines`，返回 `voucher_id/no/line_no/source_ref/evidence_ref/net_cents`。
3. UI `TaxFPanel` 的“利润项目与凭证行下钻”只展示凭证号、科目、净额、来源；没有从行跳到凭证详情，也没有回到 `tax_fee_batch_rows`/来源行的链接。
4. 报表勾稽通过 ≠ 外部申报/送达；报表发布是另一动作，独立复核后才可发布。

**定位键**：`report_runs.id` → `content_json` 行 → `voucher_lines.line_no`；当前快照和实时查询不一致（断点 B4）。

### 3.7 冲销、更正、退回

1. 正式凭证不可改删；冲销/更正通过 `voucher_relations` 追加，原凭证保留。
2. 规费复核后若来源版本变化：旧批次/旧应收只能作废或保留历史；新批次用新版本，不覆盖旧记录。
3. 核销撤销只写撤销字段；退款独立记录；不回写银行已到账事实。
4. 月结锁定后只能走受控重新开期/更正，不得直接改已记账凭证。

## 4. 反向定位（从报表行回到原件）

| 起点 | 关系 | 下一跳 | 现状 |
|---|---|---|---|
| `report_runs.content_json` 利润行 | 当前无持久行 ID | 实时 `voucher_lines` | 只能按科目/期间重查，不能仅凭快照复现（B4） |
| `voucher_lines.line_no` | `draft_line_sources.ordinal` | `draft_lines` | ordinal 从 0、凭证 line_no 从 1，需人工映射（B2） |
| `draft_line_sources.source_file_id/source_row_number` | 来源行 | `tax_source_files.rows_json` | UI 可显示行，但没有 URL/深链 |
| `sources.id` | `tax_fee_batch_rows` / batch | `tax_fee_batches` | 批次行保存 source_file/row，可回来源 |
| `fee_receivable_allocations.receivable_id` | 应收 | `fee_receivable_items` | 可回 batch_row_id |
| `fee_receivable_allocations.bank_source_id` | 银行来源 | `sources` | 不能回银行流水明细（B3） |
| `vouchers.id` | `voucher_sources.source_id` | `drafts.id` | 可回草稿，但不能直接回批次（需 draft join） |

## 5. 断点清单（PLT04-A 结论）

| 编号 | 断点 | 证据 | 影响 |
|---|---|---|---|
| B1 | 规费复核后没有产品内“发布/记账”入口 | `TaxFeeBatchPanel` 只展示复核/历史/影响，未见 post action；`PostingService` 有 API，但 UI 无按钮 | 用户必须转到其他凭证入口或调用 API，链路不闭环 |
| B2 | 凭证行无法稳定回来源行 | `voucher_lines.source_ref = sources.id`；`voucher_lines.line_no` 与 `draft_line_sources.ordinal` 无 FK，只能按顺序/金额猜 | 报表下钻到凭证行后不能一键回来源行；跨行拆分时可能错配 |
| B3 | 银行核销不能精确回银行流水行 | `fee_receivable_allocations.bank_source_id` 只 FK `sources(id,book_id)`；无 `bank_statement_line_id` | 只能证明“某银行来源”，不能证明具体流水；对账争议时需人工比对金额/日期 |
| B4 | 利润下钻用实时凭证，不是报表快照 | `reports/profit.py:176 _rows` 直接查 `voucher_lines`；`report_runs.content_json` 未被读取 | 报表已发布后新增/更正凭证会改变下钻结果，旧报表页面不能复现原快照 |
| B5 | 下钻结果截断 1000 条 | profit drill items 有 limit；大账套会缺行 | 大凭证/大期间无法完整追溯，需分页或上限提示 |
| B6 | 结算单/银行流水/原始行多处靠金额/日期/编号人工匹配 | alloc/refund 只保存 source id、bank amount/version；source row 与 bank statement 无结构关系 | 可能把同日同额的另一笔流水当成本笔；需要人工复核 |
| B7 | 来源行没有可分享深链 | `TaxSourcePanel` 行无 URL/route；行操作只改状态 | 复核人只能在同一页面定位，不能把行发给另一个人 |
| B8 | 规费批次、应收、凭证的状态没有统一“链路视图” | 分散在三个面板/表；`tax_fee_batches.status` 不记录 posted | 用户无法从批次直接看到“已复核→已记账→已收款/已退款”一条线 |

## 6. 合成 trace 计划（未执行）

PLT04-A 是只读盘点，本轮未新增运行时 trace。后续用 `nexus_backend/tests/test_tax06_fees.py` 的 `_masters/_source` 合成夹具构造一行 L1，记录以下实际 ID：

1. `source_file_id`、`source_row_number=2`、`sha256`；
2. `batch_id`、`batch_row_id`、`source_id`、`draft_id`、`draft_line ordinal`；
3. `review` 后的 `reviewed_by/reviewed_at`、`fee_receivable_id`；
4. `allocate_receivable` 后的 `allocation_id`、`bank_source_id`、`bank_draft_version`；
5. `post_draft` 后的 `voucher_id`、`voucher_line.line_no/source_ref`、`voucher_sources`；
6. `ReportService.create` 后的 `report_run_id`、利润行、drill items；
7. 冲销/退款后的 `reversed_at`/`refund_id`，确认原记录不变。

## 7. 状态

- 工程状态：PLT04-A 盘点完成（只读）；PLT04-B/C/D/E 未开始。
- 外部验收状态：不适用；无客户真实规费数据、无真实银行流水、无真实 T+。
- 已覆盖：代码路径、表关系、界面入口、合成测试夹具口径。
- 未覆盖：真实客户原始文件、真实银行回单、真实税务/申报、真实出纳/会计人员操作。
- 结论：链路“有数据结构和局部入口”，但**不是可一键追溯的闭环**；B1–B8 需按 PLT04-B/C/D/E 分批处理，不能把本盘点当作链路已验收。
