# AK05 P0/P1 修复记录

## P0-1 stage7 模板失败

根因：SQLite 在 savepoint 内更新 `proposal_jobs` 时依赖进程临时目录，当前环境无法打开 statement journal。

修复：

- SQLite 连接设置 `PRAGMA temp_store=MEMORY`；
- 监控指标写入失败不再改变已完成的模板业务终态；
- 移除临时诊断日志。

结果：

```text
test_stage7_proposal_queue.py: 34 passed
```

## P0-2 queue original_unverified

根因：AK03 后测试夹具缺少独立原件核验，业务校验正确地阻断复核。

修复：

- 在测试中由会计显式核对合成原件并记录依据；
- 不关闭 `original_unverified` 业务闸门；
- 同步修正 FakeProvider 的净额/税额证据引用；
- 修正测试金额与业务金额一致。

结果：

```text
test_queue_workflow.py: 36 passed
```

## P1-1 stop 不把已完成 Future 当在途

`stop()` 先 `_drain()`，再判断 pending。新增测试：

```text
test_stop_does_not_treat_completed_future_as_inflight
```

## P1-2 周期心跳 active 状态

统一 worker 周期心跳只按 assistant 实际在途决定 active/idle，不再用全实例在途。

## P1-3 finally return

移除 `unified_worker.main()` finally 中的 return，消除 SyntaxWarning，避免覆盖真实退出码。

## 回归

```text
test_stage7_proposal_queue.py + test_queue_workflow.py
70 passed
```

```text
test_ak05_unified_concurrency.py
13 passed
```
