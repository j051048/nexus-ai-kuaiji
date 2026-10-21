# AG05-R3 执行报告

## 状态：R4-3 身份/分母/检索指定部分通过。

## 新增

- manifest 类型校验：
  - dataset/policy/prompt/context/split/sample_ids 必须是正确类型；
  - split 只允许 train/dev/test/holdout；
  - sample_ids 非空、唯一。
- `validate_manifest` 拒绝 bool 冒充字段、未知 split、重复样本。
- `evaluate_samples` 支持 `expected_scope`：
  - 候选企业/账套等身份与冻结范围不一致即拒绝。
- 保留：冻结分母、missing/unknown、绝对安全门槛、风险 FN/FP 拆分、
  H01 manifest/result 薄适配、逐样本报告。

## 测试

```text
test_ag05_examples.py: 23 passed
```

## 未完成

- 金标驱动的 self-reported 字段消除；
- H01 结果级正向完整夹具；
- test/holdout 来源 hash 泄漏全矩阵；
- ContextPack 正式组合（当前有 AG03 负向对照）；
- 完整逐样本 JSON/Markdown 字段。

