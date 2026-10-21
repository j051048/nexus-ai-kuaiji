# AG04-R3 执行报告

## 状态：R4-2 指定离线契约通过；不涉及生产。

## 新增

- resolver 只接收不可变 `RunView`，不能再篡改 calls/tokens/范围/预算；
- 执行器保留权威计数与状态；
- 已知/未知用量并存时，已知 token 保留，未知显式记录并停止；
- 非法 token 类型/负数单独报 `usage_invalid`；
- 快照语义校验：
  - running 不得带 handoff/stop_reason；
  - resolved 必须是 `evidence_resolved` 且无 handoff；
  - handoff 必须有接手包；
  - 超预算必须有 token 停止证据；
  - 空范围/重复冲突证据拒绝。
- 保留原子保存、独立进程加载、迟到结果拒收、撤权/版本检查、Decimal。

## 测试

```text
test_ag04_investigation.py: 31 passed
```

新增覆盖：
- A05 resolver 篡改失败；
- A03 已知+未知用量保留；
- A06/A08 语义非法快照（校验和正确也拒绝）。

## 未完成

- 完整 A02 非法 usage 参数矩阵；
- A04 同调用多证据计费边界；
- A07 配置版本绑定；
- A09 失权证据投影；
- A11 冲突金额 NaN/Inf 的 handoff 完整性（已有 Decimal 拒绝，未全部转接手）。

