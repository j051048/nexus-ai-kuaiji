# AK05 P2 进展

## 已完成

### `--once` 并发语义

原 `--once` 仍走串行 `run_once()`，慢 OCR 会阻塞助手。

新增：

```text
UnifiedWorker.run_until_idle(timeout=120)
```

- 使用与常驻模式相同的双槽执行器；
- 处理到没有实际工作后退出；
- 关闭执行器，不启动后台循环；
- `main --once` 改用它。

测试：

```text
test_run_until_idle_uses_concurrent_slots_then_stops
```

### budget 契约

`queue_budget` / `batch_budget` / `migration_budget` 不再只存在于构造函数。

现在构造后台加权序列：

```text
queue × queue_budget
batch × batch_budget
migration × migration_budget
```

共享后台槽按该序列轮转，总并发仍为 2。

测试：

```text
test_background_budgets_are_weighted_not_ignored
```

默认：

```text
queue 2 : batch 2 : migration 1
```

### ExecutionTrace context_pack_hash

`assistant/service.py` 的 execution record 现在从持久 `snapshot_json`
计算 SHA-256，写入 `diagnostics.context_pack_hash`；
不再固定为 `None`。

## 未完成

- 真实 PG 投影失败项；
- T11 provider 已发送后强杀/恢复；
- 超大文件/授权谓词收敛；
- 完整 PG 多进程矩阵。
