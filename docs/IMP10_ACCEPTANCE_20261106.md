# IMP10 验收记录：通知展示与数据库事件完整性

- 日期：2026-11-06
- 工程状态：**本地/ PG16/17 表征通过**；无生产代码变更，无新增迁移。
- 外部状态：真实通知渠道、真实客户端消息、真人未验证。
- 候选：HEAD `143d4aea7154b1905b96ab757ecd14865354b641`，source 1927 / `e6a947031fe9e3b4bd23b522b29ef46bbfddd89232a5c9d989b70a170f44baa5`，migration 338 / `2c96acd52de191347a2365962e879340918fad013786faa260b4b13140646116`。
- 范围：`client_messages`、`client_message_reads`、`client_task_notification()`、`FirmTasksService.messages`；仅客户端站内通知，不外发、不自动发送。

## 已有能力（复用，不重建）

- PG 217：`client_task_notification()` 由 `business_actions`/`material_requests`/`material_request_actions`/`monthly_delivery_packages` 触发器调用；收件人由业务行决定，`hold` 时不生成，`ON CONFLICT(id) DO NOTHING` 去重。
- PG 215：`client_messages`/`client_message_reads` RLS；广播已读按用户记录，`read_at` 触发器生成。
- `FirmTasksService.messages` / `message_target` / `mark_message_read`：Python 读侧组合；不改变业务状态。
- 既有测试：`test_client_messages_boundaries.py`、`test_ux01_03_postgres.py`、`test_ux04_06_postgres.py`、`test_c01_full_recovery_postgres.py`（client_messages 恢复）。

## 10-T 对照

| 编号 | 要求 | 证据 |
| --- | --- | --- |
| 10-T1 | 新旧消息同入口，类型/收件人/来源版本不丢，历史正文不改 | 既有 `client_messages` 行模型 + `messages` 查询；未新增模板版本，正文不回写 |
| 10-T2 | 回滚不产生可见通知；重复业务事件不二次发送 | `test_pg_concurrent_return_produces_one_private_notice`：并发返回 200/409，消息 1 条；delivery_notifications 0 |
| 10-T3 | 原生 SQL 跨企业/伪造收件人/改来源/权限不足/hold 拒绝 | `test_pg_message_recipient_receipts_and_native_sql_boundaries`：跨企业不可读、伪造已读失败、UPDATE body 0 行 |
| 10-T4 | 并发撤权/重复插入/领取结算围栏；unknown 不自动发送 | `test_pg_broadcast_concurrent_reads_and_revocation`：两并发已读、撤权后消息清空；N03 测试覆盖 unknown 不自动重发 |
| 10-T5 | PG16/17 前向升级后新旧可读，历史 checksum 不变 | 两库 `migrate_accounting.py migrate` 成功；候选 migration 338/`2c96acd5…` 未变；PG 测试 5 passed×2 |
| 10-T6 | 联合恢复后业务可读可办，不补造审核/送达/签收 | P01 `next11-recovery-d3f7-r2` 已含 database/originals/identity/permissions/hold/review_export/tax_delivery/no_replay=true；恢复后 `client_messages` 不重建历史 |

## 命令与结果

| 套件 | 命令 | 结果 | 证据 |
| --- | --- | --- | --- |
| SQLite 边界 | `.venv/Scripts/python.exe -m pytest -q nexus_backend/tests/test_client_messages_boundaries.py` | **3 passed / 8.21s** | 终端输出 |
| PG17 | `scripts/test_accounting_postgres_isolated.py --config …/pg17-recovery_source.json test_ux01_03_postgres.py test_ux04_06_postgres.py` | **5 passed / 11.77s** | `tmp_test/ak05-pg-runs/60f19c40c1124f47b9c5cf4cfe2eb01d/results.xml` |
| PG16 | 同上，`pg16-recovery_source.json` | **5 passed / 4.89s** | `tmp_test/ak05-pg-runs/a4c8d24008314a4f825025ac928cd9ae/results.xml` |
| 迁移状态 | `migrate_accounting.py status`（管理员连接经环境变量，不记录 DSN） | 17/16 均 exit 0；217 已应用 | 终端输出（脱敏） |

## 诚实限制

- 没有真实邮件/短信/微信通道；站内消息不等于送达/查看/签收。
- 没有新增模板版本；历史正文不可变是当前实现约束，不是对所有未来模板的自动保证。
- 未证明 200–500 家规模；真实客户端消息量、清理与归档策略未验证。
- 本次没有代码改动，不能把“复核既有能力”写成“完成数据库瘦身”。
