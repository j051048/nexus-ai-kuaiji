-- A durable queue. Only a separately provisioned worker may dispatch across
-- companies, through bounded SECURITY DEFINER operations, never table SELECT.
DO $$ BEGIN
 IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='accounting_worker') THEN
  CREATE ROLE accounting_worker NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
 END IF;
 IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='accounting_worker' AND (rolsuper OR rolbypassrls OR rolcanlogin)) THEN
  RAISE EXCEPTION 'accounting_worker must be NOLOGIN NOSUPERUSER NOBYPASSRLS';
 END IF;
END $$;
GRANT USAGE ON SCHEMA accounting TO accounting_worker;
CREATE UNIQUE INDEX idx_ai_run_queue_scope ON accounting.ai_runs(id,event_id,firm_id,company_id,attachment_id);

CREATE TABLE accounting.ocr_batches (
 id TEXT PRIMARY KEY,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,
 created_by TEXT NOT NULL REFERENCES accounting.users(id),idempotency_key TEXT NOT NULL,
 request_fingerprint TEXT NOT NULL,created_at TEXT NOT NULL,
 UNIQUE(firm_id,created_by,idempotency_key),UNIQUE(id,firm_id,company_id),
 FOREIGN KEY(firm_id,company_id) REFERENCES accounting.firm_clients(firm_id,company_id)
);
CREATE TABLE accounting.ocr_jobs (
 id TEXT PRIMARY KEY,batch_id TEXT NOT NULL,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,
 event_id TEXT NOT NULL,attachment_id TEXT NOT NULL,expected_version INTEGER NOT NULL CHECK(expected_version>0),
 attachment_sha256 TEXT NOT NULL,configuration_fingerprint TEXT NOT NULL,
 execution_user_id TEXT NOT NULL REFERENCES accounting.users(id),created_by TEXT NOT NULL REFERENCES accounting.users(id),created_at TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'queued' CHECK(status IN ('queued','running','retry_wait','succeeded','failed','interrupted','stale','cancelled')),
 attempt_count INTEGER NOT NULL DEFAULT 0 CHECK(attempt_count>=0),max_attempts INTEGER NOT NULL DEFAULT 3 CHECK(max_attempts BETWEEN 1 AND 30 AND attempt_count<=max_attempts),
 available_at TEXT NOT NULL,lease_token TEXT,lease_until TEXT,worker_id TEXT,current_run_id TEXT,
 error_code TEXT NOT NULL DEFAULT '' CHECK(length(error_code)<=120),error_detail TEXT NOT NULL DEFAULT '' CHECK(length(error_detail)<=1000),
 updated_at TEXT NOT NULL,finished_at TEXT,
 UNIQUE(id,event_id,firm_id,company_id,attachment_id),
 FOREIGN KEY(batch_id,firm_id,company_id) REFERENCES accounting.ocr_batches(id,firm_id,company_id),
 FOREIGN KEY(event_id,firm_id,company_id) REFERENCES accounting.business_events(id,firm_id,company_id),
 FOREIGN KEY(attachment_id,event_id) REFERENCES accounting.business_attachments(id,event_id),
 FOREIGN KEY(current_run_id,event_id,firm_id,company_id,attachment_id) REFERENCES accounting.ai_runs(id,event_id,firm_id,company_id,attachment_id),
 CHECK((status='running' AND lease_token IS NOT NULL AND lease_until IS NOT NULL AND worker_id IS NOT NULL AND finished_at IS NULL)
  OR (status IN ('queued','retry_wait') AND lease_token IS NULL AND lease_until IS NULL AND worker_id IS NULL AND finished_at IS NULL)
  OR (status IN ('succeeded','failed','interrupted','stale','cancelled') AND lease_token IS NULL AND lease_until IS NULL AND worker_id IS NULL AND finished_at IS NOT NULL))
);
CREATE UNIQUE INDEX idx_ocr_active_attachment ON accounting.ocr_jobs(event_id,attachment_id) WHERE status IN ('queued','running','retry_wait');
CREATE INDEX idx_ocr_jobs_dispatch ON accounting.ocr_jobs(status,available_at,firm_id,company_id,created_at,id);
CREATE INDEX idx_ocr_jobs_batch ON accounting.ocr_jobs(batch_id,created_at,id);
CREATE TABLE accounting.ocr_attempts (
 id TEXT PRIMARY KEY,job_id TEXT NOT NULL,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,event_id TEXT NOT NULL,attachment_id TEXT NOT NULL,
 attempt_no INTEGER NOT NULL CHECK(attempt_no>0),lease_token TEXT NOT NULL,
 execution_user_id TEXT NOT NULL REFERENCES accounting.users(id),input_version INTEGER NOT NULL CHECK(input_version>0),
 attachment_sha256 TEXT NOT NULL,configuration_fingerprint TEXT NOT NULL,
 phase TEXT NOT NULL DEFAULT 'claimed' CHECK(phase IN ('claimed','sending','finished')),
 status TEXT NOT NULL DEFAULT 'running' CHECK(status IN ('running','succeeded','failed','interrupted','stale','cancelled')),
 ai_run_id TEXT,error_code TEXT NOT NULL DEFAULT '' CHECK(length(error_code)<=120),error_detail TEXT NOT NULL DEFAULT '' CHECK(length(error_detail)<=1000),
 created_at TEXT NOT NULL,finished_at TEXT,
 UNIQUE(job_id,attempt_no),UNIQUE(job_id,lease_token),
 FOREIGN KEY(job_id,event_id,firm_id,company_id,attachment_id) REFERENCES accounting.ocr_jobs(id,event_id,firm_id,company_id,attachment_id),
 FOREIGN KEY(ai_run_id,event_id,firm_id,company_id,attachment_id) REFERENCES accounting.ai_runs(id,event_id,firm_id,company_id,attachment_id),
 CHECK((status='running' AND phase IN ('claimed','sending') AND finished_at IS NULL)
  OR (status<>'running' AND phase='finished' AND finished_at IS NOT NULL))
);
CREATE TABLE accounting.ocr_retries (
 job_id TEXT NOT NULL,request_key TEXT NOT NULL,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,event_id TEXT NOT NULL,
 requested_by TEXT NOT NULL REFERENCES accounting.users(id),created_at TEXT NOT NULL,
 PRIMARY KEY(job_id,request_key),
 FOREIGN KEY(job_id) REFERENCES accounting.ocr_jobs(id),
 FOREIGN KEY(event_id,firm_id,company_id) REFERENCES accounting.business_events(id,firm_id,company_id)
);
CREATE TABLE accounting.ocr_company_schedule (
 firm_id TEXT NOT NULL,company_id TEXT NOT NULL,last_dispatch_seq BIGINT NOT NULL DEFAULT 0 CHECK(last_dispatch_seq>=0),
 PRIMARY KEY(firm_id,company_id),FOREIGN KEY(firm_id,company_id) REFERENCES accounting.firm_clients(firm_id,company_id)
);

