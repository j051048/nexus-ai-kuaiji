-- compatibility: incremental
-- 企业写锁第一批：员工录入、业务事项、异常补件、资料清单与发票导入的写事务
-- 显式迁移到 company scope（Python 侧同时传入 company_id 与 write_scope）。本迁移
-- 只扩展已评审的表清单：单企业事务写完即释放企业锁，跨企业并行互不阻塞；未评审
-- 的表仍是旧行为——进入企业事务直接报错，绝不放行。
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
   ELSE NULL END;
  IF resolved IS NOT NULL THEN
   IF resolved IS DISTINCT FROM current_setting('app.write_scope_company',true) THEN
    RAISE EXCEPTION 'Write exceeds declared company scope' USING ERRCODE='42501';END IF;
   PERFORM accounting.lock_company_write(current_setting('app.write_scope_firm',true),resolved);
  ELSIF TG_TABLE_NAME='upload_intents' THEN
   -- 上传凭证属于企业与用户的私有上传通道，不含企业账务字段；只核对事务所。
   IF value->>'firm_id' IS DISTINCT FROM current_setting('app.write_scope_firm',true) THEN
    RAISE EXCEPTION 'Write exceeds declared company scope' USING ERRCODE='42501';END IF;
   PERFORM accounting.lock_company_write(current_setting('app.write_scope_firm',true),current_setting('app.write_scope_company',true));
  ELSE
   PERFORM accounting.lock_legacy_write();
  END IF;
 END IF;
 RETURN coalesce(NEW,OLD);END $$;
REVOKE ALL ON FUNCTION accounting.company_write_table_guard() FROM PUBLIC;
