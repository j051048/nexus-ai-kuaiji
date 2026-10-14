-- Mixed-version-compatible gate. Old writes keep the original exclusive key;
-- reviewed single-company writes share it, then acquire one stable company key.
-- No submitted evidence, journal, approval or RLS policy is replaced here.
CREATE FUNCTION accounting.lock_legacy_write() RETURNS VOID LANGUAGE plpgsql VOLATILE
 SET search_path=pg_catalog,accounting,pg_temp AS $$ BEGIN
 IF coalesce(current_setting('app.write_scope_company',true),'')<>''
 OR coalesce(current_setting('app.write_dispatch',true),'')='1' THEN
  RAISE EXCEPTION 'Legacy write domain cannot enter a company transaction' USING ERRCODE='55000';
 END IF;
 PERFORM pg_advisory_xact_lock(6420031786281903101);
 END $$;
CREATE FUNCTION accounting.company_lock_key(target_firm TEXT,target_company TEXT) RETURNS BIGINT
 LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS $$
 SELECT ('x'||substr(md5('accounting-company-v1:'||length(target_firm)::text||':'||target_firm||':'||target_company),1,16))::bit(64)::bigint
 $$;
CREATE FUNCTION accounting.lock_company_write(target_firm TEXT,target_company TEXT) RETURNS VOID
 LANGUAGE plpgsql VOLATILE SET search_path=pg_catalog,accounting,pg_temp AS $$ BEGIN
 IF coalesce(current_setting('app.write_scope_company',true),'')='' THEN
  PERFORM accounting.lock_legacy_write();RETURN;
 END IF;
 IF target_firm IS DISTINCT FROM current_setting('app.write_scope_firm',true)
  OR target_company IS DISTINCT FROM current_setting('app.write_scope_company',true) THEN
  RAISE EXCEPTION 'Write exceeds declared company scope' USING ERRCODE='42501';
 END IF;
 PERFORM pg_advisory_xact_lock_shared(6420031786281903101);
 PERFORM pg_advisory_xact_lock(accounting.company_lock_key(target_firm,target_company));
 END $$;
CREATE FUNCTION accounting.begin_company_write(target_firm TEXT,target_company TEXT) RETURNS VOID
 LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$ BEGIN
 IF target_firm IS DISTINCT FROM accounting.actor_firm() OR NOT accounting.actor_active()
 OR NOT accounting.company_visible(target_company) THEN RAISE EXCEPTION 'Current company access required' USING ERRCODE='42501';END IF;
 IF coalesce(current_setting('app.write_scope_company',true),'')<>''
 AND (target_firm IS DISTINCT FROM current_setting('app.write_scope_firm',true) OR target_company IS DISTINCT FROM current_setting('app.write_scope_company',true))
 THEN RAISE EXCEPTION 'Company write scope is immutable within a transaction' USING ERRCODE='42501';END IF;
 PERFORM set_config('app.write_scope_firm',target_firm,true);
 PERFORM set_config('app.write_scope_company',target_company,true);
 PERFORM accounting.lock_company_write(target_firm,target_company);
 END $$;

-- Preserve the current definition, grants, owner and every domain check. Replace
-- only the literal old-lock call with a fail-fast compatibility entry point.
DO $$ DECLARE fn RECORD;definition TEXT;BEGIN
 FOR fn IN SELECT p.oid FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
 WHERE n.nspname='accounting' AND p.proname<>'lock_legacy_write'
 AND p.prosrc LIKE '%pg_advisory_xact_lock(6420031786281903101)%' LOOP
  definition:=pg_get_functiondef(fn.oid);
  EXECUTE replace(definition,'pg_advisory_xact_lock(6420031786281903101)','accounting.lock_legacy_write()');
 END LOOP;
END $$;

