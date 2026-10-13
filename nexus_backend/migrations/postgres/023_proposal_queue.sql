-- Independent durable proposal jobs; facts and workflow versions are distinct.
ALTER TABLE accounting.ocr_jobs ADD COLUMN expected_fact_version INTEGER CHECK(expected_fact_version>0);
CREATE UNIQUE INDEX idx_ai_run_proposal_scope ON accounting.ai_runs(id,event_id,firm_id,company_id,book_id,draft_id);
CREATE UNIQUE INDEX idx_business_mapping_proposal_scope ON accounting.business_mappings(event_id,firm_id,company_id,book_id,draft_id);

CREATE TABLE accounting.proposal_jobs (
 id TEXT PRIMARY KEY,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,event_id TEXT NOT NULL,
 book_id TEXT NOT NULL,draft_id TEXT NOT NULL,expected_fact_version INTEGER NOT NULL CHECK(expected_fact_version>0),
 expected_draft_version INTEGER NOT NULL CHECK(expected_draft_version>0),configuration_fingerprint TEXT NOT NULL,
 policy_fingerprint TEXT NOT NULL,idempotency_key TEXT NOT NULL,
 execution_user_id TEXT NOT NULL REFERENCES accounting.users(id),created_by TEXT NOT NULL REFERENCES accounting.users(id),created_at TEXT NOT NULL,
 generation INTEGER NOT NULL DEFAULT 1 CHECK(generation>0),
 status TEXT NOT NULL CHECK(status IN ('waiting','queued','running','retry_wait','succeeded','needs_attention','failed','interrupted','stale','cancelled')),
 attempt_count INTEGER NOT NULL DEFAULT 0 CHECK(attempt_count>=0),max_attempts INTEGER NOT NULL DEFAULT 3 CHECK(max_attempts BETWEEN 1 AND 30 AND attempt_count<=max_attempts),
 available_at TEXT NOT NULL,dependency_deadline TEXT NOT NULL,lease_token TEXT,lease_until TEXT,worker_id TEXT,current_run_id TEXT,
 result_template_id TEXT,result_template_version INTEGER,result_draft_version INTEGER,
 error_code TEXT NOT NULL DEFAULT '' CHECK(length(error_code)<=120),error_detail TEXT NOT NULL DEFAULT '' CHECK(length(error_detail)<=1000),
 updated_at TEXT NOT NULL,finished_at TEXT,
 UNIQUE(firm_id,idempotency_key),UNIQUE(id,event_id,firm_id,company_id,book_id,draft_id),
 FOREIGN KEY(event_id,firm_id,company_id,book_id,draft_id) REFERENCES accounting.business_mappings(event_id,firm_id,company_id,book_id,draft_id),
 FOREIGN KEY(current_run_id,event_id,firm_id,company_id,book_id,draft_id) REFERENCES accounting.ai_runs(id,event_id,firm_id,company_id,book_id,draft_id),
 FOREIGN KEY(result_template_id,result_template_version) REFERENCES accounting.journal_template_versions(template_id,version),
 CHECK((status='running' AND lease_token IS NOT NULL AND lease_until IS NOT NULL AND worker_id IS NOT NULL AND finished_at IS NULL)
  OR (status IN ('waiting','queued','retry_wait') AND lease_token IS NULL AND lease_until IS NULL AND worker_id IS NULL AND finished_at IS NULL)
  OR (status IN ('succeeded','needs_attention','failed','interrupted','stale','cancelled') AND lease_token IS NULL AND lease_until IS NULL AND worker_id IS NULL AND finished_at IS NOT NULL))
);
CREATE UNIQUE INDEX idx_proposal_active_draft ON accounting.proposal_jobs(draft_id) WHERE status IN ('waiting','queued','running','retry_wait');
CREATE INDEX idx_proposal_dispatch ON accounting.proposal_jobs(status,available_at,firm_id,company_id,created_at,id);
CREATE INDEX idx_proposal_book_page ON accounting.proposal_jobs(firm_id,book_id,created_at,id);
CREATE TABLE accounting.proposal_dependencies (
 proposal_job_id TEXT NOT NULL,generation INTEGER NOT NULL,ocr_job_id TEXT NOT NULL,attachment_id TEXT NOT NULL,
 event_id TEXT NOT NULL,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,book_id TEXT NOT NULL,draft_id TEXT NOT NULL,
 PRIMARY KEY(proposal_job_id,generation,ocr_job_id),
 FOREIGN KEY(proposal_job_id,event_id,firm_id,company_id,book_id,draft_id) REFERENCES accounting.proposal_jobs(id,event_id,firm_id,company_id,book_id,draft_id),
 FOREIGN KEY(ocr_job_id,event_id,firm_id,company_id,attachment_id) REFERENCES accounting.ocr_jobs(id,event_id,firm_id,company_id,attachment_id)
);
CREATE TABLE accounting.proposal_attempts (
 id TEXT PRIMARY KEY,job_id TEXT NOT NULL,event_id TEXT NOT NULL,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,book_id TEXT NOT NULL,draft_id TEXT NOT NULL,
 attempt_no INTEGER NOT NULL,lease_token TEXT NOT NULL,execution_user_id TEXT NOT NULL REFERENCES accounting.users(id),
 input_fact_version INTEGER NOT NULL,input_draft_version INTEGER NOT NULL,configuration_fingerprint TEXT NOT NULL,policy_fingerprint TEXT NOT NULL,
 phase TEXT NOT NULL DEFAULT 'claimed' CHECK(phase IN ('claimed','sending','finished')),
 status TEXT NOT NULL DEFAULT 'running' CHECK(status IN ('running','succeeded','needs_attention','failed','interrupted','stale','cancelled')),
 ai_run_id TEXT,error_code TEXT NOT NULL DEFAULT '',error_detail TEXT NOT NULL DEFAULT '',created_at TEXT NOT NULL,finished_at TEXT,
 UNIQUE(job_id,attempt_no),UNIQUE(job_id,lease_token),
 FOREIGN KEY(job_id,event_id,firm_id,company_id,book_id,draft_id) REFERENCES accounting.proposal_jobs(id,event_id,firm_id,company_id,book_id,draft_id),
 FOREIGN KEY(ai_run_id,event_id,firm_id,company_id,book_id,draft_id) REFERENCES accounting.ai_runs(id,event_id,firm_id,company_id,book_id,draft_id),
 CHECK((status='running' AND phase IN ('claimed','sending') AND finished_at IS NULL) OR (status<>'running' AND phase='finished' AND finished_at IS NOT NULL))
);
CREATE TABLE accounting.proposal_retries (
 job_id TEXT NOT NULL,request_key TEXT NOT NULL,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,event_id TEXT NOT NULL,book_id TEXT NOT NULL,draft_id TEXT NOT NULL,
 requested_by TEXT NOT NULL REFERENCES accounting.users(id),created_at TEXT NOT NULL,reason TEXT NOT NULL CHECK(length(btrim(reason)) BETWEEN 3 AND 1000),PRIMARY KEY(job_id,request_key),
 FOREIGN KEY(job_id,event_id,firm_id,company_id,book_id,draft_id) REFERENCES accounting.proposal_jobs(id,event_id,firm_id,company_id,book_id,draft_id)
);

