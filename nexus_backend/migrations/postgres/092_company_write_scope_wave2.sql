-- compatibility: incremental
-- 企业写锁第二批：凭证复核/导出、科目与模板、银行导入、重复来源隔离与
-- 关联回执域进入 company scope。写门按行核对公司；跨企业批次继续走全局锁。
-- 同时把两个原生确认函数内部的全局锁改为企业锁（未声明企业范围时其行为不变，
-- lock_company_write 会自动回退为旧的全局锁）。
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

-- 原生确认函数在声明了企业范围时按行解析出的公司取企业锁；未声明时保持旧全局锁。
DO $$ DECLARE definition TEXT;BEGIN
 definition:=pg_get_functiondef('accounting.confirm_same_bank_transaction(TEXT,TEXT,TEXT,INTEGER,TEXT,TEXT,TEXT,TEXT,TEXT)'::regprocedure);
 IF position('accounting.lock_legacy_write()' in definition)=0 THEN
  RAISE EXCEPTION 'confirm_same_bank_transaction 缺少预期锁语句，请人工核对' USING ERRCODE='55000';END IF;
 EXECUTE replace(definition,'PERFORM accounting.lock_legacy_write();',
  'PERFORM accounting.lock_company_write((SELECT firm_id FROM accounting.books WHERE id=target_book),(SELECT company_id FROM accounting.books WHERE id=target_book));');
 definition:=pg_get_functiondef('accounting.confirm_reconciliation(TEXT,TEXT,TEXT)'::regprocedure);
 IF position('accounting.lock_legacy_write()' in definition)=0 THEN
  RAISE EXCEPTION 'confirm_reconciliation 缺少预期锁语句，请人工核对' USING ERRCODE='55000';END IF;
 EXECUTE replace(definition,'PERFORM accounting.lock_legacy_write();',
  'PERFORM accounting.lock_company_write((SELECT firm_id FROM accounting.books WHERE id=(target_payload::jsonb->''items''->0->>''book_id'')),(SELECT company_id FROM accounting.books WHERE id=(target_payload::jsonb->''items''->0->>''book_id'')));');
END $$;
