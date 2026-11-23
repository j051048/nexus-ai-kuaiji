# 待业务决定

## D-001 客户单位财务能否被授予账套“正式复核”角色

- 规则现状：`nexus_backend/app/accounting/directory.py:406` 在分配账套 `reviewer` 时要求目标用户必须是当前事务所成员且事务所角色属于复核人员；否则返回 403“正式复核仅可授予事务所复核人员；客户财务请使用经办或只读”。
- 引入来源：提交 `0c06a38c feat(access): 访问范围策略 P0–P2 与账套/部门/复核授权约束`，属于访问范围策略对独立复核的收紧。
- 冲突测试：
  - `test_admin_derived_permissions.py::test_explicit_company_finance_manager_can_create_period_without_granting_existing_books[sqlite]`
  - `test_admin_derived_permissions.py::test_company_admin_revocation_clears_derived_grants_only_in_its_company[...]` 4 个参数化变体。
- 选择一：保留规则。客户财务不能成为账套正式复核人；复核必须由事务所复核人员完成。后果：五家客户财务只能经办/只读，复核产能集中在事务所；现有 5 个测试需改为断言 403 或改用事务所复核人。
- 选择二：允许满足条件（如企业财务负责人、且与制单人分离）的客户财务获得复核角色。后果：客户侧可复核，但必须补企业内独立复核策略、自审禁止与撤权规则；现有 5 个测试保留，另需新增负向用例。
- 当前处理：不改规则、不改这 5 个测试，RWK02-A 记“阻塞：待业务决定”。

## D-002 写事务 scope 归属（UPG Batch 01 / RWK02-E）

- 现象：`test_write_scope_baseline.py` 报告 30 个既有写事务只有 `transaction(write=True)`，未声明 `write_scope`：
  `admin_service.reveal_sensitive`、`review_delegations.{approve,create,expire_due,revoke}`、`review_policy.configure`、`aliases.{add,retire}`、`integrations.{attach_evidence,handle_return_row,record_manual_return,record_return}`、`opening.approve_policy`、`periodic.authority.bind`、`periodic.links.{link,revoke}`、`reports.explain_difference`、`scheduler.{_tick_plan,confirm,create_plan,set_enabled}`、`tax.costs.{activate,confirm,create_draft,deactivate}`、`tax.variances.{assign,handle,record,reverse,start}`。
- 约束：测试规定 baseline 只能缩不能扩；新增登记需要明确 owner 决策。逐个补 `write_scope` 需先决定每个函数是 `global`、`firm` 还是 `company`，并补齐 `company_id`/`actor` 语义；不能仅以测试通过为目标批量填值。
- 需要决定：批准逐函数补 scope（推荐、需要开发工时），或书面批准把这 30 项登记为历史 baseline（会保留未来的全局写风险）。
- 当前处理：未改 `write_scope_baseline.txt`、未批量改产品；Batch01 因此保持“部分完成”。

## D-003 集团合并试算中抵消分录的复核人归属（UPG Batch 10-1.2，待裁决）

- 现状（已实现）：`consolidation_eliminations` 只允许 `reviewed_by <> created_by`，且复核人须具备该法人可见范围的管理或复核权限；`pending` 分录不进入试算底稿。
- 待裁决：复核人是集团财务负责人、各法人财务负责人，还是允许事务所复核人代行？不同选择会改变跨法人复核的职责划分。
- 未裁决期间的保守口径：只有录入人以外、且对该法人有管理/复核权限的人可以复核；不按「集团层级」越权授予。

## D-004 受控转派的候选人范围是否包含有效代班人（UPG Batch 10-1.1，待裁决）

- 现状（已实现）：`HandoffService.candidates` 只按 `book_access` 静态授权与事务所复核角色过滤，`assign` 对无权限者直接 403，不因转派授予权限。
- 待裁决：`book_review_delegations` 当前有效的代班人是否应出现在候选人列表里（他们按代班视图已有复核能力，但不在 `book_access` 里）。
- 未裁决期间的保守口径：不把代班人列为候选人，避免转派后授权到期造成的悬空任务。

## 裁决结果（2026-11-20，用户 m59543）

### D-002：已裁决，逐函数声明 write_scope

- 禁止扩大 `test_write_scope_baseline.py` 的 baseline。
- 24 个企业写事务改为 `write_scope="company"`，并显式传入 `company_id`：
  `admin_service::reveal_sensitive`、`aliases::{add,retire}`、`review_delegations::{create,approve,revoke}`、`integrations::{attach_evidence,handle_return_row,record_manual_return,record_return}`、`opening::approve_policy`、`periodic.authority::bind`、`periodic.links::{link,revoke}`、`reports::explain_difference`、`tax.costs::{create_draft,confirm,activate,deactivate}`、`tax.variances::{record,start,handle,reverse,assign}`。
- 对仅持有 `book_id` 或业务对象 ID 的函数，先用只读事务解析 `company_id`，再进入企业写事务；写事务内继续通过 `access.book`/`_scope`/`_asset` 复验对象归属和状态，保持原有 404/403 契约，避免 TOCTOU。
- 6 个显式 `write_scope="global"`：`review_policy::configure`、`review_delegations::expire_due`、`scheduler::{create_plan,set_enabled,_tick_plan,confirm}`。调度项当前不强制非空 company_id，因此选择保守的全局锁。
- 另补 `tax.relations::verify` 的企业 scope（该调用由并发改动新增，同样按 company 处理）。
- 结果：`test_write_scope_baseline.py` 2 passed。

### D-001：已裁决，保留严格复核规则

- 保留“正式复核仅可授予事务所复核人员”，不放宽产品权限。
- 更新 `test_admin_derived_permissions.py`：
  - 企业财务创建期间：预期 403；
  - 企业管理员撤权：预期其不能给企业财务授予 book reviewer（403），其他企业保留 ledger manager 记录但无 review/manage；
  - 事务所复核人员路径保留 reviewer 授权与撤权验证。
- 结果：`7 passed, 7 skipped`。
