-- Dialogue liveness is distinct from OCR/journal workers. No private messages
-- or worker identifiers are exposed by the authorized company aggregate.
CREATE TABLE accounting.assistant_worker_heartbeats(
 worker_id TEXT PRIMARY KEY CHECK(length(btrim(worker_id)) BETWEEN 1 AND 120),
 started_at TEXT NOT NULL,heartbeat_at TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('active','idle','stopped')),
 inflight_count INTEGER NOT NULL CHECK(inflight_count BETWEEN 0 AND 2),
 CHECK(state='active' OR inflight_count=0)
);
ALTER TABLE accounting.assistant_worker_heartbeats ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.assistant_worker_heartbeats FORCE ROW LEVEL SECURITY;
REVOKE ALL ON accounting.assistant_worker_heartbeats FROM PUBLIC,accounting_app,accounting_worker;
CREATE FUNCTION accounting.record_assistant_heartbeat(target_worker TEXT,target_state TEXT,target_count INTEGER) RETURNS BOOLEAN
 LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE stamp TEXT;BEGIN
 IF NOT accounting.migration_worker_login() THEN RAISE EXCEPTION 'Dedicated worker login required' USING ERRCODE='42501';END IF;
 IF target_worker IS NULL OR target_state IS NULL OR target_count IS NULL OR length(btrim(target_worker)) NOT BETWEEN 1 AND 120
 OR target_state NOT IN ('active','idle','stopped') OR target_count NOT BETWEEN 0 AND 2 OR (target_state<>'active' AND target_count<>0) THEN RETURN false;END IF;
 stamp:=accounting.ocr_utc_now();
 INSERT INTO accounting.assistant_worker_heartbeats(worker_id,started_at,heartbeat_at,state,inflight_count) VALUES(target_worker,stamp,stamp,target_state,target_count)
 ON CONFLICT(worker_id) DO UPDATE SET started_at=CASE WHEN assistant_worker_heartbeats.state='stopped' AND excluded.state<>'stopped' THEN excluded.started_at ELSE assistant_worker_heartbeats.started_at END,
 heartbeat_at=excluded.heartbeat_at,state=excluded.state,inflight_count=excluded.inflight_count;
 RETURN true;END $$;
CREATE TRIGGER ops_dirty_after_change AFTER INSERT OR UPDATE ON accounting.assistant_turns FOR EACH ROW EXECUTE FUNCTION accounting.ops_mark_changed();

ALTER FUNCTION accounting.ops_company_metrics_internal(TEXT,TEXT,TEXT) RENAME TO ops_company_metrics_before_assistant;
CREATE FUNCTION accounting.ops_company_metrics_internal(target_firm TEXT,target_company TEXT,target_period TEXT) RETURNS JSONB
 LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE m JSONB;q JSONB;w JSONB;chat_q JSONB;chat_w JSONB;window_minutes INTEGER;stale_seconds INTEGER;
 processing_pending BIGINT;chat_pending BIGINT;key TEXT;availability BOOLEAN;BEGIN
 m:=accounting.ops_company_metrics_before_assistant(target_firm,target_company,target_period);q:=m->'queue';w:=m->'worker';
 window_minutes:=(m->'policy'->>'failure_window_minutes')::integer;stale_seconds:=(m->'policy'->>'worker_stale_seconds')::integer;
 SELECT jsonb_build_object('queued',count(*) FILTER(WHERE status='queued'),'running',count(*) FILTER(WHERE status='running'),
 'retry_wait',0,'succeeded',count(*) FILTER(WHERE status='succeeded'),'failed',count(*) FILTER(WHERE status='failed'),
 'interrupted',count(*) FILTER(WHERE status='interrupted'),'stale',count(*) FILTER(WHERE status='stale'),'cancelled',count(*) FILTER(WHERE status='cancelled'),
 'recent_failures',count(*) FILTER(WHERE status IN ('failed','interrupted') AND finished_at::timestamptz>=now()-make_interval(mins=>window_minutes)),
 'oldest_pending_at',min(created_at) FILTER(WHERE status='queued'),
 'risk_fingerprint',md5(coalesce(string_agg(id||':'||status||':'||attempt_count::text||':'||coalesce(finished_at,''),',' ORDER BY id),''))) INTO chat_q
 FROM accounting.assistant_turns WHERE firm_id=target_firm AND company_id=target_company;
 SELECT jsonb_build_object('available',coalesce(bool_or(state IN ('active','idle') AND heartbeat_at::timestamptz>=now()-make_interval(secs=>stale_seconds)),false),
 'last_heartbeat_at',max(heartbeat_at)) INTO chat_w FROM accounting.assistant_worker_heartbeats;
 processing_pending:=(q->>'queued')::bigint+(q->>'retry_wait')::bigint+(q->>'running')::bigint;
 chat_pending:=(chat_q->>'queued')::bigint+(chat_q->>'running')::bigint;
 availability:=(processing_pending=0 OR (w->>'available')::boolean) AND (chat_pending=0 OR (chat_w->>'available')::boolean)
 AND (processing_pending+chat_pending>0 OR (w->>'available')::boolean OR (chat_w->>'available')::boolean);
 w:=w||jsonb_build_object('processing_available',(w->>'available')::boolean,'assistant_available',(chat_w->>'available')::boolean,'available',availability,
 'last_heartbeat_at',greatest(w->>'last_heartbeat_at',chat_w->>'last_heartbeat_at'));
 q:=q||jsonb_build_object('oldest_pending_at',least(q->>'oldest_pending_at',chat_q->>'oldest_pending_at'),
 'risk_fingerprint',md5((q->>'risk_fingerprint')||(chat_q->>'risk_fingerprint')));
 FOREACH key IN ARRAY ARRAY['queued','running','retry_wait','succeeded','failed','interrupted','stale','cancelled','recent_failures'] LOOP
 q:=jsonb_set(q,ARRAY[key],to_jsonb((q->>key)::bigint+(chat_q->>key)::bigint));END LOOP;
 RETURN m||jsonb_build_object('queue',q,'worker',w,'assistant',jsonb_build_object('queue',chat_q,'workers',chat_w));END $$;
REVOKE ALL ON FUNCTION accounting.record_assistant_heartbeat(TEXT,TEXT,INTEGER),accounting.ops_company_metrics_before_assistant(TEXT,TEXT,TEXT),accounting.ops_company_metrics_internal(TEXT,TEXT,TEXT) FROM PUBLIC,accounting_app,accounting_worker;
GRANT EXECUTE ON FUNCTION accounting.record_assistant_heartbeat(TEXT,TEXT,INTEGER) TO accounting_worker;
