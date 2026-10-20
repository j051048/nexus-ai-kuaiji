# AG05-R1 执行报告

## 状态：身份/分母/退步/检索部分通过；H01 全强校验组合未完成。

## 修复

- `validate_manifest` 要求 dataset_id、policy/prompt/context、split、sample_ids，缺字段/空值拒绝；支持声明式候选差异。
- `evaluate_samples` 按 sample_id 对齐，拒绝重复/额外/缺失身份，不再按数组位置。
- 关键退步（跨企业、金额、风险、证据、弃答）优先于平均分。
- 当前事实 120 与历史样例 100 组合：用 100 伪造当前 claim 被 AG03 校验拒绝。
- 保留企业/账套/供应商/审核/holdout/同源过滤。

## 测试

```text
test_ag05_examples.py: 14 passed
AG02+AG03+AG04+AG05+H01: 84 passed
```

## 未完成

- 与 H01 manifest/来源 hash 的完整薄适配；
- 逐样本 JSON/Markdown 报告；
- E11 误报/漏报分别评分；
- E16 改 ID 同源拆分全矩阵；
- 与 ContextPack 的正式组合适配（当前用 AG03 校验证明当前事实优先）。

仅离线工程结论，不声明真实模型准确率。
