-- compatibility: incremental
-- The child watch row has no company_id. Resolve its immutable proposal parent
-- under a dedicated guard rather than entering the legacy gate from handoff.
CREATE FUNCTION accounting.proposal_resume_watch_write_guard() RETURNS TRIGGER
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
DECLARE value JSONB; parent_firm TEXT; parent_company TEXT;
BEGIN
 IF TG_RELID<>'accounting.proposal_resume_watch'::regclass OR TG_WHEN<>'BEFORE'
  OR TG_LEVEL<>'ROW' OR TG_NARGS<>0 THEN
  RAISE EXCEPTION 'Invalid proposal watch guard target' USING ERRCODE='42501';
 END IF;
 IF TG_OP='UPDATE' AND NEW.job_id IS DISTINCT FROM OLD.job_id THEN
  RAISE EXCEPTION 'Proposal watch parent is immutable' USING ERRCODE='23514';
 END IF;
 IF coalesce(current_setting('app.write_scope_company',true),'')='' THEN
  IF coalesce(current_setting('app.write_dispatch',true),'')='1' THEN
   RAISE EXCEPTION 'Write exceeds dispatcher domain' USING ERRCODE='42501';
  END IF;
  PERFORM accounting.lock_legacy_write();
  RETURN coalesce(NEW,OLD);
 END IF;
 value:=CASE WHEN TG_OP='DELETE' THEN to_jsonb(OLD) ELSE to_jsonb(NEW) END;
 SELECT firm_id,company_id INTO parent_firm,parent_company
  FROM accounting.proposal_jobs WHERE id=value->>'job_id';
 IF parent_firm IS DISTINCT FROM current_setting('app.write_scope_firm',true)
  OR parent_company IS DISTINCT FROM current_setting('app.write_scope_company',true) THEN
  RAISE EXCEPTION 'Proposal watch exceeds declared company scope' USING ERRCODE='42501';
 END IF;
 PERFORM accounting.lock_company_write(parent_firm,parent_company);
 RETURN coalesce(NEW,OLD);
END $$;
REVOKE ALL ON FUNCTION accounting.proposal_resume_watch_write_guard() FROM PUBLIC,accounting_app,accounting_worker,accounting_system;

DO $$ DECLARE gate RECORD;
BEGIN
 SELECT tgfoid,tgtype,tgenabled,tgnargs,tgqual INTO gate FROM pg_trigger
  WHERE tgrelid='accounting.proposal_resume_watch'::regclass
   AND tgname='batch2_company_write_gate' AND NOT tgisinternal;
 IF gate.tgfoid IS DISTINCT FROM 'accounting.company_write_table_guard()'::regprocedure
  OR gate.tgtype IS DISTINCT FROM 31 OR gate.tgenabled IS DISTINCT FROM 'O'
  OR gate.tgnargs IS DISTINCT FROM 0 OR gate.tgqual IS NOT NULL THEN
  RAISE EXCEPTION 'Unexpected proposal watch write gate' USING ERRCODE='55000';
 END IF;
 DROP TRIGGER batch2_company_write_gate ON accounting.proposal_resume_watch;
 CREATE TRIGGER batch2_company_write_gate BEFORE INSERT OR UPDATE OR DELETE
  ON accounting.proposal_resume_watch FOR EACH ROW
  EXECUTE FUNCTION accounting.proposal_resume_watch_write_guard();
END $$;
