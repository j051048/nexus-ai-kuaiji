# DCP04 验收（进行中）：先修批量任务正确性

> 状态：**部分完成（未推送）**。本包改动含新迁移 192（PG）/ 121（SQLite），
> 生产库未应用前不能上线；因此本轮只在本地提交，不推送、不部署。

## 1. 已复现并修掉的缺陷

| 缺陷 | 复现 | 修法 | 证据 |
| --- | --- | --- | --- |
| 重试把 `attempt` 清零 | `retry()` 原为 `SET attempt=0` | 只重排 `failed`，`attempt` 保history，另加 `retry_count` | `test_retry_keeps_attempts_and_never_revives_cancelled` |
| 重试复活 `cancelled` | 原 `WHERE status IN('failed','cancelled')` | 取消/跳过是终态，普通重试不碰；需显式新动作 | 同上 + `test_retry_without_failed_items_is_rejected` |
| 没有失败项时静默成功 | 原无变化也返回 200 | 409「没有可重试的失败项」 | `test_batch_operations.py` 契约更新 |
| 取消/跳过没有入口与原因 | 只有整批 retry | 新增 `cancel_item`/`skip_item`，原因 3–1000 字，写审计 | `test_skip_and_cancel_require_reason_and_terminal_items_are_rejected` |
| 执行中取消会丢意图 | 无此概念 | `cancel_requested_at/cancel_reason` 只记录意图；结算时按边界落地 | `test_cancel_running_item_records_intent_and_keeps_committed_result` |
| 取消项让批次永远不到 100% | 进度只算 succeeded+skipped+failed | 批次新增 `cancelled` 计数，进度与状态含取消 | 同上（服务层断言） |
| 失败清单可能被公式注入 | 无导出 | `failure_csv` 前缀 `'` 转义 `= + - @ \t \r`，仅失败/取消项 | `test_failure_csv_escapes_formula_and_lists_only_authorized_items` |

## 2. 契约与数据

- 逐项状态：`queued/running/succeeded/failed/cancelled/skipped/retry_wait`（沿用既有 CHECK，不新增 unknown；unknown 语义留待 worker 响应丢失场景，见待办）。
- 新列：`batch_operation_items.cancel_requested_at/cancel_reason/skip_reason/retry_count`、`batch_operations.cancelled`。
- 端点：`POST /{batch_id}/items/{item_id}/cancel|skip`、`GET /{batch_id}/failures.csv`（`text/csv`，附件名带批次号）。
- 权限：三个新动作都走 `require_manager`，与既有 retry 一致；批次按 `firm_id` 过滤。

## 3. 未完成（阻塞本包验收）

1. **全局锁（D1）**：`BatchOperationsService.worker_transaction` 仍调用 `accounting.lock_legacy_write()`，领取/结算串行化；未按公司 scope 拆分、未测「一家持锁不阻塞别家」。
2. **PG16/17 真双会话**：本地 WSL 集群当前未启动，本轮只有 SQLite 证据；PG 路径的 `worker_finish_batch_item` 已在新迁移中重写（允许 `cancelled`、统计 `cancelled`），但未在真库跑过。
3. **逐项作用域字段**：`action/object id/version/source fingerprint/idempotency key/result ref` 仍未落列，现用 `result_json` 承载（本包只做到取消/跳过/重试语义）。
4. **未知结果（unknown）**：worker 结算超时/响应丢失后用 `sent_at` 判定的机制未接入批量项。
5. **前端**：批次详情页未接取消/跳过/失败 CSV 下载按钮。
6. **恢复**：新列未纳入 P01 恢复清单的显式字段列表（表级快照已覆盖，字段级待补）。

## 4. 回归

- `pytest nexus_backend/tests/test_batch_operations.py test_dcp04_batch_control.py -q` → 7 passed。
- `ruff check` 全绿；迁移门禁 `check_migration_idempotency.py` 退出 0（新增 192/121 无告警）。

## 5. 上线顺序（下一轮）

1. 在 PG16/17 一次性库跑迁移 192 + 双会话测试（含取消与结算竞态）。
2. 生产 Supabase 先应用 192，再推送镜像（顺序同 N03 教训）。
3. 再补锁拆分与前端接线。
