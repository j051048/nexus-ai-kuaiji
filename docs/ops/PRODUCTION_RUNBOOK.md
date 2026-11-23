# 生产应急手册（PRODUCTION RUNBOOK）

- **适用版本**：迁移水位 PG `324` / SQLite `231`（本手册随迁移推进更新）
- **读者**：值班工程师、实施顾问、DBA
- **原则**：先止损与留证，再定位与修复；**任何**「重试/重发/回滚」都必须先确认外部副作用是否已经发生（银行、税局、客户系统）。
- **禁止**：在生产库上手工改账、改状态、删审计、跳过 `unposted_manual` 之类门禁；这些操作会让审计链失效，事后无法自证。

---

## 0. 通用第一步：拿请求 ID 与结构化故障日志（30 秒内）

1. 客户截图/报错里的 `X-Request-Id` 就是关联键；没有就让他再操作一次并抓响应头。
2. 服务端日志按单行 JSON 输出（`app/observability/failures.py`），字段：
   `event/request_id/trace_id/firm_id/method/route/error_class/sqlstate/safe_location`。
3. 定位命令（按你所在环境改日志路径）：

```bash
grep '"request_id":"<REQUEST_ID>"' /var/log/accounting/app.log | tail -20
```

4. 判读顺序：
   - `error_class` = `AccountingError` → 业务拒绝，看响应体中文文案，**不是故障**；
   - `error_class` 含 `DatatypeMismatch` / `UndefinedColumn` / `psycopg` → SQL 层问题，看 `sqlstate` 与 `safe_location`（文件名:行号）；
   - `sqlstate=57014` → 语句超时；`53300` → 连接数超限；`40P01`/`40001` → 死锁/序列化失败（可安全重试一次）。
5. `response` 已按写/读区分：写请求返回 `result_unknown` 表示**可能已提交**，必须按第 1 节核对，不准直接重放。

---

## 1. 写请求 `result_unknown`（最危险的一种）

判定「到底成没成」的次序：

1. 用业务对象反查：凭证 → `GET /api/books/{book_id}/vouchers`；付款 → `GET /api/treasury/payment-orders`；导入 → `GET /api/fsc/imports`。
2. 用命令键反查（幂等键就为此存在）：`SELECT * FROM posting_commands WHERE command_key=?`（PG 加 `SET ROLE accounting_app;` 与 `app.firm_id/ app.user_id`）。
3. 找到对象 → 用该对象当前状态继续，**不要**再发一次；
4. 找不到 → 视为未发生，可用**同一**幂等键重试一次；仍失败则升级。

银企/税局类 `unknown`：见第 4 节，一律冻结，不允许「再发一次碰运气」。

---

## 2. 连接池打满与慢查询

**症状**：大量 503/`result_unknown`；日志 `sqlstate=53300`；`DatabaseCapacityError`；接口整体变慢但 CPU 不高。

1. 立即看池指标（`/metrics`）：`accounting_db_pool_*`；再看 `pg_stat_activity`：

```sql
SELECT state, wait_event_type, wait_event, count(*)
FROM pg_stat_activity WHERE datname = current_database()
GROUP BY 1,2,3 ORDER BY 4 DESC;
SELECT pid, now()-query_start AS dur, left(query,160)
FROM pg_stat_activity WHERE state='active' ORDER BY dur DESC LIMIT 10;
```

2. 常见根因与处置：
   - **长事务持有公司写锁**（`pg_advisory_xact_lock`）：找到 `state='idle in transaction'` 且 `dur` 大的会话，确认不是正在跑的导入后再 `pg_terminate_backend(pid)`；
   - **缺失索引导致的慢查询**：把 `EXPLAIN (ANALYZE, BUFFERS)` 的 `Seq Scan` 行与行数记进工单，**不要**在生产上直接 `CREATE INDEX`（走迁移）；
   - **同步请求里做重活**（导出、批量重算）：确认调用方是否走异步队列；必要时临时调低入口并发。
3. 池参数只认环境变量（`ACCOUNTING_DB_POOL_MAX` 等），改完必须滚动重启；**不要**临时把上限拉满来「压住」问题。
4. 慢查询应急止血：对单个已确认的坏查询，用 `statement_timeout` 在会话级限制，而不是重启整库。

---

## 3. 5xx 比例告警（Prometheus）

1. 看 `/metrics` 的 `http_requests_total{status=~"5.."}` 按 `route` 聚合，定位到具体路由（`route` 是路由模板，不含 book id）。
2. 拉该时段的结构化故障日志，按 `error_class` 分组计数：

