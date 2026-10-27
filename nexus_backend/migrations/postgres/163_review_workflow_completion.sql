-- compatibility: breaking
-- Preserve 150 strict firm/company checks for both workspace projection tables
-- on non-projection paths too; retain the full generic resolution restored by 151.
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
 ELSIF TG_TABLE_NAME=ANY(ARRAY['ai_runs','ai_findings','document_extractions','ocr_jobs','ocr_attempts','model_costs','ops_exception_snapshots','ops_company_dirty','ops_company_refresh','processing_job_metrics','workspace_projection_state','workspace_task_projections']) THEN
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
   WHEN 'learning_rule_versions' THEN (SELECT company_id FROM accounting.books WHERE id=value->>'book_id')
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


-- Preserve strict company/dispatcher/projection domains; only the version child receives its actual book scope.
CREATE TABLE accounting.manual_review_withdrawals(request_id TEXT PRIMARY KEY REFERENCES accounting.manual_review_requests(id),book_id TEXT NOT NULL REFERENCES accounting.books(id),request_version INTEGER NOT NULL CHECK(request_version>0),reason TEXT NOT NULL CHECK(length(trim(reason)) BETWEEN 3 AND 1000),withdrawn_by TEXT NOT NULL REFERENCES accounting.users(id),created_at TEXT NOT NULL);
CREATE OR REPLACE FUNCTION accounting.rr_manual_withdrawal_guard() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$ BEGIN
 IF TG_OP<>'INSERT' THEN RAISE EXCEPTION 'Manual withdrawal immutable' USING ERRCODE='23514'; END IF;
 PERFORM accounting.lock_company_write(b.firm_id,b.company_id) FROM accounting.books b WHERE b.id=NEW.book_id;
 IF NOT EXISTS(SELECT 1 FROM accounting.manual_review_requests r JOIN accounting.books b ON b.id=r.book_id WHERE r.id=NEW.request_id AND r.book_id=NEW.book_id AND r.version=NEW.request_version AND r.status='pending' AND r.prepared_by=accounting.actor_user() AND NEW.withdrawn_by=accounting.actor_user() AND accounting.book_allowed(NEW.book_id,true) AND b.firm_id=accounting.actor_firm() AND EXISTS(SELECT 1 FROM accounting.periods pr WHERE pr.book_id=b.id AND pr.status='open') AND NOT EXISTS(SELECT 1 FROM accounting.internal_period_locks lk WHERE lk.book_id=b.id)) THEN RAISE EXCEPTION 'Only current preparer may withdraw pending manual request' USING ERRCODE='23514'; END IF;
 RETURN NEW; END $$;
DROP TRIGGER IF EXISTS rr_manual_withdrawal_guard ON accounting.manual_review_withdrawals;
CREATE TRIGGER rr_manual_withdrawal_guard BEFORE INSERT OR UPDATE OR DELETE ON accounting.manual_review_withdrawals FOR EACH ROW EXECUTE FUNCTION accounting.rr_manual_withdrawal_guard();
DROP TRIGGER IF EXISTS rr_manual_withdrawal_revision ON accounting.manual_review_withdrawals;
CREATE TRIGGER rr_manual_withdrawal_revision AFTER INSERT ON accounting.manual_review_withdrawals FOR EACH ROW EXECUTE FUNCTION accounting.close_dependency_bump('book');
CREATE OR REPLACE FUNCTION accounting.rr_manual_not_withdrawn() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$ BEGIN
 PERFORM accounting.lock_company_write(b.firm_id,b.company_id) FROM accounting.books b WHERE b.id=NEW.book_id;
 IF NEW.status='approved' AND EXISTS(SELECT 1 FROM accounting.manual_review_withdrawals WHERE request_id=NEW.id) THEN RAISE EXCEPTION 'Withdrawn manual request cannot be reviewed' USING ERRCODE='23514'; END IF;
 RETURN NEW; END $$;
DROP TRIGGER IF EXISTS rr_manual_not_withdrawn ON accounting.manual_review_requests;
CREATE TRIGGER rr_manual_not_withdrawn BEFORE UPDATE ON accounting.manual_review_requests FOR EACH ROW EXECUTE FUNCTION accounting.rr_manual_not_withdrawn();
CREATE OR REPLACE FUNCTION accounting.rr_manual_close_guard() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$ BEGIN
 PERFORM accounting.lock_company_write(b.firm_id,b.company_id) FROM accounting.books b WHERE b.id=NEW.book_id;
 IF EXISTS(SELECT 1 FROM accounting.manual_review_requests r WHERE r.book_id=NEW.book_id AND NOT EXISTS(SELECT 1 FROM accounting.manual_review_withdrawals w WHERE w.request_id=r.id) AND NOT EXISTS(SELECT 1 FROM accounting.vouchers v JOIN accounting.voucher_review_completions c ON c.id=v.id WHERE v.review_request_id=r.id)) THEN RAISE EXCEPTION 'Outstanding manual reviews must be posted or withdrawn before closing' USING ERRCODE='23514'; END IF;
 RETURN NEW; END $$;
