# 待办来源盘点（PLT03-A）

状态：只读盘点，基于当前工作区源码与本地合成数据；不修改产品代码，不代表真人可用性验收。
入口：`src/FirmInbox.tsx` / `src/WorkspaceHome.tsx` 的待办列表，服务端任务投影在
`nexus_backend/app/workspace/service.py`；前端点击统一走
`src/App.tsx::openWorkspaceTask` 和 `src/workspace-types.ts::taskDestination`。

## 盘点口径

- “待办”只指服务端 `workspace` 投影返回的 `WorkspaceTask`，以及 `FirmInbox` 同页的交付跟进 feed。
- “点击后”按当前代码实际落点记录，不按设计意图。
- “携带定位”只统计 URL / 打开目标实际带入的单位、账套、期间、对象、版本；服务端在目标请求中重新查对象不算链接携带。
- 权限判断只以服务端任务 CTE / 目标对象读取为准；前端隐藏按钮不构成授权。
- 统计数量按 `TaskKind` 类型计；交付跟进是独立 feed，不计入 14 类，但单列。

## 待办类型

| 类型 | 来源与生成位置 | 点击后去哪里 | 链接携带的单位/账套/期间/对象/版本 | 谁能办、谁只能看（服务端） | 办完如何消失/更新 |
|---|---|---|---|---|---|
| `intake` 录入草稿 | `workspace.service.py::_query` 的 intake branch；`business_events` 草稿、创建人本人、公司成员关系 | `business` 事件表单，`setBusinessTarget(event_id)` | 单位=任务 `company_id`；对象=`event_id`；账套/期间/版本未带 | 服务端：创建人 + `company_memberships`；部门员工需同部门 | 事件离开 `draft` 后分支不再返回；打开不改变状态 |
| `business` 业务提交 | business branch；申请人自己的 `business_events`，非 `handed_off` | `business` 事件表单，`event_id` | 单位/对象；账套/期间/版本未带 | 服务端：`firm_id` + `applicant_id` + 成员关系 | 状态变为 `handed_off` 或离开条件后消失；打开不改变状态 |
| `approval` 部门审批 | approval branch；`submitted`、部门审批人、非申请人 | `business` 事件表单，`event_id` | 单位/对象；账套/期间/版本未带 | 服务端：`department_approvers` + 同部门成员，且 `applicant_id<>actor` | 离开 `submitted` 后消失；审批由业务服务写事实 |
| `finance` 财务核验 | finance branch；`department_approved`、企业财务、非申请人 | `business` 事件表单，`event_id` | 单位/对象；账套/期间/版本未带 | 服务端：`company_memberships.role='finance'`，且非申请人 | 离开 `department_approved` 后消失；打开不改变状态 |
| `handoff` 会计接手 | handoff branch；direct 的 `submitted`，或 `finance_approved` 的财务 | `business` 事件表单，`event_id` | 单位/对象；账套/期间未带（direct 分支查询期初时内部用期间）；版本未带 | 服务端：direct 分支需授权账套；财务分支需 `finance` 角色 | 离开 `submitted`/`finance_approved` 后消失；打开不改变状态 |
| `case` 异常/补件 | case branch；`cases` 的 open/awaiting_info/response_submitted/in_review，经 case scope | `cases` 工作台，`initialCaseId` | 对象=`case_id`；未设置目标单位/账套/期间/版本 | 服务端：case scope + recipient/collector/manager + 独立响应人 | case 状态离开开放集合后消失；打开、阅读不改变状态 |
| `material` 资料请求 | material branch；`material_requests` pending/returned/submitted，请求人或独立验收人 | `materials`，`setMaterialTarget(request_id)` | 单位=`company_id`；对象=`request_id`；账套可能带 `book_id`；期间/版本未带 | 服务端：assignee 或独立验收人（`review_books`） | 状态离开 pending/returned/submitted 或验收完成后消失；打开不改变状态 |
| `review` 凭证复核 | review branch；`draft`、`review_books`、非申请人 | `InlineReviewDrawer`，`book_id + draft_id` | 账套/对象=`book_id/draft_id`；单位/期间/版本未带；由目标 API 重新读取 | 服务端：review scope；非申请人；独立复核政策在目标抽屉再次校验 | 草稿离开 `draft` 或审核完成后消失；打开、勾选本身不完成 |
| `ocr` 识别任务 | ocr branch；`queued/running/retry_wait/failed/interrupted/stale` 且当前处理人 | `queue`，`queueTarget=batch_id`、`jobId=ref` | 对象=`batch_id/job_id`；未设置目标单位；账套/期间/版本未带 | 服务端：申请人/财务/部门审批人/账套授权 + OCR 可见范围 | 任务离开这些状态，或映射草稿/期间不再开放后消失；打开不改变状态 |
| `proposal` 生成提案 | proposal branch；`waiting/queued/running/retry_wait/needs_attention/failed/interrupted/stale` 且有编辑账套 | `ProposalTaskDialog`，`jobId=ref` | 仅 `job_id`；未设置单位/账套/期间/版本 | 服务端：编辑账套授权 + 任务可见范围 | 任务离开这些状态后消失；打开不改变状态 |
| `export` 导出待办 | export branch；`approved` 且有 `review_books` | `accounting`，设置 `book_id`、`filter=approved`、`listTab=drafts`；**忽略 `ref`** | 账套=`book_id`；未带具体导出对象、单位、期间、版本 | 服务端：`review_books` 复核范围 | 导出记录离开 `approved` 后消失；打开不改变状态 |
| `delivery` 交付 | delivery branch；交付任务且有 `review_books` | `accounting`，设置 `book_id`、`deliveryId=ref` | 账套/对象=`book_id/ref`；未带单位/期间/版本 | 服务端：`review_books` 范围 | 交付状态变化后消失；打开不改变状态 |
| `migration` 旧账迁移 | `legacy_migration/workspace.py::workspace_branch`，经 `gates['migration']` | `accounting`，设置 `book_id`、`migrationId=ref` | 账套/对象=`book_id/ref`；未带单位/期间/版本 | 服务端：迁移可见范围 + 账套编辑范围 | 迁移任务完成/关闭后消失；打开不改变状态 |
| `alert` 运行告警 | alert branch；`open`、管理员、所属企业 active | `operations`，设置 `operationsTarget=company_id`；**忽略告警 `ref`** | 企业=`company_id`；未带具体告警对象、账套、期间、版本 | 服务端：firm/company 管理员 + 企业 active | 告警 `state` 离开 `open` 后消失；打开不改变状态 |
| `delivery followup` 交付跟进（独立 feed） | `FirmInbox` 的 `/delivery/followups`；`DeliveryFollowupPanel` | 同页 `DeliveryFollowupPanel`，按 `book_id + period` 打开 | 账套/期间；未带版本 | 服务端：monthly followup 读取范围；无 `can_act` 字段 | 交付回执/签收状态变化后刷新；打开、查看不改变状态 |

