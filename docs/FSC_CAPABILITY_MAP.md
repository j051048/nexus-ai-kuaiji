# FSC 能力—服务—页面—测试映射（FSC01 基线）

| 业务能力 | 服务/路由 | 页面/入口 | 本地测试 | PG16/17 | 恢复 |
| --- | --- | --- | --- | --- | --- |
| 企业/账套/期间 | `accounting` 服务 | 全所工作台、客户企业 | A–E 既有套件 | 既有 | P01 |
| 原始资料/事实 | `ingestion`、`business` | 资料清单、业务事项 | `employee-flow`、`business` | `test_business_postgres.py` | P01 originals |
| 草稿/独立复核 | `accounting`、`review` | 凭证队列、同屏复核 | `tax05-large-voucher` | `test_tax_j_large_voucher_postgres.py` | P01 review_export |
| 正式账簿/报表 | `ledgers`、`reports` | 正式账簿、报表 | `formal-books-*` | `test_tax_f_postgres.py` | P01 |
| 税务底稿/申报/回执 | `tax/workpaper`、`filings` | 税务底稿 | `test_next05_filing_evidence.py` | 部分 | P01 tax_delivery |
| 租赁/规费/成本/利润 | `tax/leases`、`fees`、`costs`、`reports/profit` | 税务底稿/经营看板 | `tax-f`、`tax10-costs`、`tax06-fees` | `test_tax_f_postgres.py`、`test_tax06_fees_postgres.py` | 行级 SQLite 恢复 |
| T+ 54 列合成适配/回执 | `integrations/tplus54` | 财务软件接入 | `test_tax08_tplus54*` | 各 1 | P01 |
| 外部证据双档 | `delivery/external_evidence` | 月度交付 | `test_tax16_external_evidence.py` | 5 | SQLite 行级 |
| 通知/签收 | `delivery` | 月度交付 | 既有 | 既有 | P01 |
| 银行/往来核销 | `settlements` | 银行匹配、往来 | `test_stage10_settlements.py` | 各 4 | P01 |
| 固定资产/折旧 | `periodic` | 资产与期末草稿 | `test_b04*` | 部分 | P01 |
| 工资/社保 | 无通用引擎，仅核定消费者 | 未建 | 无 | 无 | 无 |
| OA 审批/补件 | `cases/oa_snapshots` | 税务底稿、员工工作台 | `test_k05_oa*`、`tax-k-oa*` | 各 1 | 无 |
| 经营看板 | `observability/insights*`、`fleet`、`value`、`closing` | `/workspace/insights`、运行可观测性 | `test_firm_insights*`、`tasks0712` | 部分 | 无 |
| FDE 适配器契约 | `integrations/fde` | 运行可观测性 | `test_fde_adapters.py` | 无 | 无 |
| AI 路由/预算 | `assistant/routing`、`meter` | 会计助手 | `test_assistant_routing*` | 各 1 | P01 no_replay |

## 结论

- 平台已覆盖“资料→事实→草稿→复核→账簿→报表→税务/交付”的主要本地环节。
- 工资/社保、真实 T+、电子税局、银行支付、客户签收、OCR/通知仍未真实接通，属于外部门槛。
- 本表用于防止把“页面存在”当成“能力已验收”；每行必须在对应批次报告中给出实际命令与结果。
