-- compatibility: incremental
-- Restore the complete 115 write-domain guard after 148-150 replaced its
-- generic company/book/case resolution. Existing migrations remain unchanged.
CREATE OR REPLACE FUNCTION accounting.company_write_table_guard() RETURNS TRIGGER LANGUAGE plpgsql
 SET search_path=pg_catalog,accounting,pg_temp AS $$ DECLARE value JSONB;resolved TEXT;BEGIN
 IF coalesce(current_setting('app.write_scope_company',true),'')='' THEN
  IF coalesce(current_setting('app.write_dispatch',true),'')='1' THEN
   IF TG_TABLE_NAME<>ALL(ARRAY['ocr_jobs','ocr_attempts','ocr_company_schedule','proposal_jobs','proposal_attempts','assistant_turns','assistant_messages','ai_runs','processing_job_metrics','ops_company_dirty','ops_company_refresh'])
   THEN RAISE EXCEPTION 'Write exceeds dispatcher domain' USING ERRCODE='42501';END IF;
  ELSE PERFORM accounting.lock_legacy_write();END IF;
  RETURN coalesce(NEW,OLD);
 END IF;
 value:=CASE WHEN TG_OP='DELETE' THEN to_jsonb(OLD) ELSE to_jsonb(NEW) END;
 -- Preserve the actorless projection restriction introduced in 150, while
 -- retaining all application company/book/case resolution from 115 below.
 IF current_user='accounting_projection' AND TG_TABLE_NAME=ANY(ARRAY[
  'ai_runs','ai_findings','document_extractions','ocr_jobs','ocr_attempts',
  'model_costs','ops_exception_snapshots','ops_company_dirty','ops_company_refresh',
  'processing_job_metrics','workspace_projection_state','workspace_task_projections']) THEN
  IF value->>'firm_id' IS DISTINCT FROM current_setting('app.write_scope_firm',true)
   OR value->>'company_id' IS DISTINCT FROM current_setting('app.write_scope_company',true)
  THEN RAISE EXCEPTION 'Write exceeds projection company scope' USING ERRCODE='42501';END IF;
  PERFORM pg_advisory_xact_lock(accounting.company_lock_key(value->>'firm_id',value->>'company_id'));
  RETURN coalesce(NEW,OLD);
 END IF;

 IF TG_TABLE_NAME='audit_events' THEN
  IF value->>'firm_id' IS DISTINCT FROM current_setting('app.write_scope_firm',true)
   OR ((value->>'book_id') IS NOT NULL AND NOT EXISTS(SELECT 1 FROM accounting.books b WHERE b.id=value->>'book_id' AND b.company_id=current_setting('app.write_scope_company',true)))
   THEN RAISE EXCEPTION 'Audit scope differs from company transaction' USING ERRCODE='42501';END IF;
  resolved:=current_setting('app.write_scope_company',true);
 ELSIF TG_TABLE_NAME=ANY(ARRAY['ai_runs','ai_findings','document_extractions','ocr_jobs','ocr_attempts','model_costs','ops_exception_snapshots','ops_company_dirty','ops_company_refresh','processing_job_metrics']) THEN
  IF value->>'firm_id' IS DISTINCT FROM current_setting('app.write_scope_firm',true)
   OR value->>'company_id' IS DISTINCT FROM current_setting('app.write_scope_company',true)
   THEN RAISE EXCEPTION 'Write exceeds declared company scope' USING ERRCODE='42501';END IF;
  resolved:=value->>'company_id';
 ELSIF (value ? 'company_id') AND nullif(value->>'company_id','') IS NOT NULL THEN
  resolved:=value->>'company_id';
 ELSE
  resolved:=CASE TG_TABLE_NAME
   WHEN 'drafts' THEN (SELECT company_id FROM accounting.books WHERE id=value->>'book_id')
   WHEN 'draft_lines' THEN (SELECT company_id FROM accounting.books WHERE id=value->>'book_id')
   WHEN 'sources' THEN (SELECT company_id FROM accounting.books WHERE id=value->>'book_id')
   WHEN 'source_evidence' THEN (SELECT company_id FROM accounting.books WHERE id=value->>'book_id')
   WHEN 'periods' THEN (SELECT company_id FROM accounting.books WHERE id=value->>'book_id')
   WHEN 'book_access' THEN (SELECT company_id FROM accounting.books WHERE id=value->>'book_id')
   WHEN 'accounts' THEN (SELECT company_id FROM accounting.books WHERE id=value->>'book_id')
   WHEN 'journal_templates' THEN (SELECT company_id FROM accounting.books WHERE id=value->>'book_id')
   WHEN 'journal_template_versions' THEN (SELECT company_id FROM accounting.books WHERE id=value->>'book_id')
   WHEN 'draft_line_auxiliaries' THEN (SELECT company_id FROM accounting.books WHERE id=value->>'book_id')
   WHEN 'exports' THEN (SELECT company_id FROM accounting.books WHERE id=value->>'book_id')
   WHEN 'review_book_receipts' THEN (SELECT company_id FROM accounting.books WHERE id=value->>'book_id')
   WHEN 'closing_reviews' THEN (SELECT company_id FROM accounting.books WHERE id=value->>'book_id')
   WHEN 'closing_receipts' THEN (SELECT company_id FROM accounting.books WHERE id=value->>'book_id')
   WHEN 'case_commands' THEN (SELECT company_id FROM accounting.case_tasks WHERE id=value->>'case_id')
   WHEN 'intake_drafts' THEN value->>'company_id'
   WHEN 'intake_attachments' THEN value->>'company_id'
   WHEN 'intake_runs' THEN value->>'company_id'
   WHEN 'business_events' THEN value->>'company_id'
   WHEN 'business_actions' THEN value->>'company_id'
   WHEN 'business_versions' THEN value->>'company_id'
   WHEN 'business_attachments' THEN value->>'company_id'
   WHEN 'business_mappings' THEN value->>'company_id'
   WHEN 'business_submission_policies' THEN value->>'company_id'
   WHEN 'department_approvers' THEN value->>'company_id'
   WHEN 'case_tasks' THEN value->>'company_id'
   WHEN 'case_responses' THEN value->>'company_id'
   WHEN 'case_history' THEN value->>'company_id'
   WHEN 'case_supersessions' THEN value->>'company_id'
   WHEN 'case_attachments' THEN value->>'company_id'
   WHEN 'material_requests' THEN value->>'company_id'
   WHEN 'material_request_actions' THEN value->>'company_id'
   WHEN 'material_templates' THEN value->>'company_id'
   WHEN 'material_attachments' THEN value->>'company_id'
   WHEN 'service_plan_versions' THEN value->>'company_id'
   WHEN 'invoice_import_batches' THEN value->>'company_id'
   WHEN 'books' THEN value->>'company_id'
   WHEN 'learning_samples' THEN value->>'company_id'
   WHEN 'bank_import_files' THEN value->>'company_id'
   WHEN 'bank_import_previews' THEN value->>'company_id'
   WHEN 'bank_import_confirmations' THEN value->>'company_id'
   WHEN 'bank_import_source_files' THEN value->>'company_id'
   WHEN 'bank_import_profiles' THEN value->>'company_id'
   WHEN 'bank_import_keys' THEN value->>'company_id'
   WHEN 'bank_source_dispositions' THEN value->>'company_id'
   WHEN 'bank_duplicate_decisions' THEN (SELECT company_id FROM accounting.books WHERE id=value->>'book_id')
   WHEN 'workspace_task_projections' THEN value->>'company_id'
   WHEN 'workspace_projection_state' THEN value->>'company_id'
   WHEN 'closing_snapshots' THEN value->>'company_id'
   WHEN 'delivery_manifests' THEN value->>'company_id'
   WHEN 'delivery_receipts' THEN value->>'company_id'
   WHEN 'delivery_receipt_items' THEN value->>'company_id'
   WHEN 'delivery_notes' THEN value->>'company_id'
   WHEN 'settlement_items' THEN value->>'company_id'
   WHEN 'settlement_previews' THEN value->>'company_id'
   WHEN 'settlement_commands' THEN value->>'company_id'
   WHEN 'settlement_allocations' THEN value->>'company_id'
   WHEN 'settlement_reversals' THEN value->>'company_id'
   WHEN 'migration_clearance_commands' THEN value->>'company_id'
   WHEN 'migration_clearance_allocations' THEN value->>'company_id'
   WHEN 'migration_clearance_reversals' THEN value->>'company_id'
   WHEN 'monthly_baselines' THEN value->>'company_id'
   WHEN 'monthly_baseline_confirmations' THEN value->>'company_id'
   WHEN 'monthly_checks' THEN value->>'company_id'
   WHEN 'monthly_check_closures' THEN value->>'company_id'
   WHEN 'recurring_plan_versions' THEN value->>'company_id'
   WHEN 'recurring_plan_approvals' THEN value->>'company_id'
   WHEN 'recurring_generations' THEN value->>'company_id'
   ELSE NULL END;
 END IF;
 IF resolved IS NOT NULL THEN
  IF resolved IS DISTINCT FROM current_setting('app.write_scope_company',true) THEN
   RAISE EXCEPTION 'Write exceeds declared company scope' USING ERRCODE='42501';END IF;
  PERFORM pg_advisory_xact_lock_shared(6420031786281903101);
  PERFORM pg_advisory_xact_lock(accounting.company_lock_key(
   coalesce(nullif(value->>'firm_id',''),current_setting('app.write_scope_firm',true)),resolved));
 ELSIF TG_TABLE_NAME IN('upload_intents','reconciliation_receipts') THEN
  IF value->>'firm_id' IS DISTINCT FROM current_setting('app.write_scope_firm',true) THEN
   RAISE EXCEPTION 'Write exceeds declared company scope' USING ERRCODE='42501';END IF;
  PERFORM accounting.lock_company_write(current_setting('app.write_scope_firm',true),current_setting('app.write_scope_company',true));
 ELSE
  PERFORM accounting.lock_legacy_write();
 END IF;
 RETURN coalesce(NEW,OLD);END $$;
REVOKE ALL ON FUNCTION accounting.company_write_table_guard() FROM PUBLIC;

