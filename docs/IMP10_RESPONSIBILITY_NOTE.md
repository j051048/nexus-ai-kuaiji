# IMP10 职责说明：通知展示组合与数据库事件完整性

- 日期：2026-11-06
- 范围：仅通知展示文案/读侧组合与数据库事件完整性；不迁移安全约束，不新建消息队列，不重写数据库方言。
- 结论：**本次不新增迁移、不移动数据库防线**；数据库保留原子事件产生、收件人绑定、RLS 与恢复 hold，Python 只负责按现有列组合展示，并复用既有 `FirmTasksService.messages` 读侧。

## 不变量

1. 通知必须由业务事件原子产生（`business_actions`、`material_requests`、`material_request_actions`、`monthly_delivery_packages` 的触发器），不在请求线程里另发。
2. `firm_id/company_id` 来自业务行，收件人来自事件申请人、资料负责人或包联系人；客户端不能指定。
3. `client_messages_read` 只允许 `recipient_user_id=actor_user()` 或具备可见性的经理/企业成员；广播已读按用户单独记录。
4. `client_message_reads` 只允许本人写入，`read_at` 由触发器生成；读取不改变审批/签收状态。
5. 恢复 `restore_state.hold` 为真时不产生新通知；不得补造历史通知、审核、送达或签收。
6. 历史 `client_messages.body` 与审计只追加，不因展示模板升级回写；当前无模板版本字段，因此本次**不新增模板版本**。

## 现存边界

| 层 | 保留职责 | 位置 |
| --- | --- | --- |
| DB 触发器 | 事件原子生成、收件人绑定、hold 抑制、去重键 `task:<table>:<id>` | PG 217；SQLite 对应迁移 |
| RLS/授权 | 企业/收件人可见性、经理广播、本人已读 | PG 215 |
| Python 读侧 | 查询、未读数、单条已读、目标链接组合 | `nexus_backend/app/firm_tasks/service.py` |
| HTTP | `GET/POST /api/firm-tasks/messages...` | 既有路由 |
| 前端 | 消息列表、已读、跳转原事项 | 既有客户端消息页 |

## 本次改动

- 无生产代码、无迁移、无 schema 变更。
- 新增本说明与 `docs/IMP10_ACCEPTANCE_20261106.md`；仅记录既有实现与验证，不把“整理”写成“瘦身完成”。

## 明确未做

- 未把通知生成迁到 Python/消息队列。
- 未改变 RLS、append-only、hold、收件人绑定或去重语义。
- 未新增模板版本；若未来需要版本，必须先定义新旧记录读取策略并保持历史正文不可变。
- 未覆盖所有通知域；本试点只到客户端站内消息。
