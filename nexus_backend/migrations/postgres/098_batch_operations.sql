-- compatibility: incremental
-- 跨企业批处理父任务：父任务只汇总，子项逐企业执行并独立重试。
CREATE TABLE IF NOT EXISTS accounting.batch_operations(
 id TEXT PRIMARY KEY,
 firm_id TEXT NOT NULL REFERENCES accounting.firms(id),
 kind TEXT NOT NULL CHECK(kind IN('monthly_open')),
 status TEXT NOT NULL CHECK(status IN('queued','running','completed','attention','cancelled')),
 idempotency_key TEXT NOT NULL,
 request_json TEXT NOT NULL,
 total INTEGER NOT NULL CHECK(total>=0),
 succeeded INTEGER NOT NULL DEFAULT 0 CHECK(succeeded>=0),
 skipped INTEGER NOT NULL DEFAULT 0 CHECK(skipped>=0),
 failed INTEGER NOT NULL DEFAULT 0 CHECK(failed>=0),
 created_by TEXT NOT NULL REFERENCES accounting.users(id),
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL,
 started_at TEXT,
 finished_at TEXT,
 UNIQUE(firm_id,created_by,idempotency_key)
);
CREATE TABLE IF NOT EXISTS accounting.batch_operation_items(
 id TEXT PRIMARY KEY,
 batch_id TEXT NOT NULL REFERENCES accounting.batch_operations(id) ON DELETE CASCADE,
 firm_id TEXT NOT NULL REFERENCES accounting.firms(id),
 company_id TEXT NOT NULL REFERENCES accounting.companies(id),
 company_name TEXT NOT NULL DEFAULT '',
 sequence_no INTEGER NOT NULL,
 status TEXT NOT NULL CHECK(status IN('queued','running','succeeded','skipped','failed','retry_wait','cancelled')),
 attempt INTEGER NOT NULL DEFAULT 0 CHECK(attempt>=0),
 max_attempts INTEGER NOT NULL DEFAULT 3 CHECK(max_attempts BETWEEN 1 AND 10),
 available_at TEXT NOT NULL,
 lease_token TEXT,
 lease_until TEXT,
 worker_id TEXT,
 result_json TEXT NOT NULL DEFAULT '{}',
 error_code TEXT NOT NULL DEFAULT '',
 error_detail TEXT NOT NULL DEFAULT '',
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL,
 started_at TEXT,
 finished_at TEXT,
 UNIQUE(batch_id,company_id)
);
CREATE INDEX IF NOT EXISTS idx_batch_operations_firm
 ON accounting.batch_operations(firm_id,created_at DESC,id);
CREATE INDEX IF NOT EXISTS idx_batch_items_claim
 ON accounting.batch_operation_items(status,available_at,created_at,id);
CREATE INDEX IF NOT EXISTS idx_batch_items_batch
 ON accounting.batch_operation_items(batch_id,sequence_no,id);

ALTER TABLE accounting.batch_operations ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.batch_operations FORCE ROW LEVEL SECURITY;
ALTER TABLE accounting.batch_operation_items ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.batch_operation_items FORCE ROW LEVEL SECURITY;
CREATE POLICY batch_operations_manager ON accounting.batch_operations FOR ALL TO accounting_app
 USING(firm_id=accounting.actor_firm() AND EXISTS(
  SELECT 1 FROM accounting.memberships m WHERE m.firm_id=batch_operations.firm_id
  AND m.user_id=accounting.actor_user() AND m.role IN('owner','admin')))
 WITH CHECK(firm_id=accounting.actor_firm() AND EXISTS(
  SELECT 1 FROM accounting.memberships m WHERE m.firm_id=batch_operations.firm_id
  AND m.user_id=accounting.actor_user() AND m.role IN('owner','admin')));
CREATE POLICY batch_operation_items_manager ON accounting.batch_operation_items FOR ALL TO accounting_app
 USING(firm_id=accounting.actor_firm() AND EXISTS(
  SELECT 1 FROM accounting.memberships m WHERE m.firm_id=batch_operation_items.firm_id
  AND m.user_id=accounting.actor_user() AND m.role IN('owner','admin')))
 WITH CHECK(firm_id=accounting.actor_firm() AND EXISTS(
  SELECT 1 FROM accounting.memberships m WHERE m.firm_id=batch_operation_items.firm_id
  AND m.user_id=accounting.actor_user() AND m.role IN('owner','admin')));

