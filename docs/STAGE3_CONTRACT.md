# 第三阶段业务事项接口与领域契约

本阶段面向 2–3 家真实企业进行流程验证，固定人民币、员工申请、部门审批、企业财务审批、移交凭证草稿、会计独立复核与导出。金额全部传十进制字符串，存储和计算使用整数分。

## 请求结构

创建 `BusinessEventCreate`：

```json
{
  "company_id": "授权企业 ID",
  "department_id": "部门 ID，可省略后采用当前成员部门",
  "template_id": "reimbursement",
  "template_version": 1,
  "idempotency_key": "同一申请创建重试使用同一键，可省略",
  "payload": {
    "occurred_on": "2026-10-03",
    "description": "客户演示办公耗材",
    "counterparty": "耗材供应商",
    "amount": "120.30",
    "direction": "expense",
    "payment_status": "unpaid",
    "category": "office",
    "purpose": "客户演示用",
    "project": "演示项目"
  }
}
```

申请人始终取已验证的当前账号，不接受 `applicant_id`。企业、部门、模板、模板版本、申请人创建后固定；修改 `BusinessEventUpdate` 仅接受 `expected_version` 与完整 `payload`。所有请求拒绝额外字段。

创建可传 8–120 字的 `idempotency_key`，在同事务所、同申请人下去重。相同键和相同规范化初次请求即使草稿已修改，也返回当前事项；相同键不同初次内容拒绝。初次请求指纹与创建键不可改，重试仍验证当前企业成员身份；前端的同一次申请在网络失败后复用同一键。

`occurred_on` 为 2000–2099 年合法 ISO 日期；金额为正数，最多 12 位整数、2 位小数，禁止浮点数和指数表示。摘要 1–500 字、对方 1–200 字、用途最多 1000 字、项目最多 200 字。类别固定为 `office/travel/rent/communication/other`。

| 模板 | 标识 | 约束 |
| --- | --- | --- |
| 员工报销 | `reimbursement` | `direction=expense`、`payment_status=unpaid`，表示企业尚未向员工报销 |
| 采购 / 费用 | `procurement_expense` | `direction=expense`，企业可已付款或待付款 |
| 日常收支 | `daily_cashflow` | `payment_status=paid`，已实际收款或付款，可收入或支出 |

审批 / 提交 `VersionRequest` 只接受 `expected_version`；退回 `ReturnRequest` 另需非空 `reason`；移交 `HandoffRequest` 另需 `book_id`、8–120 字的 `idempotency_key`。版本必须为严格正整数，拒绝布尔值、字符串和浮点数。

## 服务入口与响应

- `context(actor)`：`companies` 内含 `id/name/role/department_id/departments[{id,name}]/approver_department_ids/can_submit/can_finance/can_manage_approvers`；同时提供三个模板与 `scopes:{own,department,company,accounting}`。
- `list_events(actor,scope='own',company_id=None,status=None,limit=50,offset=0)`：返回 `{events,total}`，`limit` 为 1–100；未授权的部门、企业、会计范围即使暂无事项也拒绝。
- `get_event(actor,event_id)`：完整事项、`company_name/department_name/applicant_name` 可读名称、当前 `payload`、`attachments`、提交 `versions`、审批 `actions`、可见的 `mapping` 与 `permissions`。申请人名称只在事项可见范围返回，未扩大账号邮箱等身份资料的读取权限。
- `create/update/submit/approve_department/approve_finance/return_event`：返回更新后的完整事项。
- `handoff(actor,event_id,request)`：返回 `{event,book_id,source_id,draft_id}`。
- `require_attachment_upload(actor,event_id,expected_version)`：磁盘写入前验证；`add_attachment(actor,event_id,expected_version,metadata,conn=None)`：再次在写事务内验证并登记附件、事项版本加一。
- `download_metadata(actor,attachment_id)`：供内部授权下载入口使用，可含私有 `storage_key`。公开事项响应和附件快照均不含存储路径。
- `list_department_approvers(actor,company_id,department_id)`：返回 `{approvers:[{user_id,name,email}]}`；`grant_department_approver/revoke_department_approver(actor,company_id,department_id,user_id)`：实时管理显式部门授权。

事项响应 `permissions` 为 `edit/submit/department_approve/finance_approve/return/handoff` 的布尔值；它只供界面展示，服务端每次请求重新计算。`actions` 包含 `id/submitted_version/stage/decision/reason/created_by/created_at`；`versions` 包含提交版本号、完整业务字段、模板快照、附件元数据和 SHA-256 快照。SHA-256 用于追溯已提交内容，不表示真实单据已认证。

申请人和部门主管可看到移交状态；会计来源与凭证 ID 映射仅对企业财务或授权账套读取者返回，员工不因此获得账本权限。

## 权限和审批状态

```text
draft / returned --申请人提交--> submitted
submitted --显式部门审批人批准--> department_approved
department_approved --企业 finance 批准--> finance_approved
finance_approved --企业 finance + 目标账套编辑权移交--> handed_off
submitted / department_approved --当前阶段审批人退回--> returned
```

本人范围要求当前企业成员身份；部门范围要求当前事务所下的显式 `department_approvers` 授权；企业范围仅企业 `finance`；会计范围仅授权账套已移交的业务资料。事务所所有者、管理员以及企业所有者、管理员可配置审批职责，但不会自动获得读取他人业务或审批权限。相同账号可以担任不同审批职责，任何审批阶段均禁止申请人审批本人事项。

员工只能以当前成员部门创建事项，企业管理员与财务可选择本企业有效部门，申请人仍为当前账号。无部门事项可保存草稿，必须先设置成员部门、重新创建后才能提交。提交前必须至少有一个实际上传并已登记的附件，且存在非申请人的有效部门审批人和企业财务审批人。

附件只能在本人 `draft/returned` 状态添加，最多 10 个、总计 50 MiB；同事项相同 SHA-256 附件拒绝。提交生成不可修改的业务、模板和附件快照，审批绑定提交版本。退回后修改、补充附件与重新提交会产生新快照，保留全部历史动作。提交后业务字段和附件冻结。

## 移交与会计复核

移交始终检查当前 `finance` 身份、目标账套编辑授权、同事务所同企业、期间开放及发生日期所属期间。一个事项只移交一次且只对应一个账套；同一事务所幂等键重试返回原来源和草稿，内容或目标变化拒绝。重试也重新检查身份和账套授权。已完成的幂等重试允许沿用初次提交版本，避免网络重试造成重复入账。

同一数据库事务原子创建 `source_kind=business` 的来源、凭证草稿、候选分录、业务映射、移交动作和审计，最后事项标记 `handed_off`。任何错误（含审计失败）全部回滚。来源证据引用为 `business:<event_id>:<submitted_version>`。

| 事项 | 候选借方 | 候选贷方 |
| --- | --- | --- |
| 员工报销 | 对应费用类别 | 2241 其他应付款 |
| 待付采购 / 费用 | 对应费用类别 | 2202 应付账款 |
| 已付采购 / 日常支出 | 对应费用类别 | 1002 银行存款 |
| 日常收入 | 1002 银行存款 | 6001 主营业务收入候选 |

类别对应租金 `6602.01`、办公 `6602.02`、交通 `6602.03`、通信 `6602.04`。`other` 类别仅生成可解释的结算一侧，要求会计补齐科目；账套自定义科目不存在或停用时也保留异常供会计处理。收入确认、税额、采购是否形成资产等业务实质由会计独立复核，不凭业务审批直接认定。移交创建的凭证始终为 `draft`，继续沿用既有修改、会计审核和导出流程。
