# PLT02-E 参考报表逐行对平

状态：**工具就绪待客户输入**。当前只支持本地合成的参考报表；真实对平必须使用客户财务负责人已确认的报表原件/CSV。

## CSV 格式

UTF-8（可带 BOM），4 列，表头必须为：

```csv
报表,行次,行名,金额
```

- `报表`：`资产负债表`、`利润表`、`现金流量表`；也接受 `balance_sheet`、`profit_statement`、`cash_flow`。
- `行次`：客户报表行次，原样保留在差异清单中；不可为空。
- `行名`：必须使用下列标准名或已登记别名；未知行名返回 422，不做模糊匹配。
- `金额`：人民币元，最多两位小数，允许千分位和 `¥`；系统内部按整数分比较。

每张表最多 512KB；重复行、空金额、超过两位小数、未知报表/行名都拒绝。

## 标准行名

| 报表 | 行名 | 系统字段 |
| --- | --- | --- |
| 资产负债表 | 资产合计 | `balance_sheet.assets` |
| 资产负债表 | 负债合计 | `balance_sheet.liabilities` |
| 资产负债表 | 所有者权益合计 | `balance_sheet.equity` |
| 利润表 | 营业收入（别名：收入合计） | `profit_statement.revenue_cents` |
| 利润表 | 成本费用（别名：费用合计） | `profit_statement.expense_cents` |
| 利润表 | 利润（别名：利润合计） | `profit_statement.profit_cents` |
| 现金流量表 | 期初现金 | `cash_flow.opening_cash_cents` |
| 现金流量表 | 期末现金 | `cash_flow.closing_cash_cents` |
| 现金流量表 | 净变动（别名：现金净变动） | `cash_flow.net_change_cents` |
| 现金流量表 | 经营活动 | `cash_flow.operating_cents` |
| 现金流量表 | 投资活动 | `cash_flow.investing_cents` |
| 现金流量表 | 筹资活动 | `cash_flow.financing_cents` |

> 客户报表如果使用不同行名，须先把标准映射写入客户确认记录；不得让工具猜行名。

## API

对平（只读，不改报表状态）：

```http
POST /api/reports/{report_id}/reconcile
Content-Type: application/json

{
  "reference_name": "客户已确认报表-2026-10.csv",
  "csv_text": "报表,行次,行名,金额\n..."
}
```

响应：

- `counts`：`matched`、`amount_different`、`missing_reference`、`missing_system`。
- `rows[]`：`line_key`、`line_no`、`line_name`、`reference_line_no`、`reference_name`、`reference_cents`、`system_cents`、`difference_cents`、`status`、`drilldown`。
- `amount_different` 行带 `drilldown`：当前系统报表的科目级构成；现金流量行给出现金流量构成。
- `reference_sha256` 是本次 CSV 字节的 sha256；解释必须绑定该 hash。

登记解释（追加，不覆盖）：

```http
POST /api/reports/{report_id}/reconciliation-notes
Content-Type: application/json

{
  "reference_sha256": "<64 位小写 hex>",
  "line_key": "balance_sheet.liabilities",
  "explanation_type": "rounding",
  "note": "客户报表为整数元，系统按分取整",
  "reason": "PLT02-E 对平说明"
}
```

`explanation_type` 只允许 `rounding`（舍入）、`reclassification`（重分类）、`basis`（口径不同）、`unknown`（待查）。没有“忽略差异”类型，也不存在把差异标记为已解决或修改报表的接口。

查询解释：

```http
GET /api/reports/{report_id}/reconciliation-notes?reference_sha256=<hash>
```

解释写入 `report_reconciliation_notes`，带登记人、时间、报告、参考 hash、行键和原因；数据库触发器拒绝 UPDATE/DELETE，并写 `report.reconciliation.explain` 审计。

## 状态与边界

- 对平只读取系统报表的快照 `content_json`；生成前后报表状态不变，不触发复核/发布/导出。
- 解释登记需要该账套的复核权限；不授予客户或只读角色。
- 解释只记录判断，不改变系统金额，不生成新的正式报表，也不把差异“核销”。
- 合成演练必须覆盖：1 分舍入差、一笔重分类、一行参考缺失；当前 `test_plt02_reference_reconciliation.py` 覆盖这三类及“参考有、系统无”。
- 当前下钻是**科目级构成**；尚未做到逐凭证下钻（PLT04-D 完成后补），不得把科目级构成说成凭证级。
- HTTP 接线由同一测试的 `test_reconciliation_routes_are_wired` 覆盖：对平、登记解释、按 hash 查询；解释写入后报表仍为 `draft`。

## 合成样例

`docs/pilot/samples/plt02_reference_2026-10.csv` 仅用于本地演练，金额均为虚构。
