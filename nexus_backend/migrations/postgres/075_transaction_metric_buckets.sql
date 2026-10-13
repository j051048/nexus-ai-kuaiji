-- Short-lived diagnostic aggregates; never store SQL or business payloads.
CREATE TABLE accounting.transaction_metric_buckets (
 firm_id TEXT NOT NULL,company_id TEXT NOT NULL,stream_id TEXT NOT NULL CHECK(length(stream_id) BETWEEN 1 AND 80),
 bucket_minute BIGINT NOT NULL,tx_count INTEGER NOT NULL CHECK(tx_count BETWEEN 1 AND 1000000),
 write_count INTEGER NOT NULL CHECK(write_count BETWEEN 0 AND tx_count),
 rollback_count INTEGER NOT NULL CHECK(rollback_count BETWEEN 0 AND tx_count),
 connection_sum DOUBLE PRECISION NOT NULL CHECK(connection_sum BETWEEN 0 AND 86400000000000),
 lock_sum DOUBLE PRECISION NOT NULL CHECK(lock_sum BETWEEN 0 AND 86400000000000),
 transaction_sum DOUBLE PRECISION NOT NULL CHECK(transaction_sum BETWEEN 0 AND 86400000000000),
 connection_max DOUBLE PRECISION NOT NULL CHECK(connection_max BETWEEN 0 AND 86400000),
 lock_max DOUBLE PRECISION NOT NULL CHECK(lock_max BETWEEN 0 AND 86400000),
 transaction_max DOUBLE PRECISION NOT NULL CHECK(transaction_max BETWEEN 0 AND 86400000),
 PRIMARY KEY(firm_id,company_id,stream_id,bucket_minute),
 FOREIGN KEY(firm_id,company_id) REFERENCES accounting.firm_clients(firm_id,company_id)
);
CREATE INDEX idx_transaction_metrics_expire ON accounting.transaction_metric_buckets(bucket_minute);
CREATE INDEX idx_transaction_metrics_company ON accounting.transaction_metric_buckets(firm_id,company_id,bucket_minute);
ALTER TABLE accounting.transaction_metric_buckets ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.transaction_metric_buckets FORCE ROW LEVEL SECURITY;
REVOKE ALL ON accounting.transaction_metric_buckets FROM PUBLIC,accounting_app,accounting_system,accounting_worker;
GRANT SELECT ON accounting.transaction_metric_buckets TO accounting_app;
CREATE POLICY transaction_metrics_read ON accounting.transaction_metric_buckets FOR SELECT TO accounting_app
 USING(firm_id=accounting.actor_firm() AND accounting.company_manager(company_id));

CREATE FUNCTION accounting.save_transaction_bucket(
 target_firm TEXT,target_company TEXT,target_stream TEXT,target_minute BIGINT,
 target_count INTEGER,target_writes INTEGER,target_rollbacks INTEGER,
 target_connection_sum DOUBLE PRECISION,target_lock_sum DOUBLE PRECISION,target_transaction_sum DOUBLE PRECISION,
 target_connection_max DOUBLE PRECISION,target_lock_max DOUBLE PRECISION,target_transaction_max DOUBLE PRECISION
) RETURNS BOOLEAN LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE current_minute BIGINT:=floor(extract(epoch FROM clock_timestamp())/60)::bigint;
 BEGIN
 IF target_firm IS DISTINCT FROM accounting.actor_firm() OR NOT accounting.actor_active()
  OR NOT accounting.company_visible(target_company) THEN
  RAISE EXCEPTION 'Current company authorization required' USING ERRCODE='42501';
 END IF;
 IF target_minute IS NULL OR target_minute NOT BETWEEN current_minute-15 AND current_minute+1 THEN RETURN false;END IF;
 IF target_stream IS NULL OR length(target_stream) NOT BETWEEN 1 AND 80 THEN
  RAISE EXCEPTION 'Invalid telemetry stream' USING ERRCODE='22023';
 END IF;
 INSERT INTO accounting.transaction_metric_buckets VALUES(
  target_firm,target_company,target_stream,target_minute,target_count,target_writes,target_rollbacks,
  target_connection_sum,target_lock_sum,target_transaction_sum,target_connection_max,target_lock_max,target_transaction_max
 ) ON CONFLICT(firm_id,company_id,stream_id,bucket_minute) DO UPDATE SET
  tx_count=excluded.tx_count,write_count=excluded.write_count,rollback_count=excluded.rollback_count,
  connection_sum=excluded.connection_sum,lock_sum=excluded.lock_sum,transaction_sum=excluded.transaction_sum,
  connection_max=excluded.connection_max,lock_max=excluded.lock_max,transaction_max=excluded.transaction_max
 WHERE excluded.tx_count>=accounting.transaction_metric_buckets.tx_count;
 -- An indexed, bounded sweep also expires buckets of terminated/idle processes.
 DELETE FROM accounting.transaction_metric_buckets WHERE (firm_id,company_id,stream_id,bucket_minute) IN(
  SELECT firm_id,company_id,stream_id,bucket_minute FROM accounting.transaction_metric_buckets
  WHERE bucket_minute<current_minute-16 ORDER BY bucket_minute LIMIT 256
 );
 RETURN true;
 END