-- Only this reviewed allowlist participates in company transactions. All other
-- native mutation functions still enter the exclusive compatibility domain.
DO $$ DECLARE fn RECORD;table_name TEXT;target_name TEXT;replacement TEXT;BEGIN
 FOR fn IN SELECT p.oid,p.proname FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
 WHERE n.nspname='accounting' AND p.proname=ANY(ARRAY[
 'reserve_model_cost','reserve_intake_model_cost','reserve_assistant_model_cost','mark_model_cost_sent','settle_model_cost',
 'worker_bind_ocr_run','worker_mark_ocr_sending','worker_renew_ocr_lease','worker_finish_ocr_job','record_ops_exception_snapshot']) LOOP
  table_name:=CASE WHEN fn.proname='reserve_model_cost' THEN 'ai_runs'
   WHEN fn.proname='reserve_intake_model_cost' THEN 'intake_runs'
   WHEN fn.proname='reserve_assistant_model_cost' THEN 'assistant_turns'
   WHEN fn.proname IN('mark_model_cost_sent','settle_model_cost') THEN 'model_costs'
   WHEN fn.proname='record_ops_exception_snapshot' THEN 'business_events' ELSE 'ocr_jobs' END;
  target_name:=CASE WHEN fn.proname='record_ops_exception_snapshot' THEN 'target_event'
   WHEN fn.proname LIKE 'worker_%' THEN 'target_job' ELSE 'target_run' END;
  replacement:=format('accounting.lock_company_write((SELECT firm_id FROM accounting.%I WHERE id=%s),(SELECT company_id FROM accounting.%I WHERE id=%s))',table_name,target_name,table_name,target_name);
  EXECUTE replace(pg_get_functiondef(fn.oid),'accounting.lock_legacy_write()',replacement);
 END LOOP;
END $$;

CREATE FUNCTION accounting.company_write_table_guard() RETURNS TRIGGER LANGUAGE plpgsql
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
 ELSIF TG_TABLE_NAME=ANY(ARRAY['ai_runs','ai_findings','document_extractions','ocr_jobs','ocr_attempts','model_costs','ops_exception_snapshots','ops_company_dirty','ops_company_refresh','processing_job_metrics']) THEN
  PERFORM accounting.lock_company_write(value->>'firm_id',value->>'company_id');
 ELSE PERFORM accounting.lock_legacy_write();
 END IF;
 RETURN coalesce(NEW,OLD);END $$;
-- Take the gate before PostgreSQL acquires tuple locks. A row-only guard would
-- invert the order for native UPDATE versus a Python transaction holding the
-- gate first, and could deadlock during a policy change or lease transition.
CREATE FUNCTION accounting.compatibility_statement_gate() RETURNS TRIGGER LANGUAGE plpgsql
 SET search_path=pg_catalog,accounting,pg_temp AS $$ BEGIN
 IF coalesce(current_setting('app.write_scope_company',true),'')<>'' THEN
  PERFORM accounting.lock_company_write(current_setting('app.write_scope_firm',true),current_setting('app.write_scope_company',true));
 ELSIF coalesce(current_setting('app.write_dispatch',true),'')='1' THEN
  PERFORM accounting.lock_dispatch_write();
 ELSE PERFORM accounting.lock_legacy_write();END IF;
 RETURN NULL;END $$;
DO $$ DECLARE item RECORD;BEGIN
 FOR item IN SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
 WHERE n.nspname='accounting' AND c.relkind='r' AND c.relname NOT IN('schema_migrations','transaction_metric_buckets') LOOP
  EXECUTE format('CREATE TRIGGER batch2_company_write_gate BEFORE INSERT OR UPDATE OR DELETE ON accounting.%I FOR EACH ROW EXECUTE FUNCTION accounting.company_write_table_guard()',item.relname);
  EXECUTE format('CREATE TRIGGER batch2_statement_write_gate BEFORE INSERT OR UPDATE OR DELETE ON accounting.%I FOR EACH STATEMENT EXECUTE FUNCTION accounting.compatibility_statement_gate()',item.relname);
 END LOOP;
END $$;
REVOKE ALL ON FUNCTION accounting.company_lock_key(TEXT,TEXT),accounting.lock_company_write(TEXT,TEXT),accounting.company_write_table_guard() FROM PUBLIC;
REVOKE ALL ON FUNCTION accounting.compatibility_statement_gate() FROM PUBLIC;
REVOKE ALL ON FUNCTION accounting.begin_company_write(TEXT,TEXT),accounting.lock_legacy_write() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION accounting.begin_company_write(TEXT,TEXT),accounting.lock_company_write(TEXT,TEXT),accounting.company_lock_key(TEXT,TEXT),accounting.lock_legacy_write() TO accounting_app,accounting_worker,accounting_system;