-- Reading progress requires present book edit/review rights, never an old
-- requester's grant. Dedicated workers have no direct queue table privileges.
CREATE FUNCTION accounting.proposal_scope(target_job TEXT,write_access BOOLEAN DEFAULT false) RETURNS BOOLEAN
 LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 SELECT EXISTS(SELECT 1 FROM accounting.proposal_jobs j WHERE j.id=target_job AND j.firm_id=accounting.actor_firm()
  AND accounting.book_allowed(j.book_id,true) AND accounting.business_event_visible(j.event_id,j.firm_id,j.company_id)
  AND (NOT write_access OR accounting.ai_event_open(j.event_id,j.firm_id,j.company_id)))
$$;
CREATE FUNCTION accounting.proposal_job_update_guard() RETURNS TRIGGER LANGUAGE plpgsql SET search_path=pg_catalog,accounting,pg_temp AS $$
 BEGIN
  IF (NEW.id,NEW.firm_id,NEW.company_id,NEW.event_id,NEW.book_id,NEW.draft_id,NEW.created_by,NEW.created_at,NEW.idempotency_key)
   IS DISTINCT FROM (OLD.id,OLD.firm_id,OLD.company_id,OLD.event_id,OLD.book_id,OLD.draft_id,OLD.created_by,OLD.created_at,OLD.idempotency_key) THEN
   RAISE EXCEPTION 'Proposal job scope and provenance are immutable' USING ERRCODE='23514';
  END IF;
  IF current_user='accounting_app' AND (OLD.status NOT IN ('failed','interrupted','stale','cancelled','needs_attention') OR NEW.status<>'queued'
   OR NEW.execution_user_id<>accounting.actor_user() OR NEW.attempt_count<>OLD.attempt_count OR NEW.max_attempts<OLD.max_attempts
   OR NEW.max_attempts>OLD.attempt_count+3 OR NEW.generation<>OLD.generation+1 OR NEW.current_run_id IS NOT NULL
   OR NEW.result_template_id IS NOT NULL OR NEW.result_template_version IS NOT NULL OR NEW.result_draft_version IS NOT NULL
   OR NEW.lease_token IS NOT NULL OR NEW.lease_until IS NOT NULL OR NEW.worker_id IS NOT NULL OR NEW.error_code<>'' OR NEW.error_detail<>'' OR NEW.finished_at IS NOT NULL) THEN
   RAISE EXCEPTION 'API may only request a bounded manual proposal retry' USING ERRCODE='23514';
  END IF;
  RETURN NEW;
 END
$$;
CREATE FUNCTION accounting.proposal_attempt_update_guard() RETURNS TRIGGER LANGUAGE plpgsql SET search_path=pg_catalog,accounting,pg_temp AS $$
 BEGIN
  IF OLD.status<>'running' OR
   (NEW.id,NEW.job_id,NEW.event_id,NEW.firm_id,NEW.company_id,NEW.book_id,NEW.draft_id,NEW.attempt_no,NEW.lease_token,NEW.execution_user_id,
    NEW.input_fact_version,NEW.input_draft_version,NEW.configuration_fingerprint,NEW.policy_fingerprint,NEW.created_at)
   IS DISTINCT FROM (OLD.id,OLD.job_id,OLD.event_id,OLD.firm_id,OLD.company_id,OLD.book_id,OLD.draft_id,OLD.attempt_no,OLD.lease_token,OLD.execution_user_id,
    OLD.input_fact_version,OLD.input_draft_version,OLD.configuration_fingerprint,OLD.policy_fingerprint,OLD.created_at)
   OR (OLD.ai_run_id IS NOT NULL AND NEW.ai_run_id IS DISTINCT FROM OLD.ai_run_id) OR (OLD.phase='sending' AND NEW.phase='claimed') THEN
   RAISE EXCEPTION 'Proposal attempt history is immutable' USING ERRCODE='23514';
  END IF;
  RETURN NEW;
 END