CREATE FUNCTION accounting.ocr_process_scope(target_event TEXT,target_firm TEXT,target_company TEXT) RETURNS BOOLEAN
 LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 SELECT accounting.ai_event_processor(target_event,target_firm,target_company)
 AND accounting.ai_event_open(target_event,target_firm,target_company)
 AND EXISTS(SELECT 1 FROM accounting.ai_company_settings s WHERE s.firm_id=target_firm AND s.company_id=target_company AND s.enabled=1)
$$;
CREATE FUNCTION accounting.ocr_batch_visible(target_batch TEXT) RETURNS BOOLEAN
 LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 SELECT EXISTS(SELECT 1 FROM accounting.ocr_jobs j WHERE j.batch_id=target_batch AND accounting.business_event_visible(j.event_id,j.firm_id,j.company_id))
$$;
CREATE FUNCTION accounting.ocr_retry_scope(target_job TEXT,target_event TEXT,target_firm TEXT,target_company TEXT) RETURNS BOOLEAN
 LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 SELECT EXISTS(SELECT 1 FROM accounting.ocr_jobs j WHERE j.id=target_job AND j.event_id=target_event AND j.firm_id=target_firm AND j.company_id=target_company)
 AND accounting.ocr_process_scope(target_event,target_firm,target_company)
