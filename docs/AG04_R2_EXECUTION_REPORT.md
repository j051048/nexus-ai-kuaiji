# AG04-R2 执行报告

## 状态：指定离线契约通过（含独立进程加载）。

## 修复与证据

- 0 预算、假时钟超时、token 未知停止；
- 迟到 resolved 拒收；
- 事实版本回调在执行前/结果接纳前检查；
- 运行中撤权停止；
- 批次全量校验后原子接纳；
- Decimal 差额；
- 快照版本、SHA-256、范围/run、计数/终态校验；
- 独立子进程加载 unknown 快照，calls 不增加，不调用 resolver。

```text
test_ag04_investigation.py: 22 passed
```

覆盖 A01–A10、A12–A15、A17 及 D01–D09。

## 未完成

- A18 并发保存；
- 自动续跑未实现（恢复为人工接手状态）。

不声明生产多 worker、provider 取消通过。

