-- Projection maintenance: dedicated company lock plus company-guard branch.
CREATE OR REPLACE FUNCTION accounting.begin_projection_company_write(
 target_firm TEXT,target_company TEXT
) RETURNS void LANGUAGE plpgsql SECURITY DEFINER
 SET search_path=pg_catalog,accounting,pg_temp AS $$ BEGIN
 IF NOT pg_has_role(session_user,'accounting_projection','MEMBER') THEN
  RAISE EXCEPTION 'Projection identity required' USING ERRCODE='42501';
 END IF;
 IF target_firm IS NULL OR target_company IS NULL THEN
  RAISE EXCEPTION 'Projection company scope required' USING ERRCODE='42501';
 END IF;
 IF coalesce(current_setting('app.write_scope_company',true),'')<>''
 AND (target_firm IS DISTINCT FROM current_setting('app.write_scope_firm',true)
  OR target_company IS DISTINCT FROM current_setting('app.write_scope_company',true))
 THEN RAISE EXCEPTION 'Company write scope is immutable within a transaction' USING ERRCODE='42501';END IF;
 PERFORM set_config('app.write_scope_firm',target_firm,true);
 PERFORM set_config('app.write_scope_company',target_company,true);
 PERFORM accounting.lock_company_write(target_firm,target_company);
END $$;
REVOKE ALL ON FUNCTION accounting.begin_projection_company_write(TEXT,TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION accounting.begin_projection_company_write(TEXT,TEXT)
 TO accounting_projection;

CREATE OR REPLACE FUNCTION accounting.company_write_table_guard() RETURNS TRIGGER LANGUAGE plpgsql
 SET search_path=pg_catalog,accounting,pg_temp AS $$ DECLARE value JSONB;BEGIN
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
 ELSIF TG_TABLE_NAME=ANY(ARRAY['ai_runs','ai_findings','document_extractions','ocr_jobs','ocr_attempts','model_costs','ops_exception_snapshots','ops_company_dirty','ops_company_refresh','processing_job_metrics','workspace_projection_state','workspace_task_projections']) THEN
  PERFORM accounting.lock_company_write(value->>'firm_id',value->>'company_id');
 ELSE PERFORM accounting.lock_legacy_write();
 END IF;
 RETURN coalesce(NEW,OLD);END $$;

GRANT SELECT ON accounting.schema_migrations TO accounting_projection;
DROP POLICY IF EXISTS schema_migrations_projection_read ON accounting.schema_migrations;
CREATE POLICY schema_migrations_projection_read ON accounting.schema_migrations
 FOR SELECT TO accounting_projection USING(true);
