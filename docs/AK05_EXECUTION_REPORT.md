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

### 3. OCR/分录队列在途心跳

`QueueWorker.run_once()` 不再在领取后立刻写 `maintenance(0)`：

- claim 成功后写 `maintenance(1)`；
- 执行结束在 `finally` 写 `maintenance(0)`；
- 慢模型调用期间不再显示零在途。

这保留原有维护与告警刷新，不新增队列。

### 4. 停止行为

- 停止后不再提交新 Future；
- 仍在执行的任务不写 `stopped`/零在途；
- 有任务未结束进入 `draining`，重复 `start()` 被拒绝；
- 无在途时才写 `stopped` 并停 QueueWorker 心跳。

## 红—绿

新增测试：

```text
test_ak05_unified_concurrency.py
6 passed
```

```text
test_ak05_persistent.py
1 passed
```

```text
test_ak05_unified_concurrency.py
test_ak05_persistent.py
test_unified_worker.py
15 passed
```

真实 SQLite 持久队列：
A 企业 OCR provider hook 阻塞；
B 企业助手先完成，且 OCR 尚未释放。

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
test_ak05_unified_concurrency.py
test_unified_worker.py
test_stage15_assistant_monitor.py
test_batch2_concurrency.py
test_batch_operations.py
28 passed
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

- T01 真实持久队列 + 真实 OCR/助手 worker **已补**：
  `test_ak05_persistent.py` 使用同一 SQLite 持久队列，
  A 企业 OCR 传输阻塞，B 企业助手在释放前成功；
- T01 仍未覆盖 PG/多进程；
- T02 已有“最多 2 个在途”测试，未覆盖大规模峰值/连接池；
- T03 多企业公平与后台不饥饿（多企业共享后台槽仍只有一次机会）；
- T04 全局/供应商上限；
- T06 监控心跳抛异常不阻断队列处理已测；慢 refresh 真实故障注入未测；
- T07 干净 stop/start、draining 拒绝、Future 异常消费已有测试；数据库关闭顺序未测；
- T08 Future 异常已测试；claim 成功后 submit 失败未测；
- T09 撤权范围交错；
- T10 费用/attempt/429 矩阵；
- T11 两个真实进程竞争；
- T12 AK02–AK04 完整联合回归；
- T13 bool/负数/小数/越界配置边界已测；角色冲突组合未完整覆盖；
- 真实隔离 PG 多进程；
- QueueWorker 慢任务期间只有一次 inflight=1，尚无独立周期心跳线程；
- 并发路径未严格使用 assistant/queue/batch/migration 预算权重，当前每 tick 一角色一任务。

## 分层结论

```text
L：有界双槽、真实助手在途心跳、停止排空核心通过
D：真实 PG 多进程未验证
P：真实供应商/线上未验证
```

AK05 本轮技术门槛**未完整通过**，不能宣布完成。
