# NEXT06 验收记录：通知通道上线准备

- 工程状态：本地验收通过（最终候选 1816／`dddef1a8…`）。
- 外部验收：未授权／缺少供应商环境。未连接真实邮件、短信或推送供应商，未向真实客户发送任何消息。

## 范围与实现

复用既有领取→事务外发送→围栏结算、unknown 核查、收件人绑定与月度包授权，本轮补齐：

- `nexus_backend/app/delivery/receipts.py`：HMAC-SHA256 回调验签（`timestamp + "." + raw`）、5 分钟窗口、64 KiB 上限、`provider_event_id` 幂等、同键不同内容 409、乱序时间线；供应商事件 append-only，不修改通知状态，不生成客户签收。
- `nexus_backend/app/delivery/callback_router.py` + `main.py`：`POST /api/delivery/provider-callback` 为免 Bearer 的公开回调，但仍要求签名；`GET /api/books/{book_id}/delivery/notifications/{notification_id}/receipts` 返回通道接受／送达／查看／退信与独立签收状态。
- `nexus_backend/migrations/sqlite/129_delivery_provider_events.sql`、`postgres/205_delivery_provider_events.sql`：事件表、租户读策略、worker 写入、append-only 触发器。
- `postgres/206_delivery_worker_read.sql`、`207_delivery_worker_event_read.sql`：仅授予 worker 解析信封和读回插入行所需列，未扩大 app 权限。
- `nexus_backend/app/delivery/worker.py`：`--once/--interval/--limit`，仅从 `DELIVERY_CHANNEL` 构造通道，不打印 DSN。
- `docs/NEXT06_DEPLOYMENT.md`：配置、回调契约、worker、上线检查清单与外部门槛。

## 本地命令与结果

候选（运行前后一致）：HEAD `df3fface95d1f69c28e859e89015465955c517b4`；源文件 1816／`dddef1a88eee0bfdc2c2db37d09560dc0290b434fe800993c05b52b7ba2a963e`；迁移 325／`48fc7b47abd864e39ec6701939dd42f0bb65c1bc16732bcff2c6686350a0c8e1`。该候选含并发任务的 `TaxFilingRecords.tsx` 修改。

`python scripts/next_harness.py run --task next06 --run-dir tmp_test/next/next06-acceptance-20261103-final3`
→ manifest `status=passed`，`candidate.stable=true`，`cleanup.status=passed`，退出码 0。

| 阶段 | 结果 | 证据 |
| --- | --- | --- |
| notification-regression（dcp07 + n03 + next06 回执） | `66 passed, 1 warning in 287.10s`，exit 0 | `tmp_test/next/next06-acceptance-20261103-final3/evidence/notification-regression.log` |
| delivery-regression（followups + investigation + monthly package） | `42 passed in 189.17s`，exit 0 | `tmp_test/next/next06-acceptance-20261103-final3/evidence/delivery-regression.log` |

`final2` 在 54d9 上通过，因并发税务 UI 修改使候选变为 ddb 后失效；`r5`/`final2` 仅保留为历史证据，不再作为最终验收。

## PostgreSQL 16/17

`test_n03_notification_postgres.py`（含新增 `test_provider_receipt_uses_worker_insert_and_app_tenant_read`）：

- PG17：23 passed／115.26s。
- PG16：23 passed／113.68s。

覆盖 worker 角色插入回执、app 角色租户读取、重复事件幂等、跨企业不可见、app 不能直接写事件表。测试配置来自隔离本地集群，未使用生产库。

## 边界与未验证

- 本地 `local_sink` 与 MiniSmtp 只证明适配器和围栏契约，不证明供应商接受、真实退信、真实送达或客户查看。
- 未接真实供应商沙箱、真实 webhook 签名密钥或生产 TLS；这些是外部门槛。
- 回调接线在 NEXT03 只做本地契约，NEXT06 未连接真实供应商，因此不能宣称真实回调已验证。
- 通知打开、送达、客户签收仍分别记录；回执不会自动生成签收。
