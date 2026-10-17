-- compatibility: incremental
-- 系统身份的企业写事务（仅用于待办投影刷新等已评审的系统任务）：
-- 与普通企业事务一样取共享全局门 + 企业锁，不再抢占全库独占锁；
-- 写门同时补上两张投影表的企业列映射。
CREATE FUNCTION accounting.begin_system_company_write(target_firm TEXT,target_company TEXT) RETURNS VOID
 LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$ BEGIN
 IF target_firm IS NULL OR target_company IS NULL OR NOT EXISTS(
  SELECT 1 FROM accounting.firm_clients fc WHERE fc.firm_id=target_firm
  AND fc.company_id=target_company AND fc.status='active')
 THEN RAISE EXCEPTION 'Unknown company scope' USING ERRCODE='42501';END IF;
 IF coalesce(current_setting('app.write_scope_company',true),'')<>''
 AND (target_firm IS DISTINCT FROM current_setting('app.write_scope_firm',true)
  OR target_company IS DISTINCT FROM current_setting('app.write_scope_company',true))
 THEN RAISE EXCEPTION 'Company write scope is immutable within a transaction' USING ERRCODE='42501';END IF;
 PERFORM set_config('app.write_scope_firm',target_firm,true);
 PERFORM set_config('app.write_scope_company',target_company,true);
 PERFORM accounting.lock_company_write(target_firm,target_company);
END $$;
REVOKE ALL ON FUNCTION accounting.begin_system_company_write(TEXT,TEXT) FROM PUBLIC,accounting_app,accounting_worker;
GRANT EXECUTE ON FUNCTION accounting.begin_system_company_write(TEXT,TEXT) TO accounting_system;

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
 IF TG_TABLE_NAME='audit_events' THEN
  IF value->>'firm_id' IS DISTINCT FROM current_setting('app.write_scope_firm',true)
   OR ((value->>'book_id') IS NOT NULL AND NOT EXISTS(SELECT 1 FROM accounting.books b WHERE b.id=value->>'book_id' AND b.company_id=current_setting('app.write_scope_company',true)))
   THEN RAISE EXCEPTION 'Audit scope differs from company transaction' USING ERRCODE='42501';END IF;
 ELSIF TG_TABLE_NAME=ANY(ARRAY['ai_runs','ai_findings','document_extractions','ocr_jobs','ocr_attempts','model_costs','ops_exception_snapshots','ops_company_dirty','ops_company_refresh','processing_job_metrics']) THEN
  PERFORM accounting.lock_company_write(value->>'firm_id',value->>'company_id');
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
   WHEN 'workspace_task_projections' THEN value->>'company_id'
   WHEN 'workspace_projection_state' THEN value->>'company_id'
   ELSE NULL END;
  IF resolved IS NOT NULL THEN
   IF resolved IS DISTINCT FROM current_setting('app.write_scope_company',true) THEN
    RAISE EXCEPTION 'Write exceeds declared company scope' USING ERRCODE='42501';END IF;
   PERFORM accounting.lock_company_write(current_setting('app.write_scope_firm',true),resolved);
  ELSIF TG_TABLE_NAME IN('upload_intents','reconciliation_receipts') THEN
   -- 事务所与用户级通道（无企业账务字段）；只核对事务所。
   IF value->>'firm_id' IS DISTINCT FROM current_setting('app.write_scope_firm',true) THEN
    RAISE EXCEPTION 'Write exceeds declared company scope' USING ERRCODE='42501';END IF;
   PERFORM accounting.lock_company_write(current_setting('app.write_scope_firm',true),current_setting('app.write_scope_company',true));
  ELSE
   PERFORM accounting.lock_legacy_write();
  END IF;
 END IF;
 RETURN coalesce(NEW,OLD);END $$;
REVOKE ALL ON FUNCTION accounting.company_write_table_guard() FROM PUBLIC;
