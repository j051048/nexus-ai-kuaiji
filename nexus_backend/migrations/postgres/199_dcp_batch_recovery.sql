-- compatibility: incremental
-- Preserve restricted worker API; commits and queue settlement are recovered separately.
CREATE OR REPLACE FUNCTION accounting.worker_claim_batch_item(p_worker_id text, p_lease_seconds integer)
 RETURNS TABLE(item_id text, batch_id text, firm_id text, created_by text, kind text, request_json text, company_id text, company_name text, attempt integer, max_attempts integer, lease_token text)
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog', 'accounting', 'pg_temp'
AS $function$
DECLARE claimed TEXT; token TEXT; stamp TEXT; lease TEXT; target_batch TEXT;
BEGIN
 PERFORM accounting.lock_legacy_write();
 IF p_worker_id IS NULL OR length(p_worker_id)=0 OR p_lease_seconds IS NULL OR p_lease_seconds NOT BETWEEN 30 AND 3600 THEN
  RAISE EXCEPTION 'Batch worker identity or lease is invalid' USING ERRCODE='22023';
 END IF;
 stamp:=to_char(clock_timestamp() AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"+00:00"');
 lease:=to_char((clock_timestamp()+make_interval(secs=>p_lease_seconds)) AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"+00:00"');
 token:=md5(p_worker_id||clock_timestamp()::text||random()::text);
 UPDATE accounting.batch_operation_items i
 SET status='running',attempt=i.attempt+1,worker_id=p_worker_id,lease_token=token,
     lease_until=lease,started_at=COALESCE(i.started_at,stamp),updated_at=stamp
 WHERE i.id=(
  SELECT candidate.id FROM accounting.batch_operation_items candidate
  WHERE candidate.status IN('queued','retry_wait') AND candidate.available_at<=stamp AND candidate.attempt<candidate.max_attempts AND candidate.cancel_requested_at IS NULL
  ORDER BY candidate.created_at,candidate.id FOR UPDATE SKIP LOCKED LIMIT 1
 ) RETURNING i.id,i.batch_id INTO claimed,target_batch;
 IF claimed IS NULL THEN RETURN; END IF;
 UPDATE accounting.batch_operations b SET status='running',
  started_at=COALESCE(b.started_at,stamp),updated_at=stamp WHERE b.id=target_batch;
 RETURN QUERY SELECT i.id,i.batch_id,i.firm_id,b.created_by,b.kind,b.request_json,i.company_id,
  i.company_name,i.attempt,i.max_attempts,i.lease_token
  FROM accounting.batch_operation_items i JOIN accounting.batch_operations b ON b.id=i.batch_id
  WHERE i.id=claimed;
END $function$;

CREATE OR REPLACE FUNCTION accounting.dcp_batch_committed_result(target_item TEXT) RETURNS JSONB
 LANGUAGE sql STABLE SET search_path=pg_catalog,accounting,pg_temp AS $$
 SELECT CASE b.kind WHEN 'review_export' THEN
  (SELECT jsonb_build_object('status','created','book_id',e.book_id,'export_id',e.id,'filename',e.filename,'count',e.count)
   FROM accounting.exports e JOIN accounting.books bk ON bk.id=e.book_id
   WHERE e.idempotency_key='batch:'||b.id||':'||i.company_id AND e.created_by=b.created_by
    AND bk.company_id=i.company_id AND bk.firm_id=i.firm_id
    AND e.book_id=(SELECT j->>'book_id' FROM jsonb_array_elements(b.request_json::jsonb->'companies') j WHERE j->>'company_id'=i.company_id))
 WHEN 'package_draft' THEN
  (SELECT jsonb_build_object('status','created','book_id',p.book_id,'package_id',p.id,'package_version',p.version,'report_id',p.report_id,'report_version',p.report_version)
   FROM accounting.monthly_delivery_packages p WHERE p.idempotency_key='batch:'||b.id||':'||i.company_id
    AND p.created_by=b.created_by AND p.company_id=i.company_id AND p.firm_id=i.firm_id
    AND p.book_id=(SELECT j->>'book_id' FROM jsonb_array_elements(b.request_json::jsonb->'companies') j WHERE j->>'company_id'=i.company_id))
 ELSE NULL END FROM accounting.batch_operation_items i JOIN accounting.batch_operations b ON b.id=i.batch_id WHERE i.id=target_item $$;
REVOKE ALL ON FUNCTION accounting.dcp_batch_committed_result(TEXT) FROM PUBLIC,accounting_app,accounting_worker;

CREATE OR REPLACE FUNCTION accounting.dcp_batch_recount(target_batch TEXT,stamp TEXT) RETURNS VOID
 LANGUAGE sql SET search_path=pg_catalog,accounting,pg_temp AS $$
 UPDATE accounting.batch_operations b SET succeeded=c.succeeded,skipped=c.skipped,failed=c.failed,cancelled=c.cancelled,
 status=CASE WHEN c.running>0 THEN 'running' WHEN c.pending>0 THEN 'queued' WHEN c.failed>0 THEN 'attention' ELSE 'completed' END,
 updated_at=stamp,finished_at=CASE WHEN c.running=0 AND c.pending=0 THEN stamp ELSE NULL END
 FROM (SELECT COUNT(*) FILTER(WHERE status='succeeded') succeeded,COUNT(*) FILTER(WHERE status='skipped') skipped,
 COUNT(*) FILTER(WHERE status='failed') failed,COUNT(*) FILTER(WHERE status='cancelled') cancelled,
 COUNT(*) FILTER(WHERE status='running') running,COUNT(*) FILTER(WHERE status IN('queued','retry_wait')) pending
 FROM accounting.batch_operation_items WHERE batch_id=target_batch) c WHERE b.id=target_batch $$;
REVOKE ALL ON FUNCTION accounting.dcp_batch_recount(TEXT,TEXT) FROM PUBLIC,accounting_app,accounting_worker;

CREATE OR REPLACE FUNCTION accounting.worker_finish_batch_item(
 p_item_id TEXT,p_lease_token TEXT,p_status TEXT,p_result_json TEXT,
 p_error_code TEXT,p_error_detail TEXT,p_retry_delay_seconds INTEGER
) RETURNS BOOLEAN LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
DECLARE stamp TEXT; item RECORD; committed JSONB;
BEGIN
 PERFORM accounting.lock_legacy_write();
 IF p_status IS NULL OR p_status NOT IN('succeeded','skipped','failed','retry_wait','cancelled')
  OR p_retry_delay_seconds IS NULL OR p_retry_delay_seconds NOT BETWEEN 0 AND 86400 THEN
  RAISE EXCEPTION 'Invalid batch settlement' USING ERRCODE='22023';
 END IF;
 stamp:=to_char(clock_timestamp() AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"+00:00"');
 SELECT * INTO item FROM accounting.batch_operation_items WHERE id=p_item_id AND lease_token=p_lease_token
  AND status='running' AND lease_until>stamp FOR UPDATE;
 IF NOT FOUND THEN RETURN false; END IF;
 committed:=accounting.dcp_batch_committed_result(item.id);
 IF committed IS NOT NULL THEN
  p_status:='succeeded';p_result_json:=committed::text;p_error_code:='';p_error_detail:='';
 ELSIF item.cancel_requested_at IS NOT NULL AND p_status NOT IN('succeeded','skipped') THEN
  p_status:='cancelled';p_error_code:='cancelled';p_error_detail:=item.cancel_reason;
 ELSIF p_status='retry_wait' AND item.attempt>=item.max_attempts THEN
  p_status:='failed';p_error_code:='max_attempts';p_error_detail:='达到重试上限，已转人工处理';
 END IF;
 UPDATE accounting.batch_operation_items SET status=p_status,result_json=COALESCE(NULLIF(p_result_json,''),'{}'),
 error_code=COALESCE(p_error_code,''),error_detail=COALESCE(p_error_detail,''),
 available_at=CASE WHEN p_status='retry_wait' THEN to_char((clock_timestamp()+make_interval(secs=>p_retry_delay_seconds)) AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"+00:00"') ELSE available_at END,
 lease_token=NULL,lease_until=NULL,worker_id=NULL,updated_at=stamp,
 finished_at=CASE WHEN p_status='retry_wait' THEN NULL ELSE stamp END WHERE id=item.id;
 PERFORM accounting.dcp_batch_recount(item.batch_id,stamp);
 RETURN true;
END $$;

CREATE OR REPLACE FUNCTION accounting.recover_batch_operations() RETURNS INTEGER
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
DECLARE stamp TEXT; item RECORD; resolved JSONB; state TEXT; changed INTEGER:=0;
BEGIN
 PERFORM accounting.lock_legacy_write();
 stamp:=to_char(clock_timestamp() AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"+00:00"');
 FOR item IN SELECT * FROM accounting.batch_operation_items WHERE status='running' AND lease_until<=stamp FOR UPDATE LOOP
  resolved:=accounting.dcp_batch_committed_result(item.id);
  state:=CASE WHEN resolved IS NOT NULL THEN 'succeeded' WHEN item.cancel_requested_at IS NOT NULL THEN 'cancelled'
   WHEN item.attempt<item.max_attempts THEN 'retry_wait' ELSE 'failed' END;
  UPDATE accounting.batch_operation_items SET status=state,result_json=COALESCE(resolved::text,result_json),
   error_code=CASE WHEN resolved IS NOT NULL THEN '' WHEN state='cancelled' THEN 'cancelled' ELSE 'worker_restarted' END,
   error_detail=CASE WHEN resolved IS NOT NULL THEN '' WHEN state='cancelled' THEN item.cancel_reason ELSE '执行中断，请按当前状态重试或人工核对' END,
   available_at=stamp,lease_token=NULL,lease_until=NULL,worker_id=NULL,updated_at=stamp,
   finished_at=CASE WHEN state='retry_wait' THEN NULL ELSE stamp END WHERE id=item.id;
  PERFORM accounting.dcp_batch_recount(item.batch_id,stamp);
  changed:=changed+1;
 END LOOP;
 RETURN changed;
END $$;
REVOKE ALL ON FUNCTION accounting.worker_claim_batch_item(TEXT,INTEGER),accounting.worker_finish_batch_item(TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,INTEGER),accounting.recover_batch_operations() FROM PUBLIC,accounting_app;
GRANT EXECUTE ON FUNCTION accounting.worker_claim_batch_item(TEXT,INTEGER),accounting.worker_finish_batch_item(TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,INTEGER),accounting.recover_batch_operations() TO accounting_worker;

