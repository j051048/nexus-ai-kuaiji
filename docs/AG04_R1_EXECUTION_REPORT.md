# AG04-R1 执行报告

## 状态：指定离线契约通过（单进程 + 快照门禁），双进程恢复未完成。

## 修复

- `Budget.seconds/tokens` 实际参与循环前检查；0 预算不调用。
- 可注入 clock；超时后不再调用。
- 可注入 authorization_check，每次调用/接纳前复查；撤权停止。
- 冲突差额改用 Decimal，非法/非有限值转人工；不补造分录。
- 快照绑定 version、run、企业、账套、预算、计数、证据、终态和 SHA-256。
- `load` 拒绝缺失/未知版本、完整性失败、错误范围/run、计数非法。
- 原子 `save`（临时文件 + os.replace）。

## 测试

```text
test_ag04_investigation.py: 13 passed
```

覆盖 A01/A02/A06/A10/A12/A13/A14 及原 D01–D09。

## 未完成

- A03 token 未知用量专项；
- A04 恢复后剩余预算的显式断言；
- A05 迟到结果；
- A07 结果接纳时撤权；
- A08 混合范围/同 ID 异内容；
- A09 prompt injection 全矩阵；
- A15 写入失败保留最后快照；
- A16 真实独立双进程恢复。

未改生产 worker/meter/attempt/迁移。
