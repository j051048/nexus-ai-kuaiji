-- Legacy template and model success receipts inherit current evidence blockers.
-- Failure/revocation cleanup retains its earlier capability semantics.
CREATE OR REPLACE FUNCTION accounting.worker_finish_proposal_job(target_job TEXT,target_token TEXT,target_status TEXT,target_error_code TEXT DEFAULT '',target_error_detail TEXT DEFAULT '',target_run TEXT DEFAULT NULL,retry_delay_seconds INTEGER DEFAULT 0) RETURNS BOOLEAN
 LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE j accounting.proposal_jobs; stamp TEXT; outcome TEXT;
 BEGIN
  IF target_status NOT IN ('succeeded','needs_attention','failed','interrupted','stale','cancelled','retry_wait') OR retry_delay_seconds NOT BETWEEN 0 AND 3600
   OR length(target_error_code)>120 OR length(target_error_detail)>1000 THEN RAISE EXCEPTION 'Invalid finish bounds' USING ERRCODE='22023'; END IF;
  PERFORM pg_advisory_xact_lock(6420031786281903101);
  SELECT * INTO j FROM accounting.proposal_jobs WHERE id=target_job AND lease_token=target_token AND status='running' AND lease_until::timestamptz>clock_timestamp() FOR UPDATE;
  IF j.id IS NULL OR (target_run IS NOT NULL AND target_run IS DISTINCT FROM j.current_run_id) THEN RETURN false; END IF;
  IF target_status='succeeded' THEN
   IF NOT accounting.book_allowed(j.book_id,true) OR NOT accounting.ai_event_open(j.event_id,j.firm_id,j.company_id) THEN RETURN false; END IF;
   IF accounting.source_workflow_blocked(j.book_id,(SELECT source_id FROM accounting.drafts WHERE id=j.draft_id AND book_id=j.book_id)) THEN RETURN false; END IF;
  END IF;
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
CREATE OR REPLACE FUNCTION accounting.worker_finish_proposal_rule(target_job TEXT,target_token TEXT,target_template TEXT,target_template_version INTEGER,target_draft_version INTEGER) RETURNS BOOLEAN
 LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE j accounting.proposal_jobs; stamp TEXT;
 BEGIN
  PERFORM pg_advisory_xact_lock(6420031786281903101);
  SELECT * INTO j FROM accounting.proposal_jobs WHERE id=target_job AND lease_token=target_token AND status='running' AND lease_until::timestamptz>clock_timestamp() FOR UPDATE;
  IF j.id IS NULL OR j.current_run_id IS NOT NULL OR target_draft_version<>j.expected_draft_version+1 THEN RETURN false; END IF;
  IF NOT EXISTS(SELECT 1 FROM accounting.journal_templates t JOIN accounting.drafts d ON d.book_id=t.book_id JOIN accounting.business_events e ON e.id=j.event_id
   WHERE t.id=target_template AND t.book_id=j.book_id AND t.active=1 AND t.current_version=target_template_version
   AND d.id=j.draft_id AND d.version=target_draft_version AND d.status='draft' AND d.last_manual_edit_version=0 AND e.fact_version=j.expected_fact_version) THEN RETURN false; END IF;
  IF NOT accounting.book_allowed(j.book_id,true) OR NOT accounting.ai_event_open(j.event_id,j.firm_id,j.company_id) THEN RETURN false; END IF;
  IF accounting.source_workflow_blocked(j.book_id,(SELECT source_id FROM accounting.drafts WHERE id=j.draft_id AND book_id=j.book_id)) THEN RETURN false; END IF;
  stamp:=accounting.ocr_utc_now();
  UPDATE accounting.proposal_attempts SET phase='finished',status='succeeded',error_code='',error_detail='',finished_at=stamp WHERE job_id=j.id AND lease_token=target_token AND status='running';
  UPDATE accounting.proposal_jobs SET status='succeeded',lease_token=NULL,lease_until=NULL,worker_id=NULL,updated_at=stamp,finished_at=stamp,error_code='',error_detail='',
   result_template_id=target_template,result_template_version=target_template_version,result_draft_version=target_draft_version WHERE id=j.id;
  RETURN true;
 END
$$;