$$;
CREATE TRIGGER proposal_job_identity BEFORE UPDATE ON accounting.proposal_jobs FOR EACH ROW EXECUTE FUNCTION accounting.proposal_job_update_guard();
CREATE TRIGGER proposal_attempt_identity BEFORE UPDATE ON accounting.proposal_attempts FOR EACH ROW EXECUTE FUNCTION accounting.proposal_attempt_update_guard();
CREATE TRIGGER proposal_jobs_no_delete BEFORE DELETE ON accounting.proposal_jobs FOR EACH ROW EXECUTE FUNCTION accounting.ai_immutable_guard();
CREATE TRIGGER proposal_attempts_no_delete BEFORE DELETE ON accounting.proposal_attempts FOR EACH ROW EXECUTE FUNCTION accounting.ai_immutable_guard();
CREATE TRIGGER proposal_dependencies_immutable BEFORE UPDATE OR DELETE ON accounting.proposal_dependencies FOR EACH ROW EXECUTE FUNCTION accounting.ai_immutable_guard();
CREATE TRIGGER proposal_retries_immutable BEFORE UPDATE OR DELETE ON accounting.proposal_retries FOR EACH ROW EXECUTE FUNCTION accounting.ai_immutable_guard();
DO $$ DECLARE item TEXT; BEGIN
 FOREACH item IN ARRAY ARRAY['proposal_jobs','proposal_dependencies','proposal_attempts','proposal_retries'] LOOP
  EXECUTE format('ALTER TABLE accounting.%I ENABLE ROW LEVEL SECURITY',item);
  EXECUTE format('ALTER TABLE accounting.%I FORCE ROW LEVEL SECURITY',item);
 END LOOP;
END $$;
GRANT SELECT,INSERT,UPDATE ON accounting.proposal_jobs TO accounting_app;
GRANT SELECT,INSERT ON accounting.proposal_dependencies,accounting.proposal_retries TO accounting_app;
GRANT SELECT ON accounting.proposal_attempts TO accounting_app;
CREATE POLICY proposal_jobs_read ON accounting.proposal_jobs FOR SELECT TO accounting_app USING(accounting.proposal_scope(id));
CREATE POLICY proposal_jobs_create ON accounting.proposal_jobs FOR INSERT TO accounting_app WITH CHECK(
 firm_id=accounting.actor_firm() AND created_by=accounting.actor_user() AND execution_user_id=accounting.actor_user()
 AND status IN ('queued','needs_attention') AND generation=1 AND attempt_count=0 AND max_attempts=3 AND current_run_id IS NULL
 AND result_template_id IS NULL AND result_template_version IS NULL AND result_draft_version IS NULL
 AND accounting.book_allowed(book_id,true) AND accounting.ai_event_open(event_id,firm_id,company_id)
 AND EXISTS(SELECT 1 FROM accounting.business_mappings m JOIN accounting.business_events e ON e.id=m.event_id JOIN accounting.drafts d ON d.id=m.draft_id
  WHERE m.event_id=proposal_jobs.event_id AND m.firm_id=proposal_jobs.firm_id AND m.company_id=proposal_jobs.company_id AND m.book_id=proposal_jobs.book_id
  AND m.draft_id=proposal_jobs.draft_id AND e.status='handed_off' AND e.fact_version=expected_fact_version AND d.version=expected_draft_version));
CREATE POLICY proposal_jobs_retry ON accounting.proposal_jobs FOR UPDATE TO accounting_app USING(accounting.proposal_scope(id,true))
 WITH CHECK(accounting.proposal_scope(id,true) AND execution_user_id=accounting.actor_user());
CREATE POLICY proposal_dependencies_read ON accounting.proposal_dependencies FOR SELECT TO accounting_app USING(accounting.proposal_scope(proposal_job_id));
CREATE POLICY proposal_dependencies_create ON accounting.proposal_dependencies FOR INSERT TO accounting_app WITH CHECK(
 accounting.proposal_scope(proposal_job_id,true) AND EXISTS(SELECT 1 FROM accounting.proposal_jobs j WHERE j.id=proposal_job_id
  AND j.generation=proposal_dependencies.generation AND j.execution_user_id=accounting.actor_user() AND j.status='queued'));
CREATE POLICY proposal_attempts_read ON accounting.proposal_attempts FOR SELECT TO accounting_app USING(accounting.proposal_scope(job_id));
CREATE POLICY proposal_retries_read ON accounting.proposal_retries FOR SELECT TO accounting_app USING(accounting.proposal_scope(job_id));
CREATE POLICY proposal_retries_create ON accounting.proposal_retries FOR INSERT TO accounting_app WITH CHECK(requested_by=accounting.actor_user() AND accounting.proposal_scope(job_id,true));

CREATE FUNCTION accounting.proposal_wait_dependencies(target_job TEXT) RETURNS BOOLEAN
 LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 BEGIN
  PERFORM pg_advisory_xact_lock(6420031786281903101);
  UPDATE accounting.proposal_jobs j SET status='waiting' WHERE j.id=target_job AND j.status='queued'
   AND j.execution_user_id=accounting.actor_user() AND accounting.proposal_scope(j.id,true)
   AND EXISTS(SELECT 1 FROM accounting.proposal_dependencies d WHERE d.proposal_job_id=j.id AND d.generation=j.generation);
  RETURN FOUND;
 END
