-- compatibility: incremental
-- Case commands already enter a declared company transaction. These reviewed
-- capabilities must take that same company gate before locking their case row.
-- Keep current authorization, independent acceptance, version, original-evidence
-- and open-period checks, function owner/search_path and all existing grants.
DO $$ DECLARE signature TEXT; definition TEXT; lock_call TEXT;
BEGIN
 FOREACH signature IN ARRAY ARRAY[
  'accounting.case_accept_evidence(text,integer)',
  'accounting.case_collection_update(text,integer,text,text)'
 ] LOOP
  definition:=pg_get_functiondef(signature::regprocedure);
  lock_call:='PERFORM accounting.lock_legacy_write();';
  IF (length(definition)-length(replace(definition,lock_call,'')))/length(lock_call)<>1
   OR position('target_case text' in lower(definition))=0
   OR position('SECURITY DEFINER' in definition)=0 THEN
   RAISE EXCEPTION 'Unexpected case helper definition: %',signature USING ERRCODE='55000';
  END IF;
  EXECUTE replace(definition,lock_call,
   'PERFORM accounting.lock_company_write((SELECT firm_id FROM accounting.case_tasks WHERE id=target_case),(SELECT company_id FROM accounting.case_tasks WHERE id=target_case));');
 END LOOP;
END $$;

-- Finance material collectors intentionally cannot read internal case_tasks.
-- Resolve command receipts through a trigger-only capability, without granting
-- access to that parent or widening case command INSERT/SELECT policies.
CREATE FUNCTION accounting.case_command_write_guard() RETURNS TRIGGER
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
DECLARE value JSONB; parent_firm TEXT; parent_company TEXT;
BEGIN
 IF TG_RELID<>'accounting.case_commands'::regclass OR TG_WHEN<>'BEFORE'
  OR TG_LEVEL<>'ROW' OR TG_NARGS<>0 THEN
  RAISE EXCEPTION 'Invalid case command guard target' USING ERRCODE='42501';
 END IF;
 IF TG_OP='UPDATE' AND (NEW.case_id,NEW.firm_id) IS DISTINCT FROM (OLD.case_id,OLD.firm_id) THEN
  RAISE EXCEPTION 'Case command parent is immutable' USING ERRCODE='23514';
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
  FROM accounting.case_tasks WHERE id=value->>'case_id';
 IF parent_firm IS DISTINCT FROM current_setting('app.write_scope_firm',true)
  OR parent_company IS DISTINCT FROM current_setting('app.write_scope_company',true)
  OR value->>'firm_id' IS DISTINCT FROM parent_firm THEN
  RAISE EXCEPTION 'Case command exceeds declared company scope' USING ERRCODE='42501';
 END IF;
 PERFORM accounting.lock_company_write(parent_firm,parent_company);
 RETURN coalesce(NEW,OLD);
END $$;
REVOKE ALL ON FUNCTION accounting.case_command_write_guard() FROM PUBLIC,accounting_app,accounting_worker,accounting_system;

DO $$ DECLARE gate RECORD;
BEGIN
 SELECT tgfoid,tgtype,tgenabled,tgnargs,tgqual INTO gate FROM pg_trigger
  WHERE tgrelid='accounting.case_commands'::regclass
   AND tgname='batch2_company_write_gate' AND NOT tgisinternal;
 IF gate.tgfoid IS DISTINCT FROM 'accounting.company_write_table_guard()'::regprocedure
  OR gate.tgtype IS DISTINCT FROM 31 OR gate.tgenabled IS DISTINCT FROM 'O'
  OR gate.tgnargs IS DISTINCT FROM 0 OR gate.tgqual IS NOT NULL THEN
  RAISE EXCEPTION 'Unexpected case command write gate' USING ERRCODE='55000';
 END IF;
 DROP TRIGGER batch2_company_write_gate ON accounting.case_commands;
 CREATE TRIGGER batch2_company_write_gate BEFORE INSERT OR UPDATE OR DELETE
  ON accounting.case_commands FOR EACH ROW
  EXECUTE FUNCTION accounting.case_command_write_guard();
END $$;