REVOKE ALL ON accounting.batch_operations,accounting.batch_operation_items FROM PUBLIC,accounting_app,accounting_worker;
GRANT SELECT,INSERT,UPDATE ON accounting.batch_operations,accounting.batch_operation_items TO accounting_app;

CREATE OR REPLACE FUNCTION accounting.worker_claim_batch_item(
 p_worker_id TEXT,p_lease_seconds INTEGER
) RETURNS TABLE(
 item_id TEXT,batch_id TEXT,firm_id TEXT,created_by TEXT,kind TEXT,request_json TEXT,
 company_id TEXT,company_name TEXT,attempt INTEGER,max_attempts INTEGER,lease_token TEXT
) LANGUAGE plpgsql SECURITY DEFINER
 SET search_path=pg_catalog,accounting,pg_temp AS $$
DECLARE claimed TEXT; token TEXT; stamp TEXT; lease TEXT; target_batch TEXT;
BEGIN
 IF p_worker_id IS NULL OR length(p_worker_id)=0 OR p_lease_seconds NOT BETWEEN 30 AND 3600 THEN
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
  WHERE candidate.status IN('queued','retry_wait') AND candidate.available_at<=stamp
  ORDER BY candidate.created_at,candidate.id FOR UPDATE SKIP LOCKED LIMIT 1
 ) RETURNING i.id,i.batch_id INTO claimed,target_batch;
 IF claimed IS NULL THEN RETURN; END IF;
 UPDATE accounting.batch_operations b SET status='running',
  started_at=COALESCE(b.started_at,stamp),updated_at=stamp WHERE b.id=target_batch;
 RETURN QUERY SELECT i.id,i.batch_id,i.firm_id,b.created_by,b.kind,b.request_json,i.company_id,
  i.company_name,i.attempt,i.max_attempts,i.lease_token
  FROM accounting.batch_operation_items i JOIN accounting.batch_operations b ON b.id=i.batch_id
  WHERE i.id=claimed;
END $$;

CREATE OR REPLACE FUNCTION accounting.worker_finish_batch_item(
 p_item_id TEXT,p_lease_token TEXT,p_status TEXT,p_result_json TEXT,
 p_error_code TEXT,p_error_detail TEXT,p_retry_delay_seconds INTEGER
) RETURNS BOOLEAN LANGUAGE plpgsql SECURITY DEFINER
 SET search_path=pg_catalog,accounting,pg_temp AS $$