$$;
CREATE FUNCTION accounting.worker_refresh_proposal_dependencies() RETURNS INTEGER
 LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE j accounting.proposal_jobs; stamp TEXT; outcome TEXT; changed INTEGER:=0;
 BEGIN
  PERFORM pg_advisory_xact_lock(6420031786281903101);stamp:=accounting.ocr_utc_now();
  FOR j IN SELECT * FROM accounting.proposal_jobs WHERE status='waiting' ORDER BY id FOR UPDATE LOOP
   IF EXISTS(SELECT 1 FROM accounting.proposal_dependencies d JOIN accounting.ocr_jobs o ON o.id=d.ocr_job_id
    WHERE d.proposal_job_id=j.id AND d.generation=j.generation AND o.status NOT IN ('succeeded','queued','running','retry_wait')) THEN
    outcome:='ocr_dependency_failed';
   ELSIF j.dependency_deadline::timestamptz<=clock_timestamp() THEN outcome:='ocr_dependency_timeout';
   ELSIF NOT EXISTS(SELECT 1 FROM accounting.proposal_dependencies d JOIN accounting.ocr_jobs o ON o.id=d.ocr_job_id
    WHERE d.proposal_job_id=j.id AND d.generation=j.generation AND o.status<>'succeeded') THEN outcome:='ready';
   ELSE CONTINUE; END IF;
   UPDATE accounting.proposal_jobs SET status=CASE WHEN outcome='ready' THEN 'queued' ELSE 'needs_attention' END,
    error_code=CASE WHEN outcome='ready' THEN '' ELSE outcome END,error_detail=CASE WHEN outcome='ready' THEN '' ELSE '票据识别未完成，请处理失败票据或积压后由会计重试' END,
    updated_at=stamp,finished_at=CASE WHEN outcome='ready' THEN NULL ELSE stamp END WHERE id=j.id;
   changed:=changed+1;
  END LOOP;
  RETURN changed;
 END
$$;
CREATE FUNCTION accounting.worker_check_proposal_lease(target_job TEXT,target_token TEXT) RETURNS BOOLEAN
 LANGUAGE sql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 SELECT EXISTS(SELECT 1 FROM accounting.proposal_jobs WHERE id=target_job AND lease_token=target_token AND status='running' AND lease_until::timestamptz>clock_timestamp())
$$;
CREATE FUNCTION accounting.worker_bind_proposal_run(target_job TEXT,target_token TEXT,target_run TEXT) RETURNS BOOLEAN
 LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE j accounting.proposal_jobs;
 BEGIN
  PERFORM pg_advisory_xact_lock(6420031786281903101);
  SELECT * INTO j FROM accounting.proposal_jobs WHERE id=target_job AND lease_token=target_token AND status='running' AND lease_until::timestamptz>clock_timestamp() FOR UPDATE;
  IF j.id IS NULL THEN RETURN false; END IF;
  IF j.current_run_id IS NOT NULL THEN RETURN j.current_run_id=target_run; END IF;
  IF NOT EXISTS(SELECT 1 FROM accounting.ai_runs r WHERE r.id=target_run AND r.kind='proposal' AND r.status='running'
   AND r.event_id=j.event_id AND r.firm_id=j.firm_id AND r.company_id=j.company_id AND r.book_id=j.book_id AND r.draft_id=j.draft_id
   AND r.created_by=j.execution_user_id AND r.input_version=j.expected_draft_version) THEN RETURN false; END IF;
  UPDATE accounting.proposal_jobs SET current_run_id=target_run,updated_at=accounting.ocr_utc_now() WHERE id=j.id;
  UPDATE accounting.proposal_attempts SET ai_run_id=target_run WHERE job_id=j.id AND lease_token=target_token AND phase='claimed' AND status='running';
  RETURN true;
 END
$$;
CREATE FUNCTION accounting.worker_mark_proposal_sending(target_job TEXT,target_token TEXT) RETURNS BOOLEAN
 LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 BEGIN
  PERFORM pg_advisory_xact_lock(6420031786281903101);
  IF NOT accounting.worker_check_proposal_lease(target_job,target_token) THEN RETURN false; END IF;
  UPDATE accounting.proposal_attempts a SET phase='sending' FROM accounting.proposal_jobs j,accounting.ai_runs r
   WHERE j.id=target_job AND r.id=j.current_run_id AND r.status='running' AND a.job_id=j.id AND a.lease_token=target_token
   AND a.status='running' AND a.ai_run_id=r.id;
  RETURN FOUND;
 END
