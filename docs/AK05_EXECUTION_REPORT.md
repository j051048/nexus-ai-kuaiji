# AK05 执行报告

## 结论：本地部分完成；AK05 全部门槛未通过。

## 已实现

### 1. 两槽有界并发

`UnifiedWorker` 新增：

- `ThreadPoolExecutor(max_workers=2)`；
- 助手固定一槽；
- OCR/分录/批量/迁移共享另一槽；
- 每类角色最多一个在途 Future；
- 只有本地槽空闲才调用现有 `worker.run_once()` 领取；
- 不预领取大批任务；
- 保留原 `run_once()` 串行兼容入口。

### 2. 真实在途与心跳

- `_inflight` 按角色记录 Future；
- 助手心跳只统计 assistant 角色，不把后台任务计入；
- `AssistantMonitor.heartbeat()` 只写持久心跳，不触发 `refresh_pending`；
- `tick()` 保留“心跳后刷新告警”的兼容行为；
- 统一 worker 周期心跳使用 `heartbeat()`。

### 3. 停止行为

- 停止后不再提交新 Future；
- 仍在执行的任务不写 `stopped`/零在途；
- 有任务未结束进入 `draining`，重复 `start()` 被拒绝；
- 无在途时才写 `stopped` 并停 QueueWorker 心跳。

## 红—绿

新增测试：

```text
test_ak05_unified_concurrency.py
3 passed
```

- 慢后台任务不阻塞助手；
- 助手在途=1，后台不计入助手心跳；
- 停止时未完成任务不报 stopped。

心跳专项：

```text
test_stage15_assistant_monitor.py
新增 test_ak05_lightweight_heartbeat_does_not_run_refresh
```

回归：

```text
test_ak05_unified_concurrency.py
test_unified_worker.py
test_stage15_assistant_monitor.py
14 passed
```

```text
test_stage15_conversations.py
40 passed（含 monitor 组合）
```

```text
test_batch2_concurrency.py
test_batch_operations.py
test_unified_worker.py
17 passed
```

```text
test_queue_workflow.py
34 passed, 2 failed
```

2 项失败为当前候选既有 AK03 `original_unverified` 阻断：

```text
票据或流水核对异常：缺少独立原件凭证
```

不是 AK05 并发路径失败，但说明当前候选不能算全绿。

## 未完成

- T01 使用真实持久队列 + 真实 OCR/助手 worker（当前为受控 FakeWorker 时序测试）；
- T02 大量任务峰值 Future/线程/领取上界；
- T03 多企业公平与后台不饥饿；
- T04 全局/供应商上限；
- T06 慢 refresh/心跳落库失败；
- T07 重复 stop/start、数据库关闭顺序；
- T08 claim 后 submit 失败；
- T09 撤权范围交错；
- T10 费用/attempt/429 矩阵；
- T11 两个真实进程竞争；
- T12 AK02–AK04 完整联合回归；
- T13 配置边界；
- 真实隔离 PG 多进程；
- QueueWorker `maintenance(0)` 的并发覆盖仍未改；
- 并发路径未严格使用 assistant/queue/batch/migration 预算权重，当前每 tick 一角色一任务。

## 分层结论

```text
L：有界双槽、真实助手在途心跳、停止排空核心通过
D：真实 PG 多进程未验证
P：真实供应商/线上未验证
```

AK05 本轮技术门槛**未完整通过**，不能宣布完成。