$$;
CREATE FUNCTION accounting.ocr_job_update_guard() RETURNS TRIGGER LANGUAGE plpgsql SET search_path=pg_catalog,accounting,pg_temp AS $$
 BEGIN
  IF (NEW.id,NEW.batch_id,NEW.firm_id,NEW.company_id,NEW.event_id,NEW.attachment_id,NEW.created_by,NEW.created_at)
   IS DISTINCT FROM (OLD.id,OLD.batch_id,OLD.firm_id,OLD.company_id,OLD.event_id,OLD.attachment_id,OLD.created_by,OLD.created_at) THEN
   RAISE EXCEPTION 'Queue job scope and provenance are immutable' USING ERRCODE='23514';
  END IF;
  IF current_user='accounting_app' AND (
   OLD.status NOT IN ('failed','interrupted','stale','cancelled') OR NEW.status<>'queued'
   OR NEW.execution_user_id<>accounting.actor_user() OR NEW.attempt_count<>OLD.attempt_count
   OR NEW.max_attempts<OLD.max_attempts OR NEW.max_attempts>OLD.attempt_count+3
   OR NEW.current_run_id IS NOT NULL OR NEW.lease_token IS NOT NULL OR NEW.lease_until IS NOT NULL OR NEW.worker_id IS NOT NULL
   OR NEW.error_code<>'' OR NEW.error_detail<>'' OR NEW.finished_at IS NOT NULL) THEN
   RAISE EXCEPTION 'API may only request a bounded manual retry' USING ERRCODE='23514';
  END IF;
  RETURN NEW;
 END
$$;
CREATE TRIGGER ocr_job_identity BEFORE UPDATE ON accounting.ocr_jobs FOR EACH ROW EXECUTE FUNCTION accounting.ocr_job_update_guard();
CREATE TRIGGER ocr_jobs_no_delete BEFORE DELETE ON accounting.ocr_jobs FOR EACH ROW EXECUTE FUNCTION accounting.ai_immutable_guard();
CREATE TRIGGER ocr_batches_immutable BEFORE UPDATE OR DELETE ON accounting.ocr_batches FOR EACH ROW EXECUTE FUNCTION accounting.ai_immutable_guard();
CREATE TRIGGER ocr_retries_immutable BEFORE UPDATE OR DELETE ON accounting.ocr_retries FOR EACH ROW EXECUTE FUNCTION accounting.ai_immutable_guard();
CREATE FUNCTION accounting.ocr_attempt_update_guard() RETURNS TRIGGER LANGUAGE plpgsql SET search_path=pg_catalog,accounting,pg_temp AS $$
 BEGIN
  IF OLD.status<>'running' OR
   (NEW.id,NEW.job_id,NEW.firm_id,NEW.company_id,NEW.event_id,NEW.attachment_id,NEW.attempt_no,NEW.lease_token,
    NEW.execution_user_id,NEW.input_version,NEW.attachment_sha256,NEW.configuration_fingerprint,NEW.created_at)
   IS DISTINCT FROM (OLD.id,OLD.job_id,OLD.firm_id,OLD.company_id,OLD.event_id,OLD.attachment_id,OLD.attempt_no,OLD.lease_token,
    OLD.execution_user_id,OLD.input_version,OLD.attachment_sha256,OLD.configuration_fingerprint,OLD.created_at)
   OR (OLD.ai_run_id IS NOT NULL AND NEW.ai_run_id IS DISTINCT FROM OLD.ai_run_id)
   OR (OLD.phase='sending' AND NEW.phase='claimed') THEN
   RAISE EXCEPTION 'Queue attempt history is immutable' USING ERRCODE='23514';
  END IF;
  RETURN NEW;
 END
