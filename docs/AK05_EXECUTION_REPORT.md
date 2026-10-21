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
12 passed

test_stage15_assistant_monitor.py
6 passed（含慢 refresh 心跳）

test_unified_worker.py + test_ak05_persistent.py + test_ak05_unified_concurrency.py
17 passed
```

```text
test_ak05_persistent.py
2 passed
```

```text
test_ak05_unified_concurrency.py
test_ak05_persistent.py
test_unified_worker.py
15 passed
```

```text
test_ak01_attempt_matrix.py
test_batch2_concurrency.py
51 passed
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
- T03 后台 queue/batch/migration 已改为轮转，单元测试通过；真实数据库持续积压公平仍未测；
- T04 本地容量测试通过；真实 PG：
  `test_batch2_concurrency_postgres.py + test_stage15_mixed_queue_postgres.py`
  20 passed / 1 failed；失败项仍以 `system` 直接写投影表，与 138 projection 身份冲突，
  属受保护 PG 投影测试，未修改；
- T06 监控心跳抛异常不阻断队列、慢 refresh 期间心跳已落库；QueueWorker 周期心跳线程已实现并单测；
- T07 干净 stop/start、draining 拒绝、Future 异常消费已有测试；main 关闭前等待 `wait_for_idle(30)`，未排空不提前关库；进程级关闭未测；
- T08 Future 异常和 submit 失败不泄漏在途已测试；claim 成功后真实 submit 竞争未测；
- T09 新增真实并发撤权：助手已进入 provider 后撤销企业成员，turn 必须 stale 且无 assistant 消息；
- T10 复用 `test_ak01_attempt_matrix.py` + `test_batch2_concurrency.py`，51 passed；真实 PG/真实供应商未验证；
- T11 两个真实进程竞争、发送后终止、恢复进程（未执行）；
- T12 AK02–AK05 受影响回归：
  `test_ag02_context_pack`、`test_ag03_answers`、`test_ag04_investigation`、
  `test_ag05_examples`、`test_intelligence_workflow`，262 passed；
- T13 bool/负数/小数/越界、assistant-only/queue-only/migration-only、重复 stop 已测；
- 真实隔离 PG 多进程；
- QueueWorker 已增加独立轻量心跳线程（10 秒）；周期线程有测试，真实慢 OCR 跨周期落库未测；
- 并发路径未严格使用 assistant/queue/batch/migration 预算权重，当前每 tick 一角色一任务。

## 分层结论

```text
L：有界双槽、真实助手在途心跳、停止排空核心通过
D：真实 PG 多进程未验证
P：真实供应商/线上未验证
```

AK05 本轮技术门槛**未完整通过**，不能宣布完成。
