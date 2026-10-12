-- Tighten dedicated-role validation and make every early worker termination
-- fence a still-pending model output. Preserve already applied migration 013.
DO $$ DECLARE r RECORD; item TEXT; BEGIN
 SELECT * INTO r FROM pg_roles WHERE rolname='accounting_worker';
 IF r.oid IS NULL OR r.rolcanlogin OR r.rolsuper OR r.rolbypassrls OR r.rolcreatedb OR r.rolcreaterole
  OR EXISTS(SELECT 1 FROM pg_auth_members WHERE member=r.oid)
  OR has_schema_privilege('accounting_worker','accounting','CREATE') THEN
  RAISE EXCEPTION 'accounting_worker has unsafe existing capabilities; repair roles explicitly before migration';
 END IF;
 FOREACH item IN ARRAY ARRAY['ocr_batches','ocr_jobs','ocr_attempts','ocr_retries','ocr_company_schedule'] LOOP
  IF has_table_privilege('accounting_worker','accounting.'||item,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER') THEN
   RAISE EXCEPTION 'accounting_worker may not have direct queue-table privileges';
  END IF;
 END LOOP;
END $$;

CREATE OR REPLACE FUNCTION accounting.worker_finish_ocr_job(target_job TEXT,target_token TEXT,target_status TEXT,target_error_code TEXT DEFAULT '',target_error_detail TEXT DEFAULT '',target_run TEXT DEFAULT NULL,retry_delay_seconds INTEGER DEFAULT 0) RETURNS BOOLEAN
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
  IF outcome<>'succeeded' AND j.current_run_id IS NOT NULL THEN
   UPDATE accounting.ai_runs SET status='stale',result_json=NULL,finished_at=stamp,error_code='worker_terminated',error_detail='后台任务已停止，旧响应不会发布'
    WHERE id=j.current_run_id AND status='running' AND created_by=j.execution_user_id AND event_id=j.event_id
    AND firm_id=j.firm_id AND company_id=j.company_id AND attachment_id=j.attachment_id;
  END IF;
  UPDATE accounting.ocr_attempts SET phase='finished',status=CASE WHEN outcome='retry_wait' THEN 'failed' ELSE outcome END,
   error_code=target_error_code,error_detail=target_error_detail,finished_at=stamp WHERE job_id=j.id AND lease_token=target_token AND status='running';
  UPDATE accounting.ocr_jobs SET status=outcome,lease_token=NULL,lease_until=NULL,worker_id=NULL,updated_at=stamp,
   error_code=target_error_code,error_detail=target_error_detail,finished_at=CASE WHEN outcome='retry_wait' THEN NULL ELSE stamp END,
   available_at=to_char((clock_timestamp()+make_interval(secs=>retry_delay_seconds)) AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"+00:00"') WHERE id=j.id;
  RETURN true;
 END
$$;

CREATE OR REPLACE FUNCTION accounting.cancel_ocr_job(target_job TEXT,reason TEXT) RETURNS BOOLEAN
 LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE j accounting.ocr_jobs; stamp TEXT;
 BEGIN
  IF length(btrim(reason)) NOT BETWEEN 1 AND 1000 THEN RAISE EXCEPTION 'Cancellation reason required' USING ERRCODE='22023'; END IF;
  PERFORM pg_advisory_xact_lock(6420031786281903101);
  -- Turning off outside processing must still allow an authorized employee or
  -- accountant to cancel queued/uncertain work; no attachment is read here.
  SELECT * INTO j FROM accounting.ocr_jobs WHERE id=target_job
   AND accounting.ai_event_processor(event_id,firm_id,company_id)
   AND accounting.ai_event_open(event_id,firm_id,company_id) FOR UPDATE;
  IF j.id IS NULL OR j.status NOT IN ('queued','running','retry_wait','interrupted') THEN RETURN false; END IF;
  stamp:=accounting.ocr_utc_now();
  IF j.current_run_id IS NOT NULL THEN
   UPDATE accounting.ai_runs SET status='stale',result_json=NULL,finished_at=stamp,error_code='queue_cancelled',error_detail='任务已由有权限的人员取消'
    WHERE id=j.current_run_id AND status='running' AND created_by=j.execution_user_id AND event_id=j.event_id
    AND firm_id=j.firm_id AND company_id=j.company_id AND attachment_id=j.attachment_id;
  END IF;
  UPDATE accounting.ocr_attempts SET phase='finished',status='cancelled',finished_at=stamp,error_code='queue_cancelled',error_detail=reason
   WHERE job_id=j.id AND status='running';
  UPDATE accounting.ocr_jobs SET status='cancelled',lease_token=NULL,lease_until=NULL,worker_id=NULL,error_code='queue_cancelled',error_detail=reason,updated_at=stamp,finished_at=stamp WHERE id=j.id;
  RETURN true;
 END
$$;
REVOKE ALL ON FUNCTION accounting.worker_finish_ocr_job(TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,INTEGER),accounting.cancel_ocr_job(TEXT,TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION accounting.worker_finish_ocr_job(TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,INTEGER) TO accounting_worker;
GRANT EXECUTE ON FUNCTION accounting.cancel_ocr_job(TEXT,TEXT) TO accounting_app;