$$;
CREATE TRIGGER ocr_attempt_identity BEFORE UPDATE ON accounting.ocr_attempts FOR EACH ROW EXECUTE FUNCTION accounting.ocr_attempt_update_guard();
CREATE TRIGGER ocr_attempts_no_delete BEFORE DELETE ON accounting.ocr_attempts FOR EACH ROW EXECUTE FUNCTION accounting.ai_immutable_guard();

DO $$ DECLARE item TEXT; BEGIN
 FOREACH item IN ARRAY ARRAY['ocr_batches','ocr_jobs','ocr_attempts','ocr_retries','ocr_company_schedule'] LOOP
  EXECUTE format('ALTER TABLE accounting.%I ENABLE ROW LEVEL SECURITY',item);
  EXECUTE format('ALTER TABLE accounting.%I FORCE ROW LEVEL SECURITY',item);
 END LOOP;
END $$;
GRANT SELECT,INSERT ON accounting.ocr_batches,accounting.ocr_retries TO accounting_app;
GRANT SELECT,INSERT,UPDATE ON accounting.ocr_jobs TO accounting_app;
GRANT SELECT ON accounting.ocr_attempts TO accounting_app;
CREATE POLICY ocr_batches_read ON accounting.ocr_batches FOR SELECT TO accounting_app USING(accounting.ocr_batch_visible(id));
CREATE POLICY ocr_batches_create ON accounting.ocr_batches FOR INSERT TO accounting_app WITH CHECK(created_by=accounting.actor_user() AND accounting.ai_company_visible(firm_id,company_id));
CREATE POLICY ocr_jobs_read ON accounting.ocr_jobs FOR SELECT TO accounting_app USING(accounting.business_event_visible(event_id,firm_id,company_id));
CREATE POLICY ocr_jobs_create ON accounting.ocr_jobs FOR INSERT TO accounting_app
 WITH CHECK(created_by=accounting.actor_user() AND execution_user_id=accounting.actor_user() AND status='queued' AND attempt_count=0 AND max_attempts=3
  AND current_run_id IS NULL AND error_code='' AND error_detail='' AND accounting.ocr_process_scope(event_id,firm_id,company_id)
  AND EXISTS(SELECT 1 FROM accounting.ocr_batches b WHERE b.id=batch_id AND b.created_by=accounting.actor_user()));
-- The batch SELECT policy cannot see a not-yet-inserted first job. Use a fixed
-- owner helper for batch provenance instead of weakening batch visibility.
CREATE FUNCTION accounting.ocr_batch_creator(target_batch TEXT) RETURNS BOOLEAN
 LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 SELECT EXISTS(SELECT 1 FROM accounting.ocr_batches b WHERE b.id=target_batch AND b.created_by=accounting.actor_user() AND b.firm_id=accounting.actor_firm())
$$;
ALTER POLICY ocr_jobs_create ON accounting.ocr_jobs WITH CHECK(created_by=accounting.actor_user() AND execution_user_id=accounting.actor_user()
 AND status='queued' AND attempt_count=0 AND max_attempts=3 AND current_run_id IS NULL AND error_code='' AND error_detail=''
 AND accounting.ocr_process_scope(event_id,firm_id,company_id) AND accounting.ocr_batch_creator(batch_id));
CREATE POLICY ocr_jobs_retry ON accounting.ocr_jobs FOR UPDATE TO accounting_app
 USING(status IN ('failed','interrupted','stale','cancelled') AND accounting.ocr_process_scope(event_id,firm_id,company_id))
 WITH CHECK(execution_user_id=accounting.actor_user() AND status='queued' AND accounting.ocr_process_scope(event_id,firm_id,company_id));
