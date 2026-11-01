-- compatibility: incremental
SET LOCAL search_path=accounting,pg_catalog;
-- DCP04: per-item cancel/skip needs reasons and intent; retry count is separate
-- from attempt so a manual retry never rewrites history.
ALTER TABLE accounting.batch_operation_items
 ADD COLUMN IF NOT EXISTS cancel_requested_at TEXT,
 ADD COLUMN IF NOT EXISTS cancel_reason TEXT NOT NULL DEFAULT '',
 ADD COLUMN IF NOT EXISTS skip_reason TEXT NOT NULL DEFAULT '',
 ADD COLUMN IF NOT EXISTS retry_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE accounting.batch_operations
 ADD COLUMN IF NOT EXISTS cancelled INTEGER NOT NULL DEFAULT 0;

-- DCP04：执行中被取消的项在结算时可以落成 cancelled；取消/跳过都算终态。
CREATE OR REPLACE FUNCTION accounting.worker_finish_batch_item(
 p_item_id TEXT,p_lease_token TEXT,p_status TEXT,p_result_json TEXT,
 p_error_code TEXT,p_error_detail TEXT,p_retry_delay_seconds INTEGER
) RETURNS BOOLEAN LANGUAGE plpgsql SECURITY DEFINER
 SET search_path=pg_catalog,accounting,pg_temp AS $$
DECLARE stamp TEXT; target_batch TEXT; counts RECORD;
BEGIN
 IF p_status NOT IN('succeeded','skipped','failed','retry_wait','cancelled') THEN
  RAISE EXCEPTION 'Batch item status is invalid' USING ERRCODE='22023';
 END IF;
 stamp:=to_char(clock_timestamp() AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"+00:00"');
 UPDATE accounting.batch_operation_items i SET status=p_status,
  result_json=COALESCE(NULLIF(p_result_json,''),'{}'),
  error_code=COALESCE(p_error_code,''),error_detail=COALESCE(p_error_detail,''),
  available_at=CASE WHEN p_status='retry_wait' THEN to_char((clock_timestamp()+make_interval(secs=>greatest(p_retry_delay_seconds,0))) AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"+00:00"') ELSE i.available_at END,
  lease_token=NULL,lease_until=NULL,worker_id=NULL,updated_at=stamp,
  finished_at=CASE WHEN p_status IN('succeeded','skipped','failed','cancelled') THEN stamp ELSE NULL END
 WHERE i.id=p_item_id AND i.lease_token=p_lease_token AND i.status='running'
 RETURNING i.batch_id INTO target_batch;
 IF target_batch IS NULL THEN RETURN false; END IF;
 SELECT COUNT(*) FILTER(WHERE status='succeeded') succeeded,
        COUNT(*) FILTER(WHERE status='skipped') skipped,
        COUNT(*) FILTER(WHERE status='failed') failed,
        COUNT(*) FILTER(WHERE status='cancelled') cancelled,
        COUNT(*) FILTER(WHERE status IN('queued','retry_wait')) pending,
        COUNT(*) FILTER(WHERE status='running') running
 INTO counts FROM accounting.batch_operation_items WHERE batch_id=target_batch;
 UPDATE accounting.batch_operations b SET succeeded=counts.succeeded,skipped=counts.skipped,
  failed=counts.failed,cancelled=counts.cancelled,
  status=CASE WHEN counts.running>0 THEN 'running'
   WHEN counts.pending>0 THEN 'queued'
   WHEN counts.failed>0 THEN 'attention'
   ELSE 'completed' END,
  updated_at=stamp,
  finished_at=CASE WHEN counts.running=0 AND counts.pending=0 THEN stamp ELSE NULL END
 WHERE b.id=target_batch;
 RETURN true;
END $$;
REVOKE ALL ON FUNCTION accounting.worker_finish_batch_item(TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,INTEGER) FROM PUBLIC,accounting_app,accounting_worker;
GRANT EXECUTE ON FUNCTION accounting.worker_finish_batch_item(TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,INTEGER) TO accounting_worker;