## 统计

- `TaskKind` 共 **14** 类：intake、business、approval、finance、handoff、case、review、ocr、proposal、export、delivery、alert、migration、material。
- **只到模块/账套级，未落到具体对象：2 类** —— `export`（忽略导出 `ref`，只到账套已审核筛选）、`alert`（忽略告警 `ref`，只到企业运行页）。
- **无链接：0 类**（所有类型都有点击处理；缺关键 id 时前端显示“这项待办缺少可办理的原记录”，不会静默落到首页）。
- **链接不带期间的 TaskKind：14 类全部**；`delivery followup` 是唯一带期间的独立 feed。
- **链接不带对象版本的 TaskKind：14 类全部**；`review`/`material`/`delivery` 由目标 API 重新读取当前版本。
- **链接不带单位的 TaskKind：4 类**（`case`、`review`、`ocr`、`proposal` 的前端进入函数未设置目标单位；由目标 API 的账套/case/job 范围兜底）。
- **无 `available_action` 字段：14 类全部**。只有 `bucket`、`action_label`、`responsible` 和 `permissions={assign,remind,claim}`；没有“可办理/仅可查看/无权查看”的当前用户动作枚举。
- **收到即完成风险：未发现待办“打开即写完成”**。`FirmInbox.openTask` 只 GET `/workspace/tasks/{kind}/{ref}`，不写标记；服务端投影由业务事实生成。`firm_task_assignments` 只记录指派，不把任务标成完成。

## 主要缺口（供 PLT03-B/C/D）

1. **目标定位不完整**：`WorkspaceTask` 没有 `period`、`object_version`、`object_type` 字段；前端所有任务链接都不带期间，也不能区分“旧版本”和“当前版本”。
2. **动作语义不足**：没有服务端计算的 `available_action`；`action_label` 只是显示文案，不能证明当前用户可办理。`permissions.claim` 只表示未指派，不等于业务可办。
3. **两类落点不准**：`export` 丢 `ref`、`alert` 丢 `ref`；点击只能到账套筛选/企业运行页，不是具体导出或告警。
4. **四类未显式切单位**：case/review/ocr/proposal 依赖目标对象加载时的服务端范围，没有“已切换到某单位”的可见提示。
5. **返回位置未统一**：`WorkspaceHome` 有 `workspace-progress` 的局部记忆，但各目标页面的返回按钮/多层抽屉未证明都回到原队列的筛选与滚动位置；需在 PLT03-C 用 Playwright 验证。
6. **交付跟进无动作枚举**：`DeliveryFollowupPanel` 只有状态与刷新，没有服务端返回当前用户可做的动作。

## 不计入待办的相邻入口

- `ClientMessages`：客户/会计消息线程，不是任务状态机；不因打开消息自动完成待办。
- `DeliveryNotifications`：发送提醒与 `unknown` 调查，显示在交付页；本身不是 `WorkspaceTask`，不能替代回执/签收。
- `FirmInbox` 的“提醒/指派”只写 `firm_task_assignments` 与通知，不改变业务对象状态。

## 验收命令

```text
rg -n "KINDS =|branch\\(|taskDestination|openWorkspaceTask|delivery/followups" \
  nexus_backend/app/workspace/service.py src/App.tsx src/workspace-types.ts src/FirmInbox.tsx
```

结果确认上述分支与目标映射存在；本文件为只读盘点，未改产品代码。
