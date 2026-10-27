# P01 月度交付运营闭环 — 验收记录（2026-10-27）

候选工作树：`tmp_test/qr01-integration`（基线 `32e3042d`）。全部为本地虚构数据，未接真实模型、未外发消息、未提交未推送。

## 一、本批交付内容

### 1. 后端：签收授权台账 + 提醒任务 + 人工跟进流

| 迁移 | 内容 |
| --- | --- |
| `nexus_backend/migrations/postgres/168_monthly_delivery_followups.sql` | 三张表：`monthly_delivery_grants`（book/period 维度的签收人授权，版本化）、`delivery_notifications`（提醒任务，`dedupe_key` 唯一、状态机守卫）、`audit_events` 复用 |
| `nexus_backend/migrations/sqlite/105_monthly_delivery_followups.sql` | 同构 SQLite 版本 |

服务 `nexus_backend/app/delivery/followups.py`（`DeliveryFollowupService`）：

- `configure(actor, book_id, DeliveryGrantRequest)`：`validate_period` → 账套复核权限 → 事务所角色 ∈ {owner, admin, reviewer} → 期间存在 → 收件人是该客户企业**当前**成员，否则 422 `收件人不是该客户企业的当前成员`；写审计 `delivery.grant.configure`。
- `revoke(...)`：版本前移；重复撤回/无授权 409。
- `queue(actor, book_id, DeliveryQueueRequest)`：`signature_reminder`/`signature_overdue` 两类；包必须已冻结（否则 409 `尚未生成月度交付包`）、必须先有授权（否则 409 `尚未配置签收人`）、已有签收则 409 `客户已签收本月资料`；**授权已撤回后必须重新配置**（409 `交付授权已撤回，请重新配置收件人后再安排提醒`）。
- `status(actor, book_id, period)`：授权、包、签收、任务四段状态 + `reasons`；收件人状态 `valid / invalid / missing`，并回带 `source`（`grant` 或 `service_plan`）。
- `followups(actor, limit)`：事务所级**只列需要人动一下**的月份，每行带 `reasons`、`next_step`、`owner`（`close_period/accountant`、`publish_package/accountant`、`configure_recipient|fix_recipient/firm_manager`、`await_customer_signature/customer`）。

去重口径：`signature:{package_id}:v{version}:g{grant_version}` —— 同一授权下的重复请求不重复提醒；重新授权（版本前移）视为新事件，允许再次提醒。撤回后 `cancelled` 为终态（DB 触发器守卫），因此不复用旧行。

**收件人只在“仍欠签名”时才算问题**：客户签收后未配置收件人不再出现在事务所待办里（`followups` 里 `if package and not ack` 才追加 `recipient_*`）。

### 2. 前端：事务所收件箱的交付待办区

- `src/delivery-followups.ts`（新增）：只做展示映射，不推导状态。未知 reason/step/owner **原样显示**，不编造下一步；`validateFollowupFeed` 拒绝不符合契约的响应（期间必须 `20xx-MM`）。
- `src/delivery-followups.test.ts`（新增，5 例）：代码转人话、未知代码保真、条数上限与空态、等待客户 vs 需事务所处理计数、契约校验。
- `src/FirmInbox.tsx`：新增「月度交付待办」区，`/delivery/followups?limit=8`；**读取失败只降级为一行提示，不遮挡任务列表**（错误状态与列表分离），并显示“其中 N 个等待客户”。行内展示：企业、期间、缺什么、下一步+负责人。
- `src/firm-inbox.css`：行布局 4 列、720px 以下单列；`aria-live` 计数、`aria-labelledby` 区域标题。

## 二、验收证据

### 后端（SQLite，全虚构数据）

`../../.venv/Scripts/python.exe -m pytest nexus_backend/tests/test_delivery_followups.py -q` → **12 passed**（55.65s），覆盖：

1. 未授权时 `status` 收件人 `missing`、队列 409；
2. 配置收件人 → 状态 `valid/source=grant`、可入队；
3. 收件人非企业成员 → 422（配置时）/ `invalid`（状态时）；
4. 期间未关账 → 409 `期间尚未关闭`；
5. 未生成包 → 409 `尚未生成月度交付包`；
6. 重复入队同授权去重；撤回后入队 409（需重新配置）；
7. 客户已签收 → 入队 409；
8. 联系人失效 → 队列 409，恢复后解除；
9. 客户换人后旧包签收人被拒（旧包绑定旧联系人）；
10. 审计记录 `delivery.grant.configure` / 通知行落库；
11. 任务状态只允许 `pending → sent → acknowledged` 等合法迁移；
12. 事务所级 feed 只列需处理月份，`next_step/owner` 正确。

### 前端

- `npx vitest run` → **99 文件 / 991 例全绿**（2.36s）。
- `npx tsc -p tsconfig.app.json --noEmit` → 0 错；`eslint` 与 `prettier --check` 对改动文件全绿。

### 端到端（真实浏览器 + 真实后端，dev 模式）

`E2E_MODE=dev npx playwright test e2e/q04-client-signing.spec.ts` → **2 passed（桌面+手机，19.3s）**。新增断言（`e2e/q04-client-signing.spec.ts`）：

- 冻结包后，事务所 feed 必须出现该账套该期间，`package_id` 一致、`reasons` 含 `signature_missing`、`owner=firm_manager`；
- 客户签收后，同一 feed **不再列出该月份**（证明“已签收即结案”而非仅改标志位）。

## 三、发现的问题（含未修）

1. **既有红灯（非本批引入）**：`nexus_backend/tests/test_stage9_delivery_api.py` 3 例失败，5.3s 内即可复现，且 **HEAD `32e3042d` 上同样失败**。根因是用例让同一账号既制单又审核，被 `162_review_responsibility` 的独立复核策略拒绝（403）。属测试债，需按“财务制单、会计复核”改写用例，或提供显式单人模式开关。**未在本批修**，避免与 P02 的复核语义改动冲突。
2. **提醒没有真实触达渠道**：`delivery_notifications` 只落任务与状态，本地通道为 `DisabledChannel`；外发需在 E02 接真实渠道，当前符合“不外发消息”的边界。
3. **`followups` 只做事务所维度**，未提供“按客户/按负责人”分组；`limit` 上限 200，超出靠分页（后续批）。
4. **交付待办区未做浏览器级断言**：`FirmInbox` 的入口依赖事务所角色与会话 persona，本轮以 API 契约 + 单元测试覆盖，UI 只做了构建/类型/样式门禁；浏览器断言留给 E01 的走查清单。

## 四、结论

P01 的后端与前端**已形成闭环**：授权（谁签）→ 提醒（催谁）→ 状态（缺什么）→ 事务所待办（下一步归谁），且“签收后不再打扰”“撤回后必须重新授权”“非成员不能收件”三条约束都有测试与 DB 守卫。可以进入下一批（P02）；上述 1、4 两项作为遗留项跟踪。

## 五、复现命令

```bash
cd tmp_test/qr01-integration
../../.venv/Scripts/python.exe -m pytest nexus_backend/tests/test_delivery_followups.py -q
npx vitest run src/delivery-followups.test.ts
npx tsc -p tsconfig.app.json --noEmit
E2E_MODE=dev npx playwright test e2e/q04-client-signing.spec.ts --reporter=line
```
