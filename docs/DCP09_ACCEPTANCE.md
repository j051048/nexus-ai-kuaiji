# DCP09 验收：先测量，再把 JIT 关在事务里

## 结论

当前候选上，`/api/workspace/bootstrap` 的瓶颈**仍然是 JIT**，不是旧候选的偶然现象。只读事务内 `SET LOCAL jit = off` 后：

| 指标（PG16，30 次采样） | 优化前 | 优化后 |
| --- | --- | --- |
| db p50 | 655.63 ms | **94.43 ms** |
| db p95 | 897.66 ms | **129.86 ms** |
| wall p50 | 660.76 ms | **98.79 ms** |
| 冷启动首请求 | — | **135.34 ms**（第二次 99.57 ms） |

同一 SQL 的 A/B（`tmp_test/p05_jit_ab.py`，各 8 次）：`jit=on` p50 583.61 ms / p95 680.68 ms；`jit=off` p50 **79.19 ms** / p95 83.35 ms。

## 环境与方法

- PG16：WSL `p02pg16`，`127.0.0.1:25531`，库 `p02_chain`（已迁到 195），应用以受限角色 `p02_app_pg16` 连接；Windows 侧客户端。PG17：`127.0.0.1:25532`。
- 工具：`tmp_test/p05_measure.py`（包装 `PostgresConnection.execute/executemany`，记录条数、去重指纹、db 耗时、墙钟；输出中位数与 p95）、`tmp_test/p05_jit_ab.py`、`tmp_test/dcp09_cold.py`。
- 每轮用新事务所/公司/账套播种（虚构数据），固定 2026-10 期间；成功态采样 30 次，冷启动单独一次进程。
- 本次只测 SQLite 之外的 PG16 主路径；浏览器请求数/传输量、队列等待未测（见未完成）。

其他关键端点（30 次，优化后）：`/api/books/{id}` p50 46.29/p95 53.07（9 语句、8 指纹，含 1 处重复）、`/api/books/{id}/vouchers` 11.48/12.92、`/api/tax/context` 11.94/12.79、`/api/workbench` 10.97/12.02、`/api/firm/members` 2.75/3.03、`/api/me` 2.02/2.37。

## 改动

`nexus_backend/app/accounting/database.py`

- `Database.transaction(..., jit=None)`：`jit=False` 时在 `conn.begin(...)` 之后执行 `SET LOCAL jit = off`。**只作用于当前事务**，提交/回滚即失效，不改数据库默认，不写连接串参数。
- `jit` 与 `write=True` 同时出现直接 `ValueError`，避免写路径携带隐式计划假设。
- SQLite 方言下是 no-op（SQLite 无 JIT）。

`nexus_backend/app/workspace/bootstrap.py`：首页读事务显式 `jit=False`。

没有改：全局默认、连接池配置、其他端点；也没有引入任何跨请求缓存。

## 红测（先写后改）

`nexus_backend/tests/test_dcp09_jit_scope.py`

1. `test_write_transaction_cannot_carry_a_plan_switch`：写事务带 `jit=False` 必须报错；
2. `test_read_transaction_keeps_results_identical`：同一查询在两种事务下结果相同；
3. `test_bootstrap_is_equivalent_and_repeats_without_leaking`：两次 bootstrap 返回完全一致且都有当前事务所；
4. `test_postgres_connection_does_not_keep_jit_off`（需隔离 PG）：事务内 `SHOW jit = off`，**同一连接的下一个事务 `SHOW jit = on`**——池里不会把设置带给下一个请求。

结果：SQLite 3 passed / 1 skipped；PG16 **4 passed**；PG17 用 psql 级验证 `SET LOCAL` 语义（`off` → 提交后 `on`），应用级未跑（见下）。相关回归 `test_stage22_home_bootstrap.py` 等 **16 passed**，ruff 通过。

## 决策

1. **合并重复查询：不做。** `/api/books/{id}` 的 9 条语句里有 1 条重复（`access.book` 在路由守卫和服务内部各查一次）。这是授权边界的重复校验，按手册「合并前先确认所有授权边界」的要求，收益（约 13 ms/请求）不足以承担撤权语义变化的风险，保留并在报告中记录。
2. **诊断分页、轮询合并：不动。** 本轮没有测到全量重算或重复轮询的证据，不凭猜测改。
3. **JIT 关闭范围：只这一个只读事务。** 不做全库默认，不做全局 `jit=off`。

## 未完成 / 未验证

1. **PG17 应用级未跑**：本地 `p02_chain` 停在 178，迁到 195 在本轮失败（`migrate_accounting.py` 报「数据库访问失败」）；只用 psql 验证了 `SET LOCAL` 语义。
2. 浏览器侧请求数、传输量、可用内容出现时间未测；
3. 队列等待、锁等待未纳入采样；
4. 助手 progress/诊断分页未测（P05 的 521→84ms 目标未预设，本包按实测给结论）；
5. 压测工具仍放在 `tmp_test/`（未入库），复跑依赖本地 PG 与 `tmp_test/p02-pg.local.json`。