$$;
REVOKE ALL ON FUNCTION accounting.save_transaction_bucket(TEXT,TEXT,TEXT,BIGINT,INTEGER,INTEGER,INTEGER,DOUBLE PRECISION,DOUBLE PRECISION,DOUBLE PRECISION,DOUBLE PRECISION,DOUBLE PRECISION,DOUBLE PRECISION) FROM PUBLIC,accounting_worker,accounting_system;
GRANT EXECUTE ON FUNCTION accounting.save_transaction_bucket(TEXT,TEXT,TEXT,BIGINT,INTEGER,INTEGER,INTEGER,DOUBLE PRECISION,DOUBLE PRECISION,DOUBLE PRECISION,DOUBLE PRECISION,DOUBLE PRECISION,DOUBLE PRECISION) TO accounting_app;

-- Company/window lookup avoids loading or scanning complete queue history.
CREATE INDEX idx_telemetry_ocr_attempts ON accounting.ocr_attempts(firm_id,company_id,created_at DESC,id DESC);
CREATE INDEX idx_telemetry_proposal_attempts ON accounting.proposal_attempts(firm_id,company_id,created_at DESC,id DESC);
CREATE INDEX idx_telemetry_assistant_turns ON accounting.assistant_turns(firm_id,company_id,created_at DESC,id DESC);
CREATE INDEX idx_telemetry_migration_jobs ON accounting.migration_jobs(firm_id,company_id,created_at DESC,id DESC);

-- Return bounded timing/status evidence, never employee payloads or documents.
CREATE FUNCTION accounting.company_runtime_samples(target_company TEXT,target_since TEXT,target_limit INTEGER)
 RETURNS JSONB LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE result JSONB;
 BEGIN
 IF NOT accounting.actor_active() OR NOT accounting.company_manager(target_company)
  OR NOT EXISTS(SELECT 1 FROM accounting.firm_clients WHERE company_id=target_company AND firm_id=accounting.actor_firm() AND status='active') THEN
  RAISE EXCEPTION 'Current company manager required' USING ERRCODE='42501';
 END IF;
 IF target_limit IS NULL OR target_limit NOT BETWEEN 1 AND 500 OR target_since IS NULL
  OR target_since::timestamptz<now()-interval '16 minutes' OR target_since::timestamptz>now() THEN
  RAISE EXCEPTION 'Bounded runtime window required' USING ERRCODE='22023';
 END IF;
 SELECT coalesce(jsonb_agg(to_jsonb(sample)),'[]'::jsonb) INTO result FROM (
  (SELECT 'ocr' AS kind,a.attempt_no,a.status,a.created_at AS started_at,a.finished_at,j.created_at AS queued_at
   FROM accounting.ocr_attempts a JOIN accounting.ocr_jobs j ON j.id=a.job_id
   WHERE a.firm_id=accounting.actor_firm() AND a.company_id=target_company AND a.created_at>=target_since
   ORDER BY a.created_at DESC,a.id DESC LIMIT target_limit)
  UNION ALL
  (SELECT 'proposal' AS kind,a.attempt_no,a.status,a.created_at AS started_at,a.finished_at,j.created_at AS queued_at
   FROM accounting.proposal_attempts a JOIN accounting.proposal_jobs j ON j.id=a.job_id
   WHERE a.firm_id=accounting.actor_firm() AND a.company_id=target_company AND a.created_at>=target_since
   ORDER BY a.created_at DESC,a.id DESC LIMIT target_limit)
 ) sample;
 RETURN result;
 END
$$;
REVOKE ALL ON FUNCTION accounting.company_runtime_samples(TEXT,TEXT,INTEGER) FROM PUBLIC,accounting_worker,accounting_system;
GRANT EXECUTE ON FUNCTION accounting.company_runtime_samples(TEXT,TEXT,INTEGER) TO accounting_app;
