# N03 交付联系人、签收与通知一致性（部分完成，2026-10-27）

本轮只做 N03 第一组与第二组中不依赖新状态机的部分；未提交、未推送、未外发任何消息。

## 1. 已修复

### 1.1 提醒不得发给不在该包版本上的联系人（先红后绿）

复现：`monthly_delivery_packages.contact_user_id` 在冻结时写死；`configure()` 换人只新增 `monthly_delivery_grants` 版本。`queue()` 原取 `grant.recipient_user_id`，于是提醒发给新联系人 B，而 B 无法签收旧包（`acknowledge()` 只认冻结联系人 A）——「提醒 → 打不开 → 无法签收」的死链。

红测：`nexus_backend/tests/test_delivery_followups.py::test_reminder_cannot_target_a_recipient_outside_the_frozen_package`
修复前 `DID NOT RAISE AccountingError`（退出码 1）。

修复（`nexus_backend/app/delivery/followups.py`）：提醒收件人固定取 `package["contact_user_id"]`；当冻结联系人与当前授权不一致时 409 阻断，文案指明两条正确下一步：

```
当前交付收件人与该包版本冻结的联系人不一致；请重新生成交付版本，或按冻结联系人重新配置交付收件人
```

对 `package_link` 一类任务同样校验；冻结时没有联系人的历史包仍可通过显式授权补位（保留 P01 的「缺联系人可受控恢复」语义）。

按新契约更新既有用例 `test_missing_contact_is_a_controlled_error_then_grant_unblocks`：授权换人后状态仍为 `valid/grant`，但提醒改为 409 并要求重新生成交付版本。这属于**契约变更**，已在用例里写明理由。

### 1.2 通道未启用不消耗重试额度

`dispatch()` 在 `ChannelDisabled` 分支原执行 `attempts=attempts+1`，每轮派发都烧额度，`MAX_ATTEMPTS=5` 后任务会被判失败——「没发出去」被记成「发失败」。

红测：`test_disabled_channel_keeps_tasks_pending` 追加连续 6 轮派发后断言 `attempts == 0 and state == 'pending'`；修复前 attempts 已累加（失败）。
修复：该分支只更新 `updated_at/last_error`，不改 `attempts`、不改状态。

## 2. 发现但未修复（明确阻断，不用半成品替代）

**外发仍在写事务内执行。** `nexus_backend/app/delivery/followups.py:479-535` 的 `dispatch()` 用 `with self.db.transaction(write=True)` 包住整段循环，并在其中调用 `channel.deliver(payload)`；这违反「外部发送逻辑不能在持有数据库写事务/企业锁期间执行」。

不能就地改成两阶段而不动表结构：`delivery_notifications.state` 目前 `CHECK(state IN('pending','sink_written','failed','cancelled'))`（`nexus_backend/migrations/sqlite/105_monthly_delivery_followups.sql:35`），缺少 `sending` 与租约列；领取/发送/结算分阶段需要新增迁移（含 SQLite 表重建）并对齐 PG 守卫。本轮**不做**该改动，避免引入未验证的表结构变更。

下一轮的最小方案（已设计未实施）：
1. 新增前向迁移（PG 171 / SQLite 108）：`state` 增加 `sending`，加 `lease_token`、`lease_until`、`attempts` 语义保持；
2. `dispatch()` 三段：短事务领取（`UPDATE … SET state='sending',lease_token=? WHERE id=? AND state IN('pending','failed')`）→ 事务外发送 → 短事务带围栏结算（`WHERE id=? AND state='sending' AND lease_token=?`）；
3. 过期租约回收与「结果未知」状态，未知不自动重发。

## 3. 测试与门禁

| 项 | 命令 | 结果 | 退出码 |
| --- | --- | --- | --- |
| 交付跟进（含新增 2 例） | `pytest nexus_backend/tests/test_delivery_followups.py -q` | 13 passed | 0 |
| 静态检查 | `ruff check`（两个改动文件） | All checks passed | 0 |

数据库：SQLite（本地一次性库），未涉及 PG 变更；本轮无新迁移。

`test_stage9_delivery.py` 37 failed 均为既有红灯：错误统一为「制单人不能独立复核本人创建版本，请交由另一位有权限的会计」，用例内部用同一身份制单并复核，与本轮改动无关（该文件与复核策略未改）。未把它们计为通过。

## 4. 源码指纹

| 文件 | sha256 前 16 位 |
| --- | --- |
| `nexus_backend/app/delivery/followups.py` | `2cc49db35f9a7daa` |
| `nexus_backend/tests/test_delivery_followups.py` | `dd7375db8c7f9686` |

`git diff --stat`：`followups.py` +32/−3、测试 +23/−1。

## 5. 未完成（N03 余项）

1. 外发两阶段与围栏（见第 2 节），含「发送前失败 / 发送后结果未知」区分与恢复隔离期不派发；
2. 联系人更换 × 旧包 × 新包 × 历史回执的矩阵用例；
3. 状态六态（已生成/尚未发送/结果未知/已发送/客户已打开/已签收）在 API 与页面上分别表达；
4. PG16/17 的联系人与通知旁路负向测试；
5. 签收完成后不再产生催签待办的服务端断言。

本轮未接入任何真实通知通道，也未启用任何外发。
