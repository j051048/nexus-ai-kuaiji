# 后端全量基线（RWK01-B）

- 状态：完成（基线，不修复）
- 解释器：`.venv/Scripts/python.exe`（Python 3.14.1，Starlette 1.8.0 / FastAPI 0.133.1）
- 命令：`.venv/Scripts/python.exe -m pytest nexus_backend/tests -q -p no:cacheprovider --ignore-glob="*_postgres.py"`
- 原始输出：`tmp_test/rework/RWK01-B/20261120-locked/full.txt`
- 结果：**5013 passed, 64 failed, 110 skipped, 3 warnings in 5502.24s (1:31:42)**
- 失败清单共 64 行，与输出中的 `FAILED` 数一致。

| # | 用例 | 错误首行 | 初步归类 |
| --- | --- | --- | --- |
| 1 | `nexus_backend/tests/test_admin_derived_permissions.py::test_explicit_company_finance_manager_can_create_period_without_granting_existing_books[sqlite]` | app.accounting.domain.AccountingError: 正式复核仅可授予事务所复核人员；客户财务请使用经办或只读 | 旧测试与新控制冲突 |
| 2 | `nexus_backend/tests/test_admin_derived_permissions.py::test_company_admin_revocation_clears_derived_grants_only_in_its_company[sqlite-delete]` | app.accounting.domain.AccountingError: 正式复核仅可授予事务所复核人员；客户财务请使用经办或只读 | 旧测试与新控制冲突 |
| 3 | `nexus_backend/tests/test_admin_derived_permissions.py::test_company_admin_revocation_clears_derived_grants_only_in_its_company[sqlite-downgrade]` | app.accounting.domain.AccountingError: 正式复核仅可授予事务所复核人员；客户财务请使用经办或只读 | 旧测试与新控制冲突 |
| 4 | `nexus_backend/tests/test_admin_derived_permissions.py::test_company_admin_revocation_clears_derived_grants_only_in_its_company[sqlite-native_delete]` | app.accounting.domain.AccountingError: 正式复核仅可授予事务所复核人员；客户财务请使用经办或只读 | 旧测试与新控制冲突 |
| 5 | `nexus_backend/tests/test_admin_derived_permissions.py::test_company_admin_revocation_clears_derived_grants_only_in_its_company[sqlite-native_downgrade]` | app.accounting.domain.AccountingError: 正式复核仅可授予事务所复核人员；客户财务请使用经办或只读 | 旧测试与新控制冲突 |
| 6 | `nexus_backend/tests/test_company_write_scope_wave2.py::test_journal_import_review_export_are_company_scoped` | app.accounting.domain.AccountingError: 制单责任人不能独立复核本人待审版本，请交另一位有权限的会计 | 旧测试与新控制冲突 |
| 7 | `nexus_backend/tests/test_demo_ai_scenario_workflows.py::test_actual_ocr_vendor_and_model_are_audited_and_other_scene_changes_keep_reuse` | AssertionError: assert (None == 'ocr-only.example.test') | 测试夹具/断言 |
| 8 | `nexus_backend/tests/test_finance_continuation.py::test_reversed_asset_history_requires_manual_reconciliation` | app.accounting.domain.AccountingError: 冲销分录必须与原凭证逐行相反 | 旧测试与新控制冲突 |
| 9 | `nexus_backend/tests/test_finance_refinement.py::test_reconciliation_preview_and_draft_preserve_history` | app.accounting.domain.AccountingError: 冲销分录必须与原凭证逐行相反 | 旧测试与新控制冲突 |
| 10 | `nexus_backend/tests/test_finance_refinement.py::test_reconciliation_native_forged_source_is_rejected` | app.accounting.domain.AccountingError: 冲销分录必须与原凭证逐行相反 | 旧测试与新控制冲突 |
| 11 | `nexus_backend/tests/test_finance_refinement.py::test_workspace_uses_corrected_net_not_original_gross` | app.accounting.domain.AccountingError: 冲销分录必须与原凭证逐行相反 | 旧测试与新控制冲突 |
| 12 | `nexus_backend/tests/test_imp_http_capacity.py::test_capacity_passes_explicit_major_to_identity_fixture[16]` | ValueError: Require a fresh local output directory | 环境 |
| 13 | `nexus_backend/tests/test_imp_http_capacity.py::test_capacity_passes_explicit_major_to_identity_fixture[17]` | ValueError: Require a fresh local output directory | 环境 |
| 14 | `nexus_backend/tests/test_intelligence_ledger.py::test_ledger_linked_bank_export_has_independent_defense_after_external_status_change[sqlite]` | sqlite3.IntegrityError: Current independent reviewer and policy required | 待核（可能产品缺陷） |
| 15 | `nexus_backend/tests/test_intelligence_ledger.py::test_ledger_unlink_keeps_original_and_restores_independent_bank_workflow[sqlite]` | app.accounting.domain.AccountingError: 制单责任人不能独立复核本人待审版本，请交另一位有权限的会计 | 旧测试与新控制冲突 |
| 16 | `nexus_backend/tests/test_intelligence_workflow.py::test_http_end_to_end_synthetic_ocr_apply_review_export_and_private_original` | AssertionError: {"detail":"正式复核仅可授予事务所复核人员；客户财务请使用经办或只读"} | 旧测试与新控制冲突 |
| 17 | `nexus_backend/tests/test_intelligence_workflow.py::test_http_same_customer_export_receipt_reaches_close_gate` | AssertionError: {"detail":"正式复核仅可授予事务所复核人员；客户财务请使用经办或只读"} | 旧测试与新控制冲突 |
| 18 | `nexus_backend/tests/test_migration_structure.py::test_new_migrations_declare_compatibility_header` | AssertionError: sqlite 141_transport_relations.sql 缺少 compatibility 头 | 测试夹具/断言 |
| 19 | `nexus_backend/tests/test_migration_structure.py::test_new_tables_carry_rls_and_write_gate` | AssertionError: ['ai_route_decisions: missing_write_gate (按 company_id 隔离但没有公司写入围栏，写入不会被串行化)', 'bridge_export_artifacts: missing_write...y_id 隔离但没有公司写入围栏，写入不会被串行化)', 'bridge_return_row_handlers: missing_write_gate (按 company_id 隔离但没有公司写入围栏， | 测试夹具/断言 |
| 20 | `nexus_backend/tests/test_migration_structure.py::test_scanner_recognizes_dynamic_and_direct_gates` | AssertionError: assert not True | 测试夹具/断言 |
| 21 | `nexus_backend/tests/test_next_p0_browser_contract.py::test_default_collection_needs_no_private_fixture_and_pg_suite_remains_required` | AssertionError: (node:32268) [DEP0205] DeprecationWarning: `module.register()` is deprecated. Use `module.registerHooks()` instead. | 测试夹具/断言 |
| 22 | `nexus_backend/tests/test_p0_f10_manifest_routes.py::test_six_runtime_consumers_are_policy3_compatible[intake_attachment]` | AssertionError: assert {'intake_atta...est_id'), ...} == {'intake_atta...est_id'), ...} | 测试夹具/断言 |
| 23 | `nexus_backend/tests/test_p0_f10_manifest_routes.py::test_six_runtime_consumers_are_policy3_compatible[event_attachment]` | AssertionError: assert {'intake_atta...est_id'), ...} == {'intake_atta...est_id'), ...} | 测试夹具/断言 |
| 24 | `nexus_backend/tests/test_p0_f10_manifest_routes.py::test_six_runtime_consumers_are_policy3_compatible[case_attachment]` | AssertionError: assert {'intake_atta...est_id'), ...} == {'intake_atta...est_id'), ...} | 测试夹具/断言 |
| 25 | `nexus_backend/tests/test_p0_f10_manifest_routes.py::test_six_runtime_consumers_are_policy3_compatible[material_attachment]` | AssertionError: assert {'intake_atta...est_id'), ...} == {'intake_atta...est_id'), ...} | 测试夹具/断言 |
| 26 | `nexus_backend/tests/test_p0_f10_manifest_routes.py::test_six_runtime_consumers_are_policy3_compatible[bank_import]` | AssertionError: assert {'intake_atta...est_id'), ...} == {'intake_atta...est_id'), ...} | 测试夹具/断言 |
| 27 | `nexus_backend/tests/test_p0_f10_manifest_routes.py::test_six_runtime_consumers_are_policy3_compatible[invoice_import]` | AssertionError: assert {'intake_atta...est_id'), ...} == {'intake_atta...est_id'), ...} | 测试夹具/断言 |
| 28 | `nexus_backend/tests/test_p0_f10_manifest_routes.py::test_six_runtime_consumers_are_policy3_compatible[source_original]` | AssertionError: assert {'intake_atta...est_id'), ...} == {'intake_atta...est_id'), ...} | 测试夹具/断言 |
| 29 | `nexus_backend/tests/test_qa_http_failures.py::test_unexpected_failure_is_controlled_and_has_trace_security_headers` | assert 403 == 500 | 测试夹具/断言 |
| 30 | `nexus_backend/tests/test_qs01_migration_integrity.py::test_repository_structure_has_no_unregistered_gaps` | AssertionError: ['ai_route_decisions: missing_write_gate (按 company_id 隔离但没有公司写入围栏，写入不会被串行化)', 'bridge_export_artifacts: missing_write...y_id 隔离但没有公司写入围栏，写入不会被串行化)', 'bridge_return_row_handlers: missing_write_gate (按 company_id 隔离但没有公司写入围栏， | 测试夹具/断言 |
| 31 | `nexus_backend/tests/test_queue_workflow.py::test_http_batch_worker_exception_resolution_proposal_review_and_export` | AssertionError: {"detail":"正式复核仅可授予事务所复核人员；客户财务请使用经办或只读"} | 旧测试与新控制冲突 |
| 32 | `nexus_backend/tests/test_stage10_backup_recovery.py::test_original_registry_covers_every_storage_key_table` | AssertionError: assert {'bank_import...chments', ...} == {'bank_import...chments', ...} | 测试夹具/断言 |
| 33 | `nexus_backend/tests/test_stage10_dispositions.py::test_approved_duplicate_cannot_be_hidden_and_current_source_truth_required` | sqlite3.IntegrityError: Current independent reviewer and policy required | 待核（可能产品缺陷） |
| 34 | `nexus_backend/tests/test_stage10_fact_revisions.py::test_pending_revision_blocks_native_approval_and_stale_proposal_can_be_rejected` | AssertionError: Regex pattern did not match. | 测试夹具/断言 |
| 35 | `nexus_backend/tests/test_stage10_fact_revisions.py::test_approved_draft_produces_adjustment_without_changing_frozen_facts` | sqlite3.IntegrityError: Current independent reviewer and policy required | 待核（可能产品缺陷） |
| 36 | `nexus_backend/tests/test_stage10_integrations_api.py::test_bridge_http_package_frozen_after_review_and_native_send_disabled` | AssertionError: assert 403 == 200 | 测试夹具/断言 |
| 37 | `nexus_backend/tests/test_stage10_ledgers.py::test_cross_period_formula_is_candidate_only_and_requires_target_samples` | app.accounting.domain.AccountingError: 制单责任人不能独立复核本人待审版本，请交另一位有权限的会计 | 旧测试与新控制冲突 |
| 38 | `nexus_backend/tests/test_stage11_queue_automation.py::test_budget_does_not_block_published_rule_on_verified_ocr` | AssertionError: assert (True and 'needs_attention' == 'succeeded' | 测试夹具/断言 |
| 39 | `nexus_backend/tests/test_stage11_queue_automation.py::test_case_created_after_enqueue_is_classified_before_send_when_opted_in` | AssertionError: assert (False) | 测试夹具/断言 |
| 40 | `nexus_backend/tests/test_stage12_import_wizard_api.py::test_http_chinese_upload_mapping_preview_confirm_and_original_download` | AssertionError: assert '****1234' == '工行1234' | 测试夹具/断言 |
| 41 | `nexus_backend/tests/test_stage12_workspace_api.py::test_http_workspace_department_approval_moves_task_without_self_approval` | AssertionError: assert 410 == 404 | 测试夹具/断言 |
| 42 | `nexus_backend/tests/test_stage12_workspace_api.py::test_http_workspace_finished_task_returns_404_and_does_not_open_another_case` | AssertionError: assert 410 == 404 | 测试夹具/断言 |
| 43 | `nexus_backend/tests/test_stage13_legacy_capture_scope.py::test_standalone_learning_without_optional_injection_skips_clearance_capture` | app.accounting.domain.AccountingError: 制单责任人不能独立复核本人待审版本，请交另一位有权限的会计 | 旧测试与新控制冲突 |
| 44 | `nexus_backend/tests/test_stage13_legacy_capture_scope.py::test_sqlite_raw_sample_insert_cannot_bypass_optional_capture_hook` | app.accounting.domain.AccountingError: 制单责任人不能独立复核本人待审版本，请交另一位有权限的会计 | 旧测试与新控制冲突 |
| 45 | `nexus_backend/tests/test_stage13_legacy_capture_scope.py::test_sqlite_forward_guards_cover_version_evaluation_and_publication` | app.accounting.domain.AccountingError: 制单责任人不能独立复核本人待审版本，请交另一位有权限的会计 | 旧测试与新控制冲突 |
| 46 | `nexus_backend/tests/test_stage16_structured_invoice.py::test_xml_sqlite_upgrade_preserves_existing_originals_runs_and_foreign_keys` | RuntimeError: SQLite 包含当前代码未知的迁移版本，请先更新代码 | 待核（可能产品缺陷） |
| 47 | `nexus_backend/tests/test_stage16_submission.py::test_direct_submit_without_department_receive_review_export_and_idempotence` | app.accounting.domain.AccountingError: 制单责任人不能独立复核本人待审版本，请交另一位有权限的会计 | 旧测试与新控制冲突 |
| 48 | `nexus_backend/tests/test_stage20_home.py::test_profile_matches_domain_grants_without_loading_accounting_facts` | sqlite3.IntegrityError: Formal reviewer must be authorized firm staff | 待核（可能产品缺陷） |
| 49 | `nexus_backend/tests/test_stage25_assistant_efficiency.py::test_snapshot_reuse_preserves_all_evidence_and_facts_with_fewer_roundtrips` | assert 61 <= 55 | 测试夹具/断言 |
| 50 | `nexus_backend/tests/test_stage7_manual_migration.py::test_pre_stage7_manual_edit_is_backfilled_and_cannot_be_cleared` | RuntimeError: SQLite 包含当前代码未知的迁移版本，请先更新代码 | 待核（可能产品缺陷） |
| 51 | `nexus_backend/tests/test_stage7_pagination.py::test_missing_auxiliary_enters_exception_page_and_export_keeps_formula_safe_values` | app.accounting.domain.AccountingError: 制单责任人不能独立复核本人待审版本，请交另一位有权限的会计 | 旧测试与新控制冲突 |
| 52 | `nexus_backend/tests/test_stage8_workflow_integration.py::test_open_case_blocks_review_and_export_after_ocr_then_close_restores_review_gate` | AssertionError: assert ('draft' == 'draft' | 测试夹具/断言 |
| 53 | `nexus_backend/tests/test_stage9_delivery_api.py::test_http_frozen_manifest_exact_receipt_and_no_fake_sync` | AssertionError: assert 403 == 200 | 测试夹具/断言 |
| 54 | `nexus_backend/tests/test_stage9_delivery_api.py::test_http_receipt_history_isolated_unknown_and_closed_period_append` | AssertionError: assert 403 == 200 | 测试夹具/断言 |
| 55 | `nexus_backend/tests/test_stage9_delivery_api.py::test_http_body_limits_and_forged_verified_state_rejected` | AssertionError: assert 403 == 200 | 测试夹具/断言 |
| 56 | `nexus_backend/tests/test_tax_f_recovery.py::test_lease_basis_and_mapping_survive_full_restore` | assert 1 == 0 | 测试夹具/断言 |
| 57 | `nexus_backend/tests/test_taxi_ad_acceptance_repair.py::test_frozen_adapter_cannot_present_stale_fee_as_current` | app.accounting.domain.AccountingError: 已审核凭证使用的科目或辅助项缺少外部映射，请补齐后重新准备 | 待核（可能产品缺陷） |
| 58 | `nexus_backend/tests/test_taxi_ad_backend_continuity.py::test_fee_draft_carries_real_driver_and_vehicle_auxiliaries` | AssertionError: assert {'counterparty': '驾驶员结算主体'} == {'counterpart...13fd31fe596c'} | 测试夹具/断言 |
| 59 | `nexus_backend/tests/test_taxi_ad_recovery.py::test_populated_taxi_source_review_export_unknown_and_hold_survive_restore` | app.accounting.domain.AccountingError: 同一运输主体的有效关系重叠，请先结束旧关系 | 待核（可能产品缺陷） |
| 60 | `nexus_backend/tests/test_taxi_source_input_limits.py::test_malformed_rows_cannot_bypass_source_row_limit` | Failed: DID NOT RAISE AccountingError | 待核（可能产品缺陷） |
| 61 | `nexus_backend/tests/test_taxi_source_input_limits.py::test_boolean_cannot_be_used_as_source_version` | Failed: DID NOT RAISE AccountingError | 待核（可能产品缺陷） |
| 62 | `nexus_backend/tests/test_upload_reference_binding.py::test_migration_preserves_legacy_reference_without_fabricating_verified_binding` | sqlite3.OperationalError: no such column: binding_json | 待核（可能产品缺陷） |
| 63 | `nexus_backend/tests/test_workspace_projection.py::test_projection_matches_live_for_export_and_delivery` | app.accounting.domain.AccountingError: 制单责任人不能独立复核本人待审版本，请交另一位有权限的会计 | 旧测试与新控制冲突 |
| 64 | `nexus_backend/tests/test_write_scope_baseline.py::test_new_unscoped_write_transaction_is_outside_baseline` | AssertionError: New write transaction must declare write_scope. New entries: app/accounting/admin_service.py::reveal_sensitive, app/accounting/review_delegations.py::approve, app/accounting/review_delegations.py::create, app/accounting/revi | 测试夹具/断言 |
