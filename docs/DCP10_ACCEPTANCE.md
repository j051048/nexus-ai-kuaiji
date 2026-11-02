# DCP10 验收：批量开期的容量、故障与守恒

## 结论

在本地独占 PG16（`p02pg16`，25531）上，用真实服务与真实 worker 循环跑通 10/50/200 家的月初批量开期：

| 规模 | 播种 | 预检 | 入队 | 执行 | 单项 p50 / p95 | 结果 | 重复账套 | 已开期 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 10 家 | 1.57 s | 86 ms | 24 ms | **3.19 s** | 178 / 178 ms | 10 succeeded | 0 | 10 |
| 50 家（1 worker） | 6.74 s | — | — | **10.54 s** | 168 / 178 ms | 50 succeeded | 0 | 50 |
| 200 家（1 worker） | 26.03 s | **1.84 s** | 0.23 s | **35.86 s** | 170 / 181 ms（max 190） | 200 succeeded | 0 | 200 |
| 50 家（4 worker） | 6.74 s | — | — | **7.11 s** | — / 732 ms | 50 succeeded | 0 | 50 |

200 家 35.86 s，低于手册「200 家开期 60 秒」的**目标值**；但这是单机、单 worker、`generate_materials=false`、无并发读写的条件下测得的，不能外推为生产 SLA。

## 故障注入结果

- **租约过期回收**（`lease`）：worker A 认领后崩溃，租约未到期时 worker B 拿不到同一项；把 `lease_until` 置为过期后 worker B 成功回收并完成，最终 3 项全部终态、**3 个新账套、0 重复**。
- **执行中取消**（`cancel`）：先处理 1 项，取消 1 个排队项，其余继续；最终 `succeeded=2 / cancelled=1`，**新建账套 2**（被取消项没有开期），计数守恒。
- **幂等**：同一 `idempotency_key` 重放不会新增批次；每家公司同期间只有一本账（`duplicate_books=0`）。

## 本包修掉的一个生产级缺陷

`BatchOperationsService.finish()` 在 PG 上先以 `accounting_worker` 身份 `SELECT ... FROM batch_operation_items` 读取消意图。`accounting_worker` 对这张表没有任何表级授权（098 迁移刻意 `REVOKE`，只给 SECURITY DEFINER 函数执行权），因此**在 PostgreSQL 上每次结算都会抛 InsufficientPrivilege**，被 worker 的兜底逻辑改写成 `retry_wait`，重试三次后转 `failed`——而账套其实已经创建，形成「账已开、任务报失败」的错位。

SQLite 测试从未覆盖到，因为 SQLite 分支不走这条预读。修法：PG 上把取消意图的读取放到调用者自己公司范围的只读事务（`_cancel_intent`），worker 角色权限不放大；SQLite 分支保持原逻辑。修复后 `test_dcp04_batch_control.py` 与 `test_batch_operations.py` 7 passed，PG 上的批量执行与结算全部通过。

## 方法与可复跑

工具（已入库）：`tools/capacity/dcp10_capacity.py`

```bash
python tools/capacity/dcp10_capacity.py run   --admin <admin-dsn> --app <app-dsn> --companies 200 --out tmp_test/dcp10-200.json
python tools/capacity/dcp10_capacity.py lease --admin <admin-dsn> --app <app-dsn> --companies 3
python tools/capacity/dcp10_capacity.py cancel --admin <admin-dsn> --app <app-dsn> --companies 3
```

- 数据：每家一个虚构企业 + 2026-10 账套，批次统一开 2026-11；播种逐条走 `DirectoryService`/`AccountingService`，不使用 SQL 直插业务表。
- 连接角色需要同时是 `accounting_app` 与 `accounting_worker` 成员（模拟 worker 服务的进程），本地用 `p02_wrk_pg16`。
- 守恒检查直接查库：批次项状态计数、每公司 2026-11 账套数（重复即失败）、新账套的 `periods.status='open'` 数。

## 未验证 / 未完成

1. **PG17 未跑**：本地 PG17 库的迁移状态仍不完整，本轮没有在 17 上重复容量测试；
2. **未注入**：真实进程 kill、响应丢失、存储 5xx、预算耗尽、执行中撤权、供应商限流假延迟；
3. **未做 60 分钟稳态观察**，也没有混合读写负载（员工录入/助手查询与批量并行）；
4. 未测 OCR/模型替身延迟对批量导出的影响；
5. 200 家播种 26 s 是串行 API 调用，不代表生产开通速度。

## 对业务的含义

- 200 家量级在本地单 worker 下可控（约 36 s），瓶颈在「每家一次开期」的串行成本，不在队列；
- 4 worker 只带来约 1.5× 提升且 p95 明显变差（732 ms），当前写锁范围下并发收益有限——这与 DCP03 的 D1 结论一致；
- 真正的风险不是慢，而是「账已开、任务记失败」这类错位：修复后仍建议在生产用一次小批次（2–3 家）验证结算状态再放大。