DECLARE stamp TEXT; target_batch TEXT; counts RECORD;
BEGIN
 IF p_status NOT IN('succeeded','skipped','failed','retry_wait') THEN
  RAISE EXCEPTION 'Batch item status is invalid' USING ERRCODE='22023';
 END IF;
 stamp:=to_char(clock_timestamp() AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"+00:00"');
 UPDATE accounting.batch_operation_items i SET status=p_status,
  result_json=COALESCE(NULLIF(p_result_json,''),'{}'),
  error_code=COALESCE(p_error_code,''),error_detail=COALESCE(p_error_detail,''),
  available_at=CASE WHEN p_status='retry_wait' THEN to_char((clock_timestamp()+make_interval(secs=>greatest(p_retry_delay_seconds,0))) AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"+00:00"') ELSE i.available_at END,
  lease_token=NULL,lease_until=NULL,worker_id=NULL,updated_at=stamp,
  finished_at=CASE WHEN p_status IN('succeeded','skipped','failed') THEN stamp ELSE NULL END
 WHERE i.id=p_item_id AND i.lease_token=p_lease_token AND i.status='running'
 RETURNING i.batch_id INTO target_batch;
 IF target_batch IS NULL THEN RETURN false; END IF;
 SELECT COUNT(*) FILTER(WHERE status='succeeded') succeeded,
        COUNT(*) FILTER(WHERE status='skipped') skipped,
        COUNT(*) FILTER(WHERE status='failed') failed,
        COUNT(*) FILTER(WHERE status IN('queued','retry_wait')) pending,
        COUNT(*) FILTER(WHERE status='running') running
 INTO counts FROM accounting.batch_operation_items WHERE batch_id=target_batch;
 UPDATE accounting.batch_operations b SET succeeded=counts.succeeded,skipped=counts.skipped,
  failed=counts.failed,
  status=CASE WHEN counts.running>0 THEN 'running'
   WHEN counts.pending>0 THEN 'queued'
   WHEN counts.failed>0 THEN 'attention'
   ELSE 'completed' END,
  updated_at=stamp,
  finished_at=CASE WHEN counts.running=0 AND counts.pending=0 THEN stamp ELSE NULL END
 WHERE b.id=target_batch;
 RETURN true;
END $$;

CREATE OR REPLACE FUNCTION accounting.recover_batch_operations() RETURNS INTEGER
LANGUAGE plpgsql SECURITY DEFINER
 SET search_path=pg_catalog,accounting,pg_temp AS $$
DECLARE stamp TEXT; changed INTEGER;
BEGIN
 stamp:=to_char(clock_timestamp() AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"+00:00"');
 UPDATE accounting.batch_operation_items i SET
  status=CASE WHEN i.attempt<i.max_attempts THEN 'retry_wait' ELSE 'failed' END,
  error_code='worker_restarted',
  error_detail=CASE WHEN i.attempt<i.max_attempts THEN '后台执行中断，已进入重试队列' ELSE '后台执行中断且达到重试上限，请人工处理' END,
  available_at=stamp,lease_token=NULL,lease_until=NULL,worker_id=NULL,updated_at=stamp,
  finished_at=CASE WHEN i.attempt<i.max_attempts THEN NULL ELSE stamp END
 WHERE i.status='running' AND i.lease_until<=stamp;
 GET DIAGNOSTICS changed=ROW_COUNT;
 UPDATE accounting.batch_operations b SET
  succeeded=(SELECT COUNT(*) FROM accounting.batch_operation_items i WHERE i.batch_id=b.id AND i.status='succeeded'),
  skipped=(SELECT COUNT(*) FROM accounting.batch_operation_items i WHERE i.batch_id=b.id AND i.status='skipped'),
  failed=(SELECT COUNT(*) FROM accounting.batch_operation_items i WHERE i.batch_id=b.id AND i.status='failed'),
  status=CASE
   WHEN EXISTS(SELECT 1 FROM accounting.batch_operation_items i WHERE i.batch_id=b.id AND i.status='running') THEN 'running'
   WHEN EXISTS(SELECT 1 FROM accounting.batch_operation_items i WHERE i.batch_id=b.id AND i.status IN('queued','retry_wait')) THEN 'queued'
   WHEN EXISTS(SELECT 1 FROM accounting.batch_operation_items i WHERE i.batch_id=b.id AND i.status='failed') THEN 'attention'
   ELSE 'completed' END,
  updated_at=stamp,
  finished_at=CASE WHEN NOT EXISTS(SELECT 1 FROM accounting.batch_operation_items i WHERE i.batch_id=b.id AND i.status IN('queued','retry_wait','running')) THEN stamp ELSE NULL END
 WHERE b.status IN('queued','running');
 RETURN changed;
END $$;

REVOKE ALL ON FUNCTION accounting.worker_claim_batch_item(TEXT,INTEGER) FROM PUBLIC,accounting_app,accounting_worker;
REVOKE ALL ON FUNCTION accounting.worker_finish_batch_item(TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,INTEGER) FROM PUBLIC,accounting_app,accounting_worker;
REVOKE ALL ON FUNCTION accounting.recover_batch_operations() FROM PUBLIC,accounting_app,accounting_worker;
GRANT EXECUTE ON FUNCTION accounting.worker_claim_batch_item(TEXT,INTEGER) TO accounting_worker;
GRANT EXECUTE ON FUNCTION accounting.worker_finish_batch_item(TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,INTEGER) TO accounting_worker;
GRANT EXECUTE ON FUNCTION accounting.recover_batch_operations() TO accounting_worker;
