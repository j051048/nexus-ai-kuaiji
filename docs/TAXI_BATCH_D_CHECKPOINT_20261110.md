# 出租车代账批次 D 检查点（2026-11-11）

## 当前状态

- 候选：HEAD `781fd93499dcba7bb76056f0febe19e372202af2`；source **2107 / `0c49f01538051b24118f5ad47c829772d3b14270e4d5332ce2e0ce76fdf4f8ee`**；migration **367 / `34d8f5422a53c363a56f541892e6150fcbc693756fff5386d90a44dc0bea2cfc`**。
- 工作区 106 项未提交；未提交、未推送、未部署。
- P01：`tmp_test/tax-c/p01-d/reports/summary.json` **passed**；run `142a5b0e463c4f588a510b8908b5df6d`，environment `8cf97847e94c4702ae41632cdbfbfc11`。
- 本地 PG16/17 仍运行（25496–25499），P01 bridge 25510 运行；收尾时停止。

## 已完成

1. TAX08 54 列合成适配器：`nexus_backend/app/integrations/tplus54.py`；54 列、Decimal 金额、`记/转`、`T54-` 外部号、回执四列、防公式注入。
2. 迁移 sqlite 148 / postgres 231：artifact、receipt、receipt rows；RLS、只读/插入权限。
3. 服务/API：冻结包校验、artifact 生成/下载、回执登记/查询、dispatch 409；`bridge_export_*` 持久化。
4. 前端：`IntegrationWorkbench.tsx` 04 区生成 54 列、下载、回执上传/登记/历史、外部未过账提示；390 无溢出。
5. 证据：D1/D2 组合 19 passed；PG16/17 D2 各 1 passed；浏览器 1 passed；P01 188/2/4。

## 未完成

- TAX07 仅回归既有核销能力；**预收、退款、付款人非驾驶员的独立核定代收分配**未新做。
- TAX09 仅有既有导航回归，**单条 D3 端到端专项链**未做。
- 客户真实 54 列模板、T+ 真实导入/过账未验证；本批只做合成适配与回执。
- D2 artifact/receipt 未做目标库行级恢复断言（P01 只验证模式与既有种子）。

## 下一步具体操作

1. 若继续 D：补 TAX07 预收/退款流（需先定义 GL 重分类与核销边界）和 D3 单链浏览器测试。
2. 如只需冻结当前批次：停止 bridge、PG 25496–25499、18000/4173；保留 `tmp_test/tax-c/p01-d` 与 `tmp_test/tax-d`。
3. 不得将本检查点写成“D 轮全部通过”；TAX07/TAX09 的缺口必须保留。
