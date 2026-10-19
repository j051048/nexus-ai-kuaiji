# DL02 单次提取与条件复查（离线）

状态：mock/归档工程实验；不调用真实模型。  
实现：`tools/ai_inference_lab/selective.py`，复用 H01 manifest、冻结 OCR、证据与评分。

## 路径

- 基线：1 次归档提取 + 确定性检查；
- 候选：同一提取 + 确定性异常路由 + 最多 1 次 mock 复查；
- 不支持/硬阻断不追加调用。

## 路由

只读：提取字段、缺失/金额勾稽/证据错误、允许的实验政策、预算。  
不读：金标、验收标签、其他企业数据。

## 状态

`completed` / `review_required` / `blocked` / `failed` / `unknown`。  
复查成功仍为 `review_required`，不等于会计复核通过；模型检查通过不等于会计审核。

## 数据

H01 合成样本：A1/A2 有异常，A3 正常，B1 多票不支持。  
候选 mock 复查修复 A1/A2 价税合计；故意坏复查可引入票号退步。  
跨企业结果按 firm/company 保留，不混用。

## 测试

`test_ai_selective_review.py`：正常不复查、异常一票一次、B1 阻断、修复后仍需复核、坏复查 regression、预算耗尽、unknown 不重试、两企业隔离。19 passed。

## 未验证

真实模型复查质量；mock 复查只证明路由/校验/状态工程。
