-- Suspended/archived clients retain queued work without occupying shared slots.
-- Publishing an already-running request still checks its current authorization.
DO $$ DECLARE fn RECORD; definition TEXT; replacement TEXT; BEGIN
 FOR fn IN SELECT p.oid,p.proname FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
 WHERE n.nspname='accounting' AND p.proname=ANY(ARRAY['claim_ocr_job','claim_proposal_job','claim_processing_job']) LOOP
  definition:=pg_get_functiondef(fn.oid);
  replacement:=replace(definition,
    'WHERE q.status IN (''queued'',''retry_wait'')',
    'WHERE EXISTS(SELECT 1 FROM accounting.firm_clients fc WHERE fc.firm_id=q.firm_id AND fc.company_id=q.company_id AND fc.status=''active'') AND q.status IN (''queued'',''retry_wait'')');
  IF replacement=definition THEN
   RAISE EXCEPTION 'Unexpected dispatcher definition for %; inspect before upgrading',fn.proname;
  END IF;
  EXECUTE replacement;
 END LOOP;
END $$;