DROP TRIGGER IF EXISTS rr_manual_period_close_guard ON accounting.periods;
CREATE TRIGGER rr_manual_period_close_guard BEFORE INSERT OR UPDATE ON accounting.periods FOR EACH ROW WHEN(NEW.status='closed') EXECUTE FUNCTION accounting.rr_manual_close_guard();
DROP TRIGGER IF EXISTS rr_manual_internal_close_guard ON accounting.internal_period_locks;
CREATE TRIGGER rr_manual_internal_close_guard BEFORE INSERT ON accounting.internal_period_locks FOR EACH ROW EXECUTE FUNCTION accounting.rr_manual_close_guard();
ALTER TABLE accounting.manual_review_withdrawals ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.manual_review_withdrawals FORCE ROW LEVEL SECURITY;
REVOKE ALL ON accounting.manual_review_withdrawals FROM PUBLIC,accounting_app,accounting_worker,accounting_system;
GRANT SELECT,INSERT ON accounting.manual_review_withdrawals TO accounting_app;
CREATE POLICY rr_read ON accounting.manual_review_withdrawals FOR SELECT TO accounting_app USING(accounting.book_allowed(book_id));
CREATE POLICY rr_insert ON accounting.manual_review_withdrawals FOR INSERT TO accounting_app WITH CHECK(accounting.book_allowed(book_id,true));
REVOKE ALL ON FUNCTION accounting.rr_manual_withdrawal_guard(),accounting.rr_manual_not_withdrawn(),accounting.rr_manual_close_guard() FROM PUBLIC,accounting_app,accounting_worker,accounting_system;
ALTER FUNCTION accounting.closing_fingerprint(TEXT) RENAME TO closing_fingerprint_pre_review_policy;
CREATE OR REPLACE FUNCTION accounting.closing_fingerprint(target_book TEXT) RETURNS TEXT LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$ DECLARE old_hash TEXT; extra JSONB; BEGIN
 old_hash:=accounting.closing_fingerprint_pre_review_policy(target_book);
 SELECT jsonb_build_object('manual_review_requests',(SELECT COALESCE(jsonb_agg(to_jsonb(r) ORDER BY r.id),'[]'::jsonb) FROM accounting.manual_review_requests r WHERE r.book_id=target_book),'manual_review_withdrawals',(SELECT COALESCE(jsonb_agg(to_jsonb(w) ORDER BY w.request_id),'[]'::jsonb) FROM accounting.manual_review_withdrawals w WHERE w.book_id=target_book),'manual_posted_vouchers',(SELECT COALESCE(jsonb_agg(jsonb_build_object('id',v.id,'review_request_id',v.review_request_id,'review_completion_id',v.review_completion_id) ORDER BY v.id),'[]'::jsonb) FROM accounting.vouchers v WHERE v.book_id=target_book AND v.review_request_id IS NOT NULL)) INTO extra;
 RETURN encode(sha256(convert_to(old_hash||extra::text,'UTF8')),'hex'); END $$;
ALTER FUNCTION accounting.closing_issues(TEXT) RENAME TO closing_issues_pre_review_policy;
CREATE OR REPLACE FUNCTION accounting.closing_issues(target_book TEXT) RETURNS TABLE(category TEXT,object_type TEXT,object_id TEXT,detail TEXT) LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$ BEGIN
 IF NOT accounting.ai_book_reviewer(target_book) THEN RAISE EXCEPTION 'Current book reviewer required' USING ERRCODE='42501'; END IF;
 RETURN QUERY SELECT * FROM accounting.closing_issues_pre_review_policy(target_book) UNION ALL SELECT 'manual_review_pending','manual_review_request',r.id,'手工凭证尚未完成审核与正式记账；未审核申请可由制单人明确撤回' FROM accounting.manual_review_requests r WHERE r.book_id=target_book AND NOT EXISTS(SELECT 1 FROM accounting.manual_review_withdrawals w WHERE w.request_id=r.id) AND NOT EXISTS(SELECT 1 FROM accounting.vouchers v JOIN accounting.voucher_review_completions c ON c.id=v.id WHERE v.review_request_id=r.id);
 END $$;
ALTER FUNCTION accounting.closing_report(TEXT) RENAME TO closing_report_pre_review_policy;
CREATE OR REPLACE FUNCTION accounting.closing_report(target_book TEXT) RETURNS JSONB LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 WITH issues AS (SELECT * FROM accounting.closing_issues(target_book)), counts AS (SELECT category,count(*) AS count FROM issues GROUP BY category)
 SELECT jsonb_build_object('engine_version',1,'passed',NOT EXISTS(SELECT 1 FROM issues),'issue_count',(SELECT count(*) FROM issues),'truncated',(SELECT count(*) FROM issues)>500,'counts',COALESCE((SELECT jsonb_object_agg(category,count) FROM counts),'{}'::jsonb),'issues',COALESCE((SELECT jsonb_agg(to_jsonb(selected) ORDER BY selected.category,selected.object_type,selected.object_id) FROM (SELECT * FROM issues ORDER BY category,object_type,object_id LIMIT 500) selected),'[]'::jsonb));
$$;
REVOKE ALL ON FUNCTION accounting.closing_fingerprint_pre_review_policy(TEXT),accounting.closing_issues_pre_review_policy(TEXT),accounting.closing_report_pre_review_policy(TEXT),accounting.closing_fingerprint(TEXT),accounting.closing_issues(TEXT),accounting.closing_report(TEXT) FROM PUBLIC,accounting_app,accounting_worker,accounting_system;
GRANT EXECUTE ON FUNCTION accounting.closing_fingerprint(TEXT),accounting.closing_issues(TEXT),accounting.closing_report(TEXT) TO accounting_app;