$$;
CREATE FUNCTION accounting.worker_renew_proposal_lease(target_job TEXT,target_token TEXT,lease_seconds INTEGER DEFAULT 180) RETURNS BOOLEAN
 LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 BEGIN
  IF lease_seconds NOT BETWEEN 30 AND 600 THEN RAISE EXCEPTION 'Invalid lease bound' USING ERRCODE='22023'; END IF;
  PERFORM pg_advisory_xact_lock(6420031786281903101);
  UPDATE accounting.proposal_jobs SET lease_until=to_char((clock_timestamp()+make_interval(secs=>lease_seconds)) AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"+00:00"'),updated_at=accounting.ocr_utc_now()
   WHERE id=target_job AND lease_token=target_token AND status='running' AND lease_until::timestamptz>clock_timestamp();
  RETURN FOUND;
 END
$$;
CREATE FUNCTION accounting.worker_finish_proposal_job(target_job TEXT,target_token TEXT,target_status TEXT,target_error_code TEXT DEFAULT '',target_error_detail TEXT DEFAULT '',target_run TEXT DEFAULT NULL,retry_delay_seconds INTEGER DEFAULT 0) RETURNS BOOLEAN
 LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE j accounting.proposal_jobs; stamp TEXT; outcome TEXT;
 BEGIN
  IF target_status NOT IN ('succeeded','needs_attention','failed','interrupted','stale','cancelled','retry_wait') OR retry_delay_seconds NOT BETWEEN 0 AND 3600
   OR length(target_error_code)>120 OR length(target_error_detail)>1000 THEN RAISE EXCEPTION 'Invalid finish bounds' USING ERRCODE='22023'; END IF;
  PERFORM pg_advisory_xact_lock(6420031786281903101);
  SELECT * INTO j FROM accounting.proposal_jobs WHERE id=target_job AND lease_token=target_token AND status='running' AND lease_until::timestamptz>clock_timestamp() FOR UPDATE;
  IF j.id IS NULL OR (target_run IS NOT NULL AND target_run IS DISTINCT FROM j.current_run_id) THEN RETURN false; END IF;
  IF target_status='succeeded' AND NOT EXISTS(SELECT 1 FROM accounting.ai_runs r JOIN accounting.ai_proposal_applications a ON a.run_id=r.id
   WHERE r.id=j.current_run_id AND r.status='succeeded' AND a.draft_id=j.draft_id AND a.book_id=j.book_id AND a.applied_draft_version=j.expected_draft_version+1) THEN RETURN false; END IF;
  stamp:=accounting.ocr_utc_now();outcome:=CASE WHEN target_status='retry_wait' AND j.attempt_count>=j.max_attempts THEN 'failed' ELSE target_status END;
  IF outcome<>'succeeded' AND j.current_run_id IS NOT NULL THEN
   UPDATE accounting.ai_runs SET status='stale',result_json=NULL,finished_at=stamp,error_code='worker_terminated',error_detail='后台分录任务已停止，旧响应不会发布'
    WHERE id=j.current_run_id AND status='running' AND created_by=j.execution_user_id AND event_id=j.event_id AND firm_id=j.firm_id AND company_id=j.company_id AND book_id=j.book_id AND draft_id=j.draft_id;
  END IF;
  UPDATE accounting.proposal_attempts SET phase='finished',status=CASE WHEN outcome='retry_wait' THEN 'failed' ELSE outcome END,
   error_code=target_error_code,error_detail=target_error_detail,finished_at=stamp WHERE job_id=j.id AND lease_token=target_token AND status='running';
  UPDATE accounting.proposal_jobs SET status=outcome,lease_token=NULL,lease_until=NULL,worker_id=NULL,updated_at=stamp,error_code=target_error_code,error_detail=target_error_detail,
   result_draft_version=CASE WHEN outcome='succeeded' THEN expected_draft_version+1 ELSE NULL END,
   finished_at=CASE WHEN outcome='retry_wait' THEN NULL ELSE stamp END,
   available_at=to_char((clock_timestamp()+make_interval(secs=>retry_delay_seconds)) AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"+00:00"') WHERE id=j.id;
  RETURN true;
 END
$$;
CREATE FUNCTION accounting.worker_finish_proposal_rule(target_job TEXT,target_token TEXT,target_template TEXT,target_template_version INTEGER,target_draft_version INTEGER) RETURNS BOOLEAN
 LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE j accounting.proposal_jobs; stamp TEXT;
 BEGIN
  PERFORM pg_advisory_xact_lock(6420031786281903101);
  SELECT * INTO j FROM accounting.proposal_jobs WHERE id=target_job AND lease_token=target_token AND status='running' AND lease_until::timestamptz>clock_timestamp() FOR UPDATE;
  IF j.id IS NULL OR j.current_run_id IS NOT NULL OR target_draft_version<>j.expected_draft_version+1 THEN RETURN false; END IF;
  IF NOT EXISTS(SELECT 1 FROM accounting.journal_templates t JOIN accounting.drafts d ON d.book_id=t.book_id JOIN accounting.business_events e ON e.id=j.event_id
   WHERE t.id=target_template AND t.book_id=j.book_id AND t.active=1 AND t.current_version=target_template_version
   AND d.id=j.draft_id AND d.version=target_draft_version AND d.status='draft' AND d.last_manual_edit_version=0 AND e.fact_version=j.expected_fact_version) THEN RETURN false; END IF;
  stamp:=accounting.ocr_utc_now();
  UPDATE accounting.proposal_attempts SET phase='finished',status='succeeded',error_code='',error_detail='',finished_at=stamp WHERE job_id=j.id AND lease_token=target_token AND status='running';
  UPDATE accounting.proposal_jobs SET status='succeeded',lease_token=NULL,lease_until=NULL,worker_id=NULL,updated_at=stamp,finished_at=stamp,error_code='',error_detail='',
   result_template_id=target_template,result_template_version=target_template_version,result_draft_version=target_draft_version WHERE id=j.id;
  RETURN true;
 END
$$;
CREATE FUNCTION accounting.cancel_proposal_job(target_job TEXT,reason TEXT) RETURNS BOOLEAN
 LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE j accounting.proposal_jobs; stamp TEXT;
 BEGIN
  IF length(btrim(reason)) NOT BETWEEN 1 AND 1000 THEN RAISE EXCEPTION 'Cancellation reason required' USING ERRCODE='22023'; END IF;
  PERFORM pg_advisory_xact_lock(6420031786281903101);
  SELECT * INTO j FROM accounting.proposal_jobs WHERE id=target_job AND accounting.proposal_scope(id) FOR UPDATE;
  IF j.id IS NULL OR j.status NOT IN ('waiting','queued','running','retry_wait','interrupted') THEN RETURN false; END IF;
  stamp:=accounting.ocr_utc_now();
  UPDATE accounting.ai_runs SET status='stale',result_json=NULL,finished_at=stamp,error_code='queue_cancelled',error_detail='分录任务已由授权会计取消'
   WHERE id=j.current_run_id AND status='running' AND created_by=j.execution_user_id AND event_id=j.event_id AND firm_id=j.firm_id AND company_id=j.company_id AND book_id=j.book_id AND draft_id=j.draft_id;
  UPDATE accounting.proposal_attempts SET phase='finished',status='cancelled',finished_at=stamp,error_code='queue_cancelled',error_detail=reason WHERE job_id=j.id AND status='running';
  UPDATE accounting.proposal_jobs SET status='cancelled',lease_token=NULL,lease_until=NULL,worker_id=NULL,error_code='queue_cancelled',error_detail='授权会计已取消自动分录任务',updated_at=stamp,finished_at=stamp WHERE id=j.id;
  RETURN true;
 END
$$;
CREATE FUNCTION accounting.recover_proposal_jobs() RETURNS INTEGER
 LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE j accounting.proposal_jobs; a accounting.proposal_attempts; outcome TEXT; stamp TEXT; recovered INTEGER:=0;
 BEGIN
  PERFORM pg_advisory_xact_lock(6420031786281903101);stamp:=accounting.ocr_utc_now();
  FOR j IN SELECT * FROM accounting.proposal_jobs WHERE status='running' AND lease_until::timestamptz<=clock_timestamp() ORDER BY id FOR UPDATE LOOP
   SELECT * INTO a FROM accounting.proposal_attempts WHERE job_id=j.id AND lease_token=j.lease_token;
   outcome:=CASE WHEN a.phase='sending' THEN 'interrupted' WHEN j.attempt_count<j.max_attempts THEN 'retry_wait' ELSE 'failed' END;
   UPDATE accounting.ai_runs SET status='stale',result_json=NULL,finished_at=stamp,error_code='worker_lease_expired',error_detail=''
    WHERE id=j.current_run_id AND status='running';
   UPDATE accounting.proposal_attempts SET phase='finished',status=CASE WHEN outcome='retry_wait' THEN 'failed' ELSE outcome END,finished_at=stamp,
    error_code='worker_lease_expired',error_detail='工作进程中断；发送后的未确认任务须人工重试' WHERE id=a.id AND status='running';
   UPDATE accounting.proposal_jobs SET status=outcome,lease_token=NULL,lease_until=NULL,worker_id=NULL,updated_at=stamp,available_at=stamp,
    finished_at=CASE WHEN outcome='retry_wait' THEN NULL ELSE stamp END,error_code='worker_lease_expired',error_detail='工作进程中断；发送后的未确认任务须人工重试' WHERE id=j.id;
   recovered:=recovered+1;
  END LOOP;
  PERFORM accounting.worker_refresh_proposal_dependencies();
  RETURN recovered;
 END
$$;

-- Preserve legacy OCR dispatch while applying the shared global and company caps.
CREATE OR REPLACE FUNCTION accounting.claim_ocr_job(target_worker TEXT,lease_seconds INTEGER DEFAULT 180,global_limit INTEGER DEFAULT 2)
 RETURNS SETOF accounting.ocr_jobs LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE j accounting.ocr_jobs; token TEXT; stamp TEXT; seq BIGINT;
 BEGIN
  IF length(btrim(target_worker)) NOT BETWEEN 1 AND 120 OR lease_seconds NOT BETWEEN 30 AND 600 OR global_limit NOT BETWEEN 1 AND 2 THEN
   RAISE EXCEPTION 'Invalid queue worker bounds' USING ERRCODE='22023';
  END IF;
  PERFORM pg_advisory_xact_lock(6420031786281903101);
  PERFORM accounting.recover_ocr_jobs();
  IF (SELECT count(*) FROM accounting.ocr_jobs WHERE status='running')+(SELECT count(*) FROM accounting.proposal_jobs WHERE status='running')>=global_limit THEN RETURN; END IF;
  INSERT INTO accounting.ocr_company_schedule(firm_id,company_id)
   SELECT DISTINCT firm_id,company_id FROM accounting.ocr_jobs WHERE status IN ('queued','retry_wait') ON CONFLICT DO NOTHING;
  SELECT q.* INTO j FROM accounting.ocr_jobs q JOIN accounting.ocr_company_schedule s USING(firm_id,company_id)
   WHERE q.status IN ('queued','retry_wait') AND q.available_at::timestamptz<=clock_timestamp() AND q.attempt_count<q.max_attempts
   AND NOT EXISTS(SELECT 1 FROM accounting.ocr_jobs busy WHERE busy.firm_id=q.firm_id AND busy.company_id=q.company_id AND busy.status='running') AND NOT EXISTS(SELECT 1 FROM accounting.proposal_jobs busy WHERE busy.firm_id=q.firm_id AND busy.company_id=q.company_id AND busy.status='running')
   ORDER BY s.last_dispatch_seq,q.created_at,q.id LIMIT 1 FOR UPDATE OF q;
  IF j.id IS NULL THEN RETURN; END IF;
  stamp:=accounting.ocr_utc_now();token:=md5(random()::text||clock_timestamp()::text||j.id||target_worker);
  SELECT coalesce(max(last_dispatch_seq),0)+1 INTO seq FROM accounting.ocr_company_schedule;
  UPDATE accounting.ocr_company_schedule SET last_dispatch_seq=seq WHERE firm_id=j.firm_id AND company_id=j.company_id;
  UPDATE accounting.ocr_jobs SET status='running',attempt_count=attempt_count+1,lease_token=token,
   lease_until=to_char((clock_timestamp()+make_interval(secs=>lease_seconds)) AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"+00:00"'),
   worker_id=target_worker,current_run_id=NULL,error_code='',error_detail='',updated_at=stamp WHERE id=j.id RETURNING * INTO j;
  INSERT INTO accounting.ocr_attempts(id,job_id,firm_id,company_id,event_id,attachment_id,attempt_no,lease_token,execution_user_id,input_version,attachment_sha256,configuration_fingerprint,created_at)
   VALUES(md5(token||j.id),j.id,j.firm_id,j.company_id,j.event_id,j.attachment_id,j.attempt_count,token,j.execution_user_id,COALESCE(j.expected_fact_version,j.expected_version),j.attachment_sha256,j.configuration_fingerprint,stamp);
  RETURN NEXT j;
 END
$$;
CREATE OR REPLACE FUNCTION accounting.worker_check_ocr_lease(target_job TEXT,target_token TEXT) RETURNS BOOLEAN
 LANGUAGE sql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 SELECT EXISTS(SELECT 1 FROM accounting.ocr_jobs WHERE id=target_job AND lease_token=target_token AND status='running' AND lease_until::timestamptz>clock_timestamp())
$$;
CREATE OR REPLACE FUNCTION accounting.worker_bind_ocr_run(target_job TEXT,target_token TEXT,target_run TEXT) RETURNS BOOLEAN
 LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE j accounting.ocr_jobs;
 BEGIN
  PERFORM pg_advisory_xact_lock(6420031786281903101);
  SELECT * INTO j FROM accounting.ocr_jobs WHERE id=target_job AND lease_token=target_token AND status='running' AND lease_until::timestamptz>clock_timestamp() FOR UPDATE;
  IF j.id IS NULL THEN RETURN false; END IF;
  IF j.current_run_id IS NOT NULL THEN RETURN j.current_run_id=target_run; END IF;
  IF NOT EXISTS(SELECT 1 FROM accounting.ai_runs r WHERE r.id=target_run AND r.kind='ocr' AND r.status='running'
   AND r.event_id=j.event_id AND r.firm_id=j.firm_id AND r.company_id=j.company_id AND r.attachment_id=j.attachment_id
   AND r.created_by=j.execution_user_id AND r.input_version=COALESCE(j.expected_fact_version,j.expected_version)) THEN RETURN false; END IF;
  UPDATE accounting.ocr_jobs SET current_run_id=target_run,updated_at=accounting.ocr_utc_now() WHERE id=j.id;
  UPDATE accounting.ocr_attempts SET ai_run_id=target_run WHERE job_id=j.id AND lease_token=target_token AND phase='claimed' AND status='running';
  RETURN true;
 END
$$;

CREATE FUNCTION accounting.claim_proposal_job(target_worker TEXT,lease_seconds INTEGER DEFAULT 180,global_limit INTEGER DEFAULT 2)
 RETURNS SETOF accounting.proposal_jobs LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE j accounting.proposal_jobs; token TEXT; stamp TEXT; seq BIGINT;
 BEGIN
  IF length(btrim(target_worker)) NOT BETWEEN 1 AND 120 OR lease_seconds NOT BETWEEN 30 AND 600 OR global_limit NOT BETWEEN 1 AND 2 THEN
   RAISE EXCEPTION 'Invalid queue worker bounds' USING ERRCODE='22023'; END IF;
  PERFORM pg_advisory_xact_lock(6420031786281903101);
  PERFORM accounting.recover_proposal_jobs();
  IF (SELECT count(*) FROM accounting.ocr_jobs WHERE status='running')+(SELECT count(*) FROM accounting.proposal_jobs WHERE status='running')>=global_limit THEN RETURN; END IF;
  INSERT INTO accounting.ocr_company_schedule(firm_id,company_id) SELECT DISTINCT firm_id,company_id FROM accounting.proposal_jobs WHERE status IN ('queued','retry_wait') ON CONFLICT DO NOTHING;
  SELECT q.* INTO j FROM accounting.proposal_jobs q JOIN accounting.ocr_company_schedule s USING(firm_id,company_id)
   WHERE q.status IN ('queued','retry_wait') AND q.available_at::timestamptz<=clock_timestamp() AND q.attempt_count<q.max_attempts
   AND NOT EXISTS(SELECT 1 FROM accounting.ocr_jobs busy WHERE busy.firm_id=q.firm_id AND busy.company_id=q.company_id AND busy.status='running')
   AND NOT EXISTS(SELECT 1 FROM accounting.proposal_jobs busy WHERE busy.firm_id=q.firm_id AND busy.company_id=q.company_id AND busy.status='running')
   ORDER BY s.last_dispatch_seq,q.created_at,q.id LIMIT 1 FOR UPDATE OF q;
  IF j.id IS NULL THEN RETURN; END IF;
  stamp:=accounting.ocr_utc_now();token:=md5(random()::text||clock_timestamp()::text||j.id||target_worker);
  SELECT coalesce(max(last_dispatch_seq),0)+1 INTO seq FROM accounting.ocr_company_schedule;
  UPDATE accounting.ocr_company_schedule SET last_dispatch_seq=seq WHERE firm_id=j.firm_id AND company_id=j.company_id;
  UPDATE accounting.proposal_jobs SET status='running',attempt_count=attempt_count+1,lease_token=token,
   lease_until=to_char((clock_timestamp()+make_interval(secs=>lease_seconds)) AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"+00:00"'),
   worker_id=target_worker,current_run_id=NULL,error_code='',error_detail='',updated_at=stamp WHERE id=j.id RETURNING * INTO j;
  INSERT INTO accounting.proposal_attempts(id,job_id,event_id,firm_id,company_id,book_id,draft_id,attempt_no,lease_token,execution_user_id,input_fact_version,input_draft_version,configuration_fingerprint,policy_fingerprint,created_at)
   VALUES(md5(token||j.id),j.id,j.event_id,j.firm_id,j.company_id,j.book_id,j.draft_id,j.attempt_count,token,j.execution_user_id,j.expected_fact_version,j.expected_draft_version,j.configuration_fingerprint,j.policy_fingerprint,stamp);
  RETURN NEXT j;
 END
$$;
CREATE FUNCTION accounting.claim_processing_job(target_worker TEXT,lease_seconds INTEGER DEFAULT 180,global_limit INTEGER DEFAULT 2)
 RETURNS JSONB LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE kind TEXT; o accounting.ocr_jobs; p accounting.proposal_jobs;
 BEGIN
  IF length(btrim(target_worker)) NOT BETWEEN 1 AND 120 OR lease_seconds NOT BETWEEN 30 AND 600 OR global_limit NOT BETWEEN 1 AND 2 THEN
   RAISE EXCEPTION 'Invalid queue worker bounds' USING ERRCODE='22023'; END IF;
  PERFORM pg_advisory_xact_lock(6420031786281903101);
  PERFORM accounting.recover_ocr_jobs();PERFORM accounting.recover_proposal_jobs();
  IF (SELECT count(*) FROM accounting.ocr_jobs WHERE status='running')+(SELECT count(*) FROM accounting.proposal_jobs WHERE status='running')>=global_limit THEN RETURN NULL; END IF;
  INSERT INTO accounting.ocr_company_schedule(firm_id,company_id)
   SELECT DISTINCT firm_id,company_id FROM (SELECT firm_id,company_id FROM accounting.ocr_jobs WHERE status IN ('queued','retry_wait')
    UNION SELECT firm_id,company_id FROM accounting.proposal_jobs WHERE status IN ('queued','retry_wait')) jobs ON CONFLICT DO NOTHING;
  SELECT q.kind INTO kind FROM (
   SELECT id,firm_id,company_id,status,available_at,attempt_count,max_attempts,created_at,'ocr' AS kind FROM accounting.ocr_jobs
   UNION ALL SELECT id,firm_id,company_id,status,available_at,attempt_count,max_attempts,created_at,'proposal' AS kind FROM accounting.proposal_jobs
  ) q JOIN accounting.ocr_company_schedule s USING(firm_id,company_id)
   WHERE q.status IN ('queued','retry_wait') AND q.available_at::timestamptz<=clock_timestamp() AND q.attempt_count<q.max_attempts
   AND NOT EXISTS(SELECT 1 FROM accounting.ocr_jobs busy WHERE busy.firm_id=q.firm_id AND busy.company_id=q.company_id AND busy.status='running')
   AND NOT EXISTS(SELECT 1 FROM accounting.proposal_jobs busy WHERE busy.firm_id=q.firm_id AND busy.company_id=q.company_id AND busy.status='running')
   ORDER BY s.last_dispatch_seq,q.created_at,q.id LIMIT 1;
  IF kind='ocr' THEN
   SELECT * INTO o FROM accounting.claim_ocr_job(target_worker,lease_seconds,global_limit);
   IF o.id IS NOT NULL THEN RETURN to_jsonb(o)||jsonb_build_object('job_kind','ocr'); END IF;
  ELSIF kind='proposal' THEN
   SELECT * INTO p FROM accounting.claim_proposal_job(target_worker,lease_seconds,global_limit);
   IF p.id IS NOT NULL THEN RETURN to_jsonb(p)||jsonb_build_object('job_kind','proposal'); END IF;
  END IF;
  RETURN NULL;
 END
$$;

REVOKE ALL ON FUNCTION accounting.proposal_scope(TEXT,BOOLEAN),accounting.proposal_job_update_guard(),accounting.proposal_attempt_update_guard(),
 accounting.proposal_wait_dependencies(TEXT),accounting.worker_refresh_proposal_dependencies(),accounting.worker_check_proposal_lease(TEXT,TEXT),
 accounting.worker_bind_proposal_run(TEXT,TEXT,TEXT),accounting.worker_mark_proposal_sending(TEXT,TEXT),accounting.worker_renew_proposal_lease(TEXT,TEXT,INTEGER),
 accounting.worker_finish_proposal_job(TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,INTEGER),accounting.worker_finish_proposal_rule(TEXT,TEXT,TEXT,INTEGER,INTEGER),
 accounting.cancel_proposal_job(TEXT,TEXT),accounting.recover_proposal_jobs(),accounting.claim_proposal_job(TEXT,INTEGER,INTEGER),accounting.claim_processing_job(TEXT,INTEGER,INTEGER) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION accounting.proposal_scope(TEXT,BOOLEAN),accounting.proposal_wait_dependencies(TEXT),accounting.cancel_proposal_job(TEXT,TEXT) TO accounting_app;
GRANT EXECUTE ON FUNCTION accounting.worker_refresh_proposal_dependencies(),accounting.worker_check_proposal_lease(TEXT,TEXT),accounting.worker_bind_proposal_run(TEXT,TEXT,TEXT),
 accounting.worker_mark_proposal_sending(TEXT,TEXT),accounting.worker_renew_proposal_lease(TEXT,TEXT,INTEGER),accounting.worker_finish_proposal_job(TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,INTEGER),
 accounting.worker_finish_proposal_rule(TEXT,TEXT,TEXT,INTEGER,INTEGER),accounting.recover_proposal_jobs(),accounting.claim_proposal_job(TEXT,INTEGER,INTEGER),accounting.claim_processing_job(TEXT,INTEGER,INTEGER) TO accounting_worker;
