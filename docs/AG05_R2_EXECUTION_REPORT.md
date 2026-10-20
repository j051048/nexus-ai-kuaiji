# AG05-R2 执行报告

## 状态：单一评分入口与身份/安全门槛通过；H01 薄适配未完成。

## 修复

- `evaluate` 兼容入口委托 `evaluate_samples`，不再有弱评分旁路；
- 固定 sample_id 对齐，拒绝重复/额外/缺失；
- manifest 必填公共身份；
- allowed_differences 白名单仅 prompt/policy/context；
- 关键退步（跨企业、金额、风险、证据、弃答）优先；
- 当前事实 120 vs 历史 100，伪 claim 被 AG03 明确拒绝。

```text
test_ag05_examples.py: 18 passed
AG02+AG03+AG04+AG05+H01: 97 passed
```

## 未完成

- 与 H01 manifest/source hash 的正式薄适配；
- 冻结 manifest 驱动分母、missing/unknown 完整输出；
- 逐样本 JSON/Markdown 报告；
- E11/E16 误报/漏报拆分；
- E16 同源改 ID 全矩阵；
- 绝对安全门槛与相对变化分栏输出。

不声明真实模型准确率。


