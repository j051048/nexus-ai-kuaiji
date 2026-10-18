-- compatibility: incremental
-- Proposal result mutations run inside the declared single-company transaction.
-- Keep their existing capability/lease/authorization checks and grants. Scheduler
-- claims, dependency scans and recovery keep their separate dispatcher lock.
DO $$
DECLARE signature TEXT; definition TEXT; lock_call TEXT;
BEGIN
 FOREACH signature IN ARRAY ARRAY[
  'accounting.proposal_wait_dependencies(text)',
  'accounting.cancel_proposal_job(text,text)',
  'accounting.cancel_resumable_proposal(text,text)',
  'accounting.worker_bind_proposal_run(text,text,text)',
  'accounting.worker_mark_proposal_sending(text,text)',
  'accounting.worker_renew_proposal_lease(text,text,integer)',
  'accounting.worker_finish_proposal_job(text,text,text,text,text,text,integer)',
  'accounting.worker_finish_proposal_rule(text,text,text,integer,integer)',
  'accounting.worker_finish_proposal_company_rule(text,text,text,integer,integer)',
  'accounting.worker_resume_proposal(text,text,text,text)'
 ] LOOP
  definition:=pg_get_functiondef(signature::regprocedure);
  lock_call:='PERFORM accounting.lock_legacy_write();';
  IF (length(definition)-length(replace(definition,lock_call,'')))/length(lock_call)<>1
   OR position('target_job text' in lower(definition))=0
   OR position('SECURITY DEFINER' in definition)=0 THEN
   RAISE EXCEPTION 'Unexpected proposal helper definition: %',signature USING ERRCODE='55000';
  END IF;
  -- The row is resolved by the existing SECURITY DEFINER function. The lock
  -- rejects a different declared firm/company before any mutation. An absent
  -- company scope retains the legacy gate; dispatcher entry stays forbidden.
  EXECUTE replace(definition,lock_call,
   'PERFORM accounting.lock_company_write((SELECT firm_id FROM accounting.proposal_jobs WHERE id=target_job),(SELECT company_id FROM accounting.proposal_jobs WHERE id=target_job));');
 END LOOP;
END $$;