```bash
grep '"event":"request_failed"' app.log | jq -r '.error_class' | sort | uniq -c | sort -rn
```

3. 若集中在单一 `route` → 回滚该功能开关或停用入口（功能开关优先于回滚代码）；若分散 → 先看数据库与外部依赖。
4. 处置完成后在工单记录：`request_id` 样本 3 条、`error_class` 分布、根因、修复提交号。

---

## 4. 银行 / 税局 `unknown` 冻结核查（禁止自动重发）

**铁律**：`bank_unknown` 与税局 `unknown` **只能人工核实终态**，不得从任何入口重发（补导、重新提交、切换渠道都不行）。

1. 冻结：把该指令/申报标为 `unknown` 状态，并在台账登记「已冻结 + 时间 + 操作人」。
2. 取证：
   - 银行：网银流水 / 柜面回单 / 客户经理书面确认（三选一，须含金额、日期、对方账号后四位）；
   - 税局：电子税务局「申报查询」截图或导出（含申报状态与回执号）。
3. 判定：
   - **已发生** → 走 `advance_external(target='succeeded')` 并按证据补登记；若系统里已有重复指令，另一笔按退款/冲销处理；
   - **未发生** → 记录核实依据后按失败处理，再从**新**命令键重发（不得复用原键）；
   - **仍无法判定** → 保持冻结，升级给资金负责人，不得自行解除。
4. 事后：把核实证据挂到对象证据区（`integrations` 的证据接口），确保审计链能追到「谁、凭什么」解除冻结。

---

## 5. PostgreSQL 备份、还原与跨主版本回滚

### 5.1 备份

```bash
python scripts/backup_accounting.py dump --output /backup/accounting-$(date +%F).dump
```

- 归档头会写 `Dumped from database version`；**还原前先比对目标实例主版本**（脚本已内置 `restore_version_conflict` 守卫，冲突会明确拒绝，不得用过滤选项绕过）。
- 备份文件与 `accounting.schema_migrations` 的 `max(version)` 一起记录，后续排查「代码带迁移、库没跟上」时以此为准。

### 5.2 还原（演练优先）

1. 先恢复到**独立实例/独立库**，不要在现网库上直接覆盖。
2. 恢复后核对：`max(version)` 与代码预期一致；关键表行数（`vouchers` / `audit_events` / `payment_orders`）与备份时刻一致。
3. 恢复后必须重跑：登录、凭证查询、一次只读导出；**不要**立刻开放写入。
4. 生产切换窗口内禁止跑迁移脚本；切换后先跑 `python scripts/check_migration_idempotency.py`。

### 5.3 跨主版本回滚检查表

| 检查项 | 通过标准 | 不通过怎么办 |
| :--- | :--- | :--- |
| 源库主版本 | 与目标实例一致，或目标更高且已按官方步骤升级 | 换目标实例，不做降级还原 |
| 归档可读 | `pg_restore -l` 能列出目录 | 归档损坏：启用上一份备份 |
| 扩展与角色 | `accounting_app/worker/system/projection` 四角色存在 | 按部署脚本重建角色后再还原 |
| RLS 状态 | 还原后 `relrowsecurity/relforcerowsecurity` 全为 true | **停止开放**，按迁移重放策略修复 |
| 迁移水位 | `max(version)` 与代码一致 | 差几版就先补迁移（前向），不倒推 |
| 审计链 | 抽样 10 条 `audit_events` 能按指纹串起来 | 视为证据失效，升级给合规负责人 |

**永远不要**：直接删表重建、跳过 RLS 恢复数据、用 `--no-owner` 之类选项掩盖权限问题后当作「已恢复」。

---

## 6. 告警通道自身故障

- 告警发送失败不会打断业务（`AlertDispatcher.dispatch` 永远返回结果而不抛错）：`status='failed'` 时按退避已重试 `max_attempts` 次。
- 通道恢复后无需人工清理：失败不写静默时间戳，下一次成功会真正发出。
- 若怀疑通道配置错误：先在本机用桩验证负载形状（`build_payload('dingtalk'|'wecom'|'webhook', alert)`），再改生产通道配置。
- **禁止**在测试里连接真实钉钉/企业微信/Webhook 地址；联调由客户提供测试群机器人并单独授权。

---

## 7. 值班交接模板

```
事件：<一句话>
时间段：<开始>-<结束>（+08:00）
影响：<哪些客户/账套/功能>
请求 ID 样本：<3 条>
根因：<一句话 + 证据链接>
已做处置：<含是否改过数据/状态>
外部副作用：<银行/税局/客户系统 是否已发生，未知就写未知>
待跟进：<人 + 截止时间>
```
