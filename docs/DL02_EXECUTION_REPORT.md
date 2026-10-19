# DL02 执行报告

## 结论

**DL02 离线工具/状态/身份契约通过；候选 mock 效果为 `promising_offline`，真实模型效果未验证。**

## 前置

H01-R1 54 passed；DL01 15 passed；HEAD `59764a9b`；未修改 C01。

## 实现

`tools/ai_inference_lab/selective.py`、`test_ai_selective_review.py`、`DL02_SELECTIVE_REVIEW.md`。

## 结果

- A3 正常：不追加调用；
- A1/A2 异常：各 1 次 mock 复查，逻辑 2 次/实际 mock 1 次；
- A1/A2 修复 gross，但仍 `review_required`（会计复核）；
- B1 不支持：`blocked`，0 次调用；
- 坏复查：`regression`，关键票号退步；
- 预算 0：不调用；
- unknown：保持 unknown，一次 attempt；
- 两企业结果隔离。

## 测试

```text
pytest test_ai_inference_contracts.py test_ai_selective_review.py -q
22 passed
```

## 未验证

真实模型复查、真实费用/耗时、生产预算、会计复核 UI。mock 调用/时间不证明真实模型质量。

## 门槛

允许进入 DL03；候选效果不是本批阻断。
