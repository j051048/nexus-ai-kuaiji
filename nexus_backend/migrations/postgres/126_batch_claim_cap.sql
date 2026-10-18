-- compatibility: incremental
-- R05/F11: do not claim a retry_wait row that has already exhausted its
-- max_attempts. The finish guard still converts late writes to failed.
DO $do$ DECLARE d TEXT; BEGIN
 d:=pg_get_functiondef('accounting.worker_claim_batch_item(text,integer)'::regprocedure);
 d:=replace(d,
  'WHERE candidate.status IN(''queued'',''retry_wait'') AND candidate.available_at<=stamp',
  'WHERE candidate.status IN(''queued'',''retry_wait'') AND candidate.available_at<=stamp AND candidate.attempt<candidate.max_attempts');
 EXECUTE d;
END $do$;
