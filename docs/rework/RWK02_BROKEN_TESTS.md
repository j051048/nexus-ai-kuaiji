# RWK02：被新控制打破的旧测试与遗留失败

先读 `RWK00_MASTER.md`。前置：RWK01-A、RWK01-B。

原则：每个失败先回答"**新规则对，还是旧测试对**"，写明依据再动手。三种结论，对应三种处理：

- 新规则是有意的、正确的：更新旧测试的夹具或期望，使其走合规路径。在状态文件写出旧行为、新行为、依据（哪份提示词或哪条业务规则）。
- 新规则误伤了合法场景：改产品，保留旧测试。
- 无法判断，属于业务决定：**不改任何一方**，写入 `docs/rework/DECISIONS_NEEDED.md`，状态记"阻塞"，继续做别的。

不允许批量改期望值。每个用例单独记录。

## RWK02-A  "正式复核仅可授予事务所复核人员"

**现象。** `nexus_backend/tests/test_admin_derived_permissions.py` 5 个用例失败，报错"正式复核仅可授予事务所复核人员；客户财务请使用经办或只读"。涉及：

- `test_explicit_company_finance_manager_can_create_period_without_granting_existing_books[sqlite]`
- `test_company_admin_revocation_clears_derived_grants_only_in_its_company` 的四个参数化变体

**做法。**

1. 找到该规则的引入位置和引入它的提交，读对应的提示词或文档，确认它来自哪条要求。
2. 这是一条**业务规则**：客户单位的财务人员能否被授予账套复核角色。它直接影响五家单位各自的财务能否参与复核。**你不要自行裁定。** 把以下内容写入 `DECISIONS_NEEDED.md`：规则内容、引入来源、受影响的角色与场景、两种选择各自的后果、受影响的测试清单。
3. 在得到决定前：不改规则，不改这 5 个测试。状态记"阻塞：待业务决定"。

## RWK02-B  "制单责任人不能独立复核本人待审版本"

**现象。** `nexus_backend/tests/test_intelligence_ledger.py` 两个用例失败：

- `test_ledger_linked_bank_export_has_independent_defense_after_external_status_change[sqlite]`：应用层报制单人不能复核本人版本。
- `test_ledger_unlink_keeps_original_and_restores_independent_bank_workflow[sqlite]`：数据库报 `Current independent reviewer and policy required`。

`nexus_backend/tests/test_company_write_scope_wave2.py::test_journal_import_review_export_are_company_scoped` 可能同类，一并核实。

**做法。** 制单与复核分离是明确要求的控制。预期结论是更新测试夹具：让复核由另一名有权限的合成用户完成。逐个确认测试原本要验证的东西（联动导出的独立防线、解除关联后的流程）在新夹具下仍被验证，断言不减少。

**验收。** 这些用例通过；每个用例的夹具改动在状态文件单列。

## RWK02-C  "冲销分录必须与原凭证逐行相反"

**现象。** `nexus_backend/tests/test_finance_refinement.py`（`test_reconciliation_preview_and_draft_preserve_history`、`test_reconciliation_native_forged_source_is_rejected`、`test_workspace_uses_corrected_net_not_original_gross`）和 `test_finance_continuation.py::test_reversed_asset_history_requires_manual_reconciliation` 失败。

**做法。**

1. 弄清这些测试中的冲销是如何构造的：是测试手工构造了不完全相反的分录，还是产品的某条更正路径本身生成的冲销就不满足"逐行相反"。
2. 是前者：修测试夹具，用产品的正式冲销入口生成。
3. 是后者：这是产品缺陷，更正路径生成的冲销必须满足规则，修产品。
4. 注意"更正"与"冲销"不同：部分更正（冲销后再录正确凭证）是两张凭证，不应要求更正凭证与原凭证相反。确认规则只作用于冲销凭证本身。

**验收。** 四个用例通过；状态文件写明每个属于哪种情况。

## RWK02-D  税务类遗留失败

**现象。** `docs/fsc_opt/OPT_STATUS.md` 末尾"已知回归"列出的 6 个：

- `test_tax_f_recovery`：`approved_version` 期望 0 实际 1。
- `test_taxi_ad_acceptance_repair`：缺外部映射。
- `test_taxi_ad_backend_continuity`：费用草稿 `counterparty` 写了名称而不是主体标识。
- `test_taxi_ad_recovery`：恢复夹具与关系重叠约束冲突。
- `test_taxi_source_input_limits` 两个：畸形行绕过行数上限；布尔值被当作版本号，均未抛 `AccountingError`。

**做法。** 逐个处理。初步判断供你核实：

- `test_taxi_source_input_limits` 两个像是**输入校验的产品缺陷**：行数上限必须对畸形行同样生效；版本号必须拒绝布尔值（Python 中 `True` 是 `int` 的子类，校验要用 `type(x) is int`）。修产品。
- `counterparty` 写名称而非标识像是产品缺陷，核实通用别名表（OPT07-F）接入后该字段应存什么。
- 其余三个先查清再定。

**验收。** `<python> -m pytest nexus_backend/tests -q -k "tax"` 无失败。

## RWK02-E  基线中的其余失败

**做法。** 按 `docs/rework/BACKEND_BASELINE.md`，处理 A–D 之外的所有失败和报错。验收时已见到的还有：

- `test_ag04_investigation.py::test_a18_concurrent_atomic_saves_leave_loadable_snapshot`：Windows 下临时文件替换报权限错误。确认是原子写入实现在 Windows 上的并发缺陷还是测试环境问题；是前者要修（重试替换或改用进程内锁）。
- `test_ak05_reliability_persistent.py::test_late_other_company_assistant_finishes_before_blocked_ocr`：期望 `succeeded` 实际 `running`，可能是时序敏感。**不通过加长等待解决**；查清是否为真实的调度回归。
- `test_demo_ai_scenario_workflows.py::test_actual_ocr_vendor_and_model_are_audited_and_other_scene_changes_keep_reuse`：期望的供应商记录为 `None`。
- `test_imp_http_capacity.py` 两个、`test_dcp09_jit_scope.py`、`test_b04_api.py` 等：依赖对齐后先复跑，仍失败再查。

每个失败单独一条状态记录。

**验收。**

```
<python> -m pytest nexus_backend/tests -q -p no:cacheprovider --ignore-glob="*_postgres.py"
```

完整跑完。结果只允许两类非通过项：`DECISIONS_NEEDED.md` 中登记的待决定项，以及无 PG 环境的跳过。其余必须为零。把最后的汇总行原样贴入状态文件。
