# 出租车代账批次 E 检查点（2026-11-12）

## 当前状态

- 候选：HEAD `781fd93499dcba7bb76056f0febe19e372202af2`；source **2121 / `2a4896b9ba9e3c3c5205069abf1f66b9b20f51197d927d3d312008db4b3328a2`**；migration **371 / `317663028b53eea8cb439b776a62576a3a7322ebd59e03ba2acbba23f6cf7e1a`**。
- 工作区 126 项未提交；未提交、未推送、未部署。
- P01：`tmp_test/tax-e/p01-final2/reports/summary.json` **passed**；run `9e35d5344d6a440d9c89f2cb2db6e2e0`，environment `8cf97847e94c4702ae41632cdbfbfc11`。
- 本地 PG16/17 仍运行（25496–25499），P01 bridge 25510 运行；收尾时停止。

## 已完成

1. TAX10–TAX12 通用成本批次：`nexus_backend/app/tax/costs.py`、sqlite 149 / postgres 232、`tax_cost_sources/batches/rows`；维修、保险、OA 的显式来源与确定性金额。
2. 独立复核、来源版本/指纹失效、同指纹幂等、自审 409、外部事务所 RLS 404、回执不适用（本批不发送）。
3. 前端 `TaxCostBatchPanel`：来源登记/多选、策略、生成、批次汇总/行、复核、失效、历史；1280/390。
4. 证据：C/E 组合 46 passed/55.78s；PG16/17 各 5 passed；浏览器 2 passed/25.1s；P01 188/2/4。

## 未完成

- TAX10 包干特殊项目切换、覆盖范围、实付冲回与重复冲回、跨期费率、月中停用/新增按日分摊。
- TAX11 车辆/合同/资产维度的折旧、摊销、退保/增批/处置/漏提补提/尾差更正预览。
- TAX12 工资汇总=明细、社保、计息、审批撤回/跨人泄露专项。
- E4 差异/人工核定页面、可导出产物、负责人/下一步完整链路。
- PG 目标库 E 行级恢复（P01 仅模式；目标表行数 0）。
- 真实 OA/工资底稿、保险算法、客户数据、真机、CI、部署。

## 下一步具体操作

1. 如继续 E：先定义维修包干/按车切换的会计边界与冲回引用，再补 TAX11 `DepreciableAsset` 车辆/合同绑定迁移和负向用例，最后补 TAX12 工资汇总/社保/计息或明确划出范围。
2. 如冻结当前批次：停止 bridge、PG 25496–25499；保留 `tmp_test/tax-e/p01-final`、`tmp_test/tax-e`、`tmp_test/tax10-costs-*.png`。
3. 不得把本检查点写成“E 轮全部通过”；上述缺口必须保留。