CREATE POLICY ocr_attempts_read ON accounting.ocr_attempts FOR SELECT TO accounting_app USING(accounting.business_event_visible(event_id,firm_id,company_id));
CREATE POLICY ocr_retries_read ON accounting.ocr_retries FOR SELECT TO accounting_app USING(accounting.business_event_visible(event_id,firm_id,company_id));
CREATE POLICY ocr_retries_create ON accounting.ocr_retries FOR INSERT TO accounting_app
 WITH CHECK(requested_by=accounting.actor_user() AND accounting.ocr_retry_scope(job_id,event_id,firm_id,company_id));

CREATE FUNCTION accounting.ocr_utc_now() RETURNS TEXT LANGUAGE sql VOLATILE SET search_path=pg_catalog AS $$
 SELECT to_char(clock_timestamp() AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"+00:00"')
$$;
CREATE FUNCTION accounting.recover_ocr_jobs() RETURNS INTEGER LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE j accounting.ocr_jobs; a accounting.ocr_attempts; r accounting.ai_runs; outcome TEXT; stamp TEXT; recovered INTEGER:=0;
 BEGIN
  PERFORM pg_advisory_xact_lock(6420031786281903101);
  stamp:=accounting.ocr_utc_now();
  FOR j IN SELECT * FROM accounting.ocr_jobs WHERE status='running' AND lease_until::timestamptz<=clock_timestamp() ORDER BY id FOR UPDATE LOOP
   SELECT * INTO a FROM accounting.ocr_attempts WHERE job_id=j.id AND lease_token=j.lease_token;
   SELECT * INTO r FROM accounting.ai_runs WHERE id=j.current_run_id;
   IF r.status IN ('succeeded','failed','stale') THEN outcome:=r.status;
   ELSIF a.phase='sending' THEN outcome:='interrupted';
   ELSIF j.attempt_count<j.max_attempts THEN outcome:='retry_wait';
   ELSE outcome:='failed'; END IF;
   IF r.status='running' THEN
    UPDATE accounting.ai_runs SET status='stale',result_json=NULL,finished_at=stamp,error_code='worker_lease_expired',error_detail='' WHERE id=r.id AND status='running';
   END IF;
   UPDATE accounting.ocr_attempts SET phase='finished',status=CASE WHEN outcome='retry_wait' THEN 'interrupted' ELSE outcome END,
    finished_at=stamp,error_code=CASE WHEN r.status IN ('succeeded','failed','stale') THEN r.error_code ELSE 'worker_lease_expired' END,
    error_detail=CASE WHEN r.status IN ('succeeded','failed','stale') THEN r.error_detail ELSE '工作进程中断；已保留尝试记录' END
    WHERE id=a.id AND status='running';
   UPDATE accounting.ocr_jobs SET status=outcome,lease_token=NULL,lease_until=NULL,worker_id=NULL,updated_at=stamp,available_at=stamp,
    finished_at=CASE WHEN outcome='retry_wait' THEN NULL ELSE stamp END,
    error_code=CASE WHEN r.status IN ('succeeded','failed','stale') THEN r.error_code ELSE 'worker_lease_expired' END,
    error_detail=CASE WHEN r.status IN ('succeeded','failed','stale') THEN r.error_detail ELSE '工作进程中断；发送后的未确认任务须人工重试' END
    WHERE id=j.id;
   recovered:=recovered+1;
  END LOOP;
  RETURN recovered;
 END
$$;
CREATE FUNCTION accounting.claim_ocr_job(target_worker TEXT,lease_seconds INTEGER DEFAULT 180,global_limit INTEGER DEFAULT 2)
 RETURNS SETOF accounting.ocr_jobs LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE j accounting.ocr_jobs; token TEXT; stamp TEXT; seq BIGINT;
 BEGIN
  IF length(btrim(target_worker)) NOT BETWEEN 1 AND 120 OR lease_seconds NOT BETWEEN 30 AND 600 OR global_limit NOT BETWEEN 1 AND 2 THEN
   RAISE EXCEPTION 'Invalid queue worker bounds' USING ERRCODE='22023';
  END IF;
  PERFORM pg_advisory_xact_lock(6420031786281903101);
  PERFORM accounting.recover_ocr_jobs();
  IF (SELECT count(*) FROM accounting.ocr_jobs WHERE status='running')>=global_limit THEN RETURN; END IF;
  INSERT INTO accounting.ocr_company_schedule(firm_id,company_id)
   SELECT DISTINCT firm_id,company_id FROM accounting.ocr_jobs WHERE status IN ('queued','retry_wait') ON CONFLICT DO NOTHING;
  SELECT q.* INTO j FROM accounting.ocr_jobs q JOIN accounting.ocr_company_schedule s USING(firm_id,company_id)
   WHERE q.status IN ('queued','retry_wait') AND q.available_at::timestamptz<=clock_timestamp() AND q.attempt_count<q.max_attempts
   AND NOT EXISTS(SELECT 1 FROM accounting.ocr_jobs busy WHERE busy.firm_id=q.firm_id AND busy.company_id=q.company_id AND busy.status='running')
   ORDER BY s.last_dispatch_seq,q.created_at,q.id LIMIT 1 FOR UPDATE OF q;
  IF j.id IS NULL THEN RETURN; END IF;
  stamp:=accounting.ocr_utc_now();token:=md5(random()::text||clock_timestamp()::text||j.id||target_worker);
  SELECT coalesce(max(last_dispatch_seq),0)+1 INTO seq FROM accounting.ocr_company_schedule;
  UPDATE accounting.ocr_company_schedule SET last_dispatch_seq=seq WHERE firm_id=j.firm_id AND company_id=j.company_id;
  UPDATE accounting.ocr_jobs SET status='running',attempt_count=attempt_count+1,lease_token=token,
   lease_until=to_char((clock_timestamp()+make_interval(secs=>lease_seconds)) AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"+00:00"'),
   worker_id=target_worker,current_run_id=NULL,error_code='',error_detail='',updated_at=stamp WHERE id=j.id RETURNING * INTO j;
  INSERT INTO accounting.ocr_attempts(id,job_id,firm_id,company_id,event_id,attachment_id,attempt_no,lease_token,execution_user_id,input_version,attachment_sha256,configuration_fingerprint,created_at)
   VALUES(md5(token||j.id),j.id,j.firm_id,j.company_id,j.event_id,j.attachment_id,j.attempt_count,token,j.execution_user_id,j.expected_version,j.attachment_sha256,j.configuration_fingerprint,stamp);
  RETURN NEXT j;
 END
$$;
CREATE FUNCTION accounting.worker_check_ocr_lease(target_job TEXT,target_token TEXT) RETURNS BOOLEAN
 LANGUAGE sql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 SELECT EXISTS(SELECT 1 FROM accounting.ocr_jobs WHERE id=target_job AND lease_token=target_token AND status='running' AND lease_until::timestamptz>clock_timestamp())
$$;
CREATE FUNCTION accounting.worker_bind_ocr_run(target_job TEXT,target_token TEXT,target_run TEXT) RETURNS BOOLEAN
 LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE j accounting.ocr_jobs;
 BEGIN
  PERFORM pg_advisory_xact_lock(6420031786281903101);
  SELECT * INTO j FROM accounting.ocr_jobs WHERE id=target_job AND lease_token=target_token AND status='running' AND lease_until::timestamptz>clock_timestamp() FOR UPDATE;
  IF j.id IS NULL THEN RETURN false; END IF;
  IF j.current_run_id IS NOT NULL THEN RETURN j.current_run_id=target_run; END IF;
  IF NOT EXISTS(SELECT 1 FROM accounting.ai_runs r WHERE r.id=target_run AND r.kind='ocr' AND r.status='running'
   AND r.event_id=j.event_id AND r.firm_id=j.firm_id AND r.company_id=j.company_id AND r.attachment_id=j.attachment_id
   AND r.created_by=j.execution_user_id AND r.input_version=j.expected_version) THEN RETURN false; END IF;
  UPDATE accounting.ocr_jobs SET current_run_id=target_run,updated_at=accounting.ocr_utc_now() WHERE id=j.id;
  UPDATE accounting.ocr_attempts SET ai_run_id=target_run WHERE job_id=j.id AND lease_token=target_token AND phase='claimed' AND status='running';
  RETURN true;
 END
$$;
CREATE FUNCTION accounting.worker_mark_ocr_sending(target_job TEXT,target_token TEXT) RETURNS BOOLEAN
 LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 BEGIN
  PERFORM pg_advisory_xact_lock(6420031786281903101);
  IF NOT accounting.worker_check_ocr_lease(target_job,target_token) THEN RETURN false; END IF;
  IF NOT EXISTS(SELECT 1 FROM accounting.ocr_jobs j JOIN accounting.ai_runs r ON r.id=j.current_run_id WHERE j.id=target_job AND r.status='running') THEN RETURN false; END IF;
  UPDATE accounting.ocr_attempts SET phase='sending' WHERE job_id=target_job AND lease_token=target_token AND status='running' AND ai_run_id IS NOT NULL;
  RETURN FOUND;
 END
$$;
CREATE FUNCTION accounting.worker_renew_ocr_lease(target_job TEXT,target_token TEXT,lease_seconds INTEGER DEFAULT 180) RETURNS BOOLEAN
 LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 BEGIN
  IF lease_seconds NOT BETWEEN 30 AND 600 THEN RAISE EXCEPTION 'Invalid lease bound' USING ERRCODE='22023'; END IF;
  PERFORM pg_advisory_xact_lock(6420031786281903101);
  UPDATE accounting.ocr_jobs SET lease_until=to_char((clock_timestamp()+make_interval(secs=>lease_seconds)) AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"+00:00"'),updated_at=accounting.ocr_utc_now()
   WHERE id=target_job AND lease_token=target_token AND status='running' AND lease_until::timestamptz>clock_timestamp();
  RETURN FOUND;
 END
$$;
CREATE FUNCTION accounting.worker_finish_ocr_job(target_job TEXT,target_token TEXT,target_status TEXT,target_error_code TEXT DEFAULT '',target_error_detail TEXT DEFAULT '',target_run TEXT DEFAULT NULL,retry_delay_seconds INTEGER DEFAULT 0) RETURNS BOOLEAN
 LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE j accounting.ocr_jobs; stamp TEXT; outcome TEXT;
 BEGIN
  IF target_status NOT IN ('succeeded','failed','interrupted','stale','cancelled','retry_wait') OR retry_delay_seconds NOT BETWEEN 0 AND 3600
   OR length(target_error_code)>120 OR length(target_error_detail)>1000 THEN RAISE EXCEPTION 'Invalid finish bounds' USING ERRCODE='22023'; END IF;
  PERFORM pg_advisory_xact_lock(6420031786281903101);
  SELECT * INTO j FROM accounting.ocr_jobs WHERE id=target_job AND lease_token=target_token AND status='running' AND lease_until::timestamptz>clock_timestamp() FOR UPDATE;
  IF j.id IS NULL THEN RETURN false; END IF;
  IF target_run IS NOT NULL AND target_run IS DISTINCT FROM j.current_run_id THEN RETURN false; END IF;
  IF target_status='succeeded' AND NOT EXISTS(SELECT 1 FROM accounting.ai_runs WHERE id=j.current_run_id AND status='succeeded') THEN RETURN false; END IF;
  stamp:=accounting.ocr_utc_now();outcome:=CASE WHEN target_status='retry_wait' AND j.attempt_count>=j.max_attempts THEN 'failed' ELSE target_status END;
  UPDATE accounting.ocr_attempts SET phase='finished',status=CASE WHEN outcome='retry_wait' THEN 'failed' ELSE outcome END,
   error_code=target_error_code,error_detail=target_error_detail,finished_at=stamp WHERE job_id=j.id AND lease_token=target_token AND status='running';
  UPDATE accounting.ocr_jobs SET status=outcome,lease_token=NULL,lease_until=NULL,worker_id=NULL,updated_at=stamp,
   error_code=target_error_code,error_detail=target_error_detail,finished_at=CASE WHEN outcome='retry_wait' THEN NULL ELSE stamp END,
   available_at=to_char((clock_timestamp()+make_interval(secs=>retry_delay_seconds)) AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"+00:00"') WHERE id=j.id;
  RETURN true;
 END
$$;
CREATE FUNCTION accounting.cancel_ocr_job(target_job TEXT,reason TEXT) RETURNS BOOLEAN
 LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE j accounting.ocr_jobs; stamp TEXT;
 BEGIN
  IF length(btrim(reason)) NOT BETWEEN 1 AND 1000 THEN RAISE EXCEPTION 'Cancellation reason required' USING ERRCODE='22023'; END IF;
  PERFORM pg_advisory_xact_lock(6420031786281903101);
  SELECT * INTO j FROM accounting.ocr_jobs WHERE id=target_job AND accounting.ocr_process_scope(event_id,firm_id,company_id) FOR UPDATE;
  IF j.id IS NULL OR j.status NOT IN ('queued','running','retry_wait') THEN RETURN false; END IF;
  stamp:=accounting.ocr_utc_now();
  IF j.current_run_id IS NOT NULL THEN
   UPDATE accounting.ai_runs SET status='stale',result_json=NULL,finished_at=stamp,error_code='queue_cancelled',error_detail='任务已由有权限的人员取消'
    WHERE id=j.current_run_id AND status='running' AND created_by=j.execution_user_id AND event_id=j.event_id AND attachment_id=j.attachment_id;
  END IF;
  UPDATE accounting.ocr_attempts SET phase='finished',status='cancelled',finished_at=stamp,error_code='queue_cancelled',error_detail=reason
   WHERE job_id=j.id AND status='running';
  UPDATE accounting.ocr_jobs SET status='cancelled',lease_token=NULL,lease_until=NULL,worker_id=NULL,error_code='queue_cancelled',error_detail=reason,updated_at=stamp,finished_at=stamp WHERE id=j.id;
  RETURN true;
 END
$$;

REVOKE ALL ON FUNCTION accounting.ocr_process_scope(TEXT,TEXT,TEXT),accounting.ocr_batch_visible(TEXT),accounting.ocr_retry_scope(TEXT,TEXT,TEXT,TEXT),accounting.ocr_batch_creator(TEXT),
 accounting.ocr_job_update_guard(),accounting.ocr_attempt_update_guard(),accounting.ocr_utc_now(),accounting.recover_ocr_jobs(),accounting.claim_ocr_job(TEXT,INTEGER,INTEGER),
 accounting.worker_check_ocr_lease(TEXT,TEXT),accounting.worker_bind_ocr_run(TEXT,TEXT,TEXT),accounting.worker_mark_ocr_sending(TEXT,TEXT),accounting.worker_renew_ocr_lease(TEXT,TEXT,INTEGER),
 accounting.worker_finish_ocr_job(TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,INTEGER),accounting.cancel_ocr_job(TEXT,TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION accounting.ocr_process_scope(TEXT,TEXT,TEXT),accounting.ocr_batch_visible(TEXT),accounting.ocr_retry_scope(TEXT,TEXT,TEXT,TEXT),accounting.ocr_batch_creator(TEXT),accounting.cancel_ocr_job(TEXT,TEXT) TO accounting_app;
GRANT EXECUTE ON FUNCTION accounting.recover_ocr_jobs(),accounting.claim_ocr_job(TEXT,INTEGER,INTEGER),accounting.worker_check_ocr_lease(TEXT,TEXT),accounting.worker_bind_ocr_run(TEXT,TEXT,TEXT),
 accounting.worker_mark_ocr_sending(TEXT,TEXT),accounting.worker_renew_ocr_lease(TEXT,TEXT,INTEGER),accounting.worker_finish_ocr_job(TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,INTEGER) TO accounting_worker;
