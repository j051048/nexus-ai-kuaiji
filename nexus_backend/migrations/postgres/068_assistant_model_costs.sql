-- Chat shares the same company budget, immutable provenance and late settlement.
ALTER TABLE accounting.model_company_policies ADD COLUMN chat_reserve_micro BIGINT NOT NULL DEFAULT 0 CHECK(chat_reserve_micro BETWEEN 0 AND 999999999999990000);
ALTER TABLE accounting.model_costs DROP CONSTRAINT model_costs_kind_check;
ALTER TABLE accounting.model_costs ADD CONSTRAINT model_costs_kind_check CHECK(kind IN ('ocr','proposal','chat'));
ALTER TABLE accounting.model_costs ADD COLUMN chat_turn_id TEXT UNIQUE;
ALTER TABLE accounting.model_costs ADD CONSTRAINT model_cost_chat_scope FOREIGN KEY(chat_turn_id,firm_id,company_id,created_by) REFERENCES accounting.assistant_turns(id,firm_id,company_id,created_by);
ALTER TABLE accounting.model_costs DROP CONSTRAINT model_cost_source_kind;
ALTER TABLE accounting.model_costs ADD CONSTRAINT model_cost_source_kind CHECK(
 (chat_turn_id IS NULL AND intake_run_id IS NULL AND run_id IS NOT NULL AND event_id IS NOT NULL AND kind IN('ocr','proposal')) OR
 (chat_turn_id IS NULL AND intake_run_id IS NOT NULL AND intake_run_id=id AND run_id IS NULL AND event_id IS NULL AND kind='ocr') OR
 (chat_turn_id IS NOT NULL AND chat_turn_id=id AND run_id IS NULL AND intake_run_id IS NULL AND event_id IS NULL AND kind='chat'));
CREATE FUNCTION accounting.model_cost_chat_identity_guard() RETURNS TRIGGER LANGUAGE plpgsql SET search_path=pg_catalog,accounting,pg_temp AS $$
 BEGIN
  IF NEW.chat_turn_id IS DISTINCT FROM OLD.chat_turn_id THEN RAISE EXCEPTION 'Chat cost source is immutable' USING ERRCODE='23514'; END IF;
  RETURN NEW;
 END
$$;
CREATE TRIGGER model_cost_chat_identity BEFORE UPDATE ON accounting.model_costs FOR EACH ROW EXECUTE FUNCTION accounting.model_cost_chat_identity_guard();
CREATE FUNCTION accounting.reserve_assistant_model_cost(target_run TEXT,target_token TEXT,target_provider TEXT,target_model TEXT,target_pricing TEXT,target_reserve BIGINT) RETURNS TEXT
 LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE r accounting.assistant_turns; c accounting.model_costs; p accounting.model_company_policies; period_key TEXT; exposure NUMERIC; stamp TEXT; reserve_amount BIGINT;
 BEGIN
  PERFORM pg_advisory_xact_lock(6420031786281903101);
  SELECT * INTO r FROM accounting.assistant_turns WHERE id=target_run AND firm_id=accounting.actor_firm() AND created_by=accounting.actor_user();
  IF r.id IS NULL THEN RETURN 'authorization_changed'; END IF;
  IF NOT pg_has_role(session_user,'accounting_worker','MEMBER') OR NOT accounting.assistant_check(target_run,current_setting('app.assistant_token',true)) OR r.sent_at IS NOT NULL THEN RETURN 'authorization_changed'; END IF;
  IF target_provider IS DISTINCT FROM r.provider OR target_model IS DISTINCT FROM r.model OR target_token IS NULL OR length(target_token) NOT BETWEEN 16 AND 200 THEN RETURN 'run_invalid'; END IF;
  IF NOT accounting.valid_model_pricing(target_pricing,target_provider,target_model) OR target_reserve IS NULL OR target_reserve NOT BETWEEN 0 AND 999999999999990000 THEN RETURN 'pricing_invalid'; END IF;
  SELECT * INTO c FROM accounting.model_costs WHERE id=target_run;
  IF c.id IS NOT NULL THEN
   IF c.charge_token<>target_token OR c.pricing_json::jsonb IS DISTINCT FROM target_pricing::jsonb THEN RETURN 'token_mismatch'; END IF;
   RETURN c.state;
  END IF;
  IF r.status<>'running' THEN RETURN 'run_invalid'; END IF;
  SELECT * INTO p FROM accounting.model_company_policies WHERE firm_id=r.firm_id AND company_id=r.company_id;
  period_key:=to_char(clock_timestamp() AT TIME ZONE 'Asia/Shanghai','YYYY-MM');reserve_amount:=target_reserve;
  IF p.quota_enabled=1 THEN
   IF NOT (target_pricing::jsonb->>'configured')::boolean THEN RETURN 'pricing_missing'; END IF;
   reserve_amount:=p.chat_reserve_micro;
   IF p.monthly_limit_micro<=0 OR reserve_amount<=0 THEN RETURN 'policy_invalid'; END IF;
   IF EXISTS(SELECT 1 FROM accounting.model_costs WHERE firm_id=r.firm_id AND company_id=r.company_id AND overrun=1) THEN RETURN 'quota_overrun'; END IF;
   SELECT coalesce(sum(CASE WHEN period=period_key THEN CASE WHEN state='released' THEN 0 ELSE coalesce(confirmed_micro,estimated_micro,reserved_micro) END
    WHEN period<period_key AND state IN ('reserved','sent','uncertain') THEN reserved_micro ELSE 0 END),0) INTO exposure
    FROM accounting.model_costs WHERE firm_id=r.firm_id AND company_id=r.company_id AND period<=period_key;
   IF exposure+reserve_amount>p.monthly_limit_micro THEN RETURN 'quota_exhausted'; END IF;
  END IF;
  stamp:=accounting.ocr_utc_now();
  INSERT INTO accounting.model_costs(id,run_id,chat_turn_id,firm_id,company_id,event_id,kind,created_by,period,provider,model,charge_token,pricing_json,quota_enabled,reserved_micro,state,created_at)
   VALUES(r.id,NULL,r.id,r.firm_id,r.company_id,NULL,'chat',r.created_by,period_key,r.provider,r.model,target_token,target_pricing,coalesce(p.quota_enabled,0),reserve_amount,'reserved',stamp);
  RETURN 'reserved';
 END
$$;

CREATE OR REPLACE FUNCTION accounting.mark_model_cost_sent(target_run TEXT,target_token TEXT) RETURNS BOOLEAN
 LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE c accounting.model_costs;
 BEGIN
  PERFORM pg_advisory_xact_lock(6420031786281903101);
  SELECT * INTO c FROM accounting.model_costs WHERE id=target_run AND charge_token=target_token AND created_by=accounting.actor_user() AND firm_id=accounting.actor_firm();
  IF c.id IS NULL THEN RETURN false; END IF;
  IF c.chat_turn_id IS NOT NULL THEN
   IF NOT pg_has_role(session_user,'accounting_worker','MEMBER') OR NOT accounting.assistant_check(c.chat_turn_id,current_setting('app.assistant_token',true)) THEN RETURN false; END IF;
  ELSIF c.intake_run_id IS NOT NULL THEN
   IF NOT EXISTS(SELECT 1 FROM accounting.intake_runs r WHERE r.id=c.intake_run_id AND accounting.intake_process_allowed(r.intake_id)) THEN RETURN false; END IF;
  ELSIF NOT accounting.ai_event_processor(c.event_id,c.firm_id,c.company_id) OR NOT accounting.ai_event_open(c.event_id,c.firm_id,c.company_id) THEN RETURN false; END IF;
  IF c.state='sent' THEN RETURN true; END IF;
  IF c.state<>'reserved' THEN RETURN false; END IF;
  UPDATE accounting.model_costs SET state='sent',sent_at=accounting.ocr_utc_now(),version=version+1 WHERE id=c.id;
  RETURN true;
 END
$$;
CREATE OR REPLACE FUNCTION accounting.claim_ocr_job(target_worker TEXT,lease_seconds INTEGER DEFAULT 180,global_limit INTEGER DEFAULT 2)
 RETURNS SETOF accounting.ocr_jobs LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE j accounting.ocr_jobs; token TEXT; stamp TEXT; seq BIGINT;
 BEGIN
  IF length(btrim(target_worker)) NOT BETWEEN 1 AND 120 OR lease_seconds NOT BETWEEN 30 AND 600 OR global_limit NOT BETWEEN 1 AND 2 THEN
   RAISE EXCEPTION 'Invalid queue worker bounds' USING ERRCODE='22023';
  END IF;
  PERFORM pg_advisory_xact_lock(6420031786281903101);
  PERFORM accounting.recover_ocr_jobs();
  IF (SELECT count(*) FROM accounting.ocr_jobs WHERE status='running')+(SELECT count(*) FROM accounting.proposal_jobs WHERE status='running')+(SELECT count(*) FROM accounting.assistant_turns WHERE status='running')>=global_limit THEN RETURN; END IF;
  INSERT INTO accounting.ocr_company_schedule(firm_id,company_id)
   SELECT DISTINCT firm_id,company_id FROM accounting.ocr_jobs WHERE status IN ('queued','retry_wait') ON CONFLICT DO NOTHING;
  SELECT q.* INTO j FROM accounting.ocr_jobs q JOIN accounting.ocr_company_schedule s USING(firm_id,company_id)
   WHERE q.status IN ('queued','retry_wait') AND q.available_at::timestamptz<=clock_timestamp() AND q.attempt_count<q.max_attempts
   AND NOT EXISTS(SELECT 1 FROM accounting.ocr_jobs busy WHERE busy.firm_id=q.firm_id AND busy.company_id=q.company_id AND busy.status='running') AND NOT EXISTS(SELECT 1 FROM accounting.proposal_jobs busy WHERE busy.firm_id=q.firm_id AND busy.company_id=q.company_id AND busy.status='running')
   AND NOT EXISTS(SELECT 1 FROM accounting.assistant_turns busy WHERE busy.firm_id=q.firm_id AND busy.company_id=q.company_id AND busy.status='running')
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
CREATE OR REPLACE FUNCTION accounting.claim_proposal_job(target_worker TEXT,lease_seconds INTEGER DEFAULT 180,global_limit INTEGER DEFAULT 2)
 RETURNS SETOF accounting.proposal_jobs LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE j accounting.proposal_jobs; token TEXT; stamp TEXT; seq BIGINT;
 BEGIN
  IF length(btrim(target_worker)) NOT BETWEEN 1 AND 120 OR lease_seconds NOT BETWEEN 30 AND 600 OR global_limit NOT BETWEEN 1 AND 2 THEN
   RAISE EXCEPTION 'Invalid queue worker bounds' USING ERRCODE='22023'; END IF;
  PERFORM pg_advisory_xact_lock(6420031786281903101);
  PERFORM accounting.recover_proposal_jobs();
  IF (SELECT count(*) FROM accounting.ocr_jobs WHERE status='running')+(SELECT count(*) FROM accounting.proposal_jobs WHERE status='running')+(SELECT count(*) FROM accounting.assistant_turns WHERE status='running')>=global_limit THEN RETURN; END IF;
  INSERT INTO accounting.ocr_company_schedule(firm_id,company_id) SELECT DISTINCT firm_id,company_id FROM accounting.proposal_jobs WHERE status IN ('queued','retry_wait') ON CONFLICT DO NOTHING;
  SELECT q.* INTO j FROM accounting.proposal_jobs q JOIN accounting.ocr_company_schedule s USING(firm_id,company_id)
   WHERE q.status IN ('queued','retry_wait') AND q.available_at::timestamptz<=clock_timestamp() AND q.attempt_count<q.max_attempts
   AND NOT EXISTS(SELECT 1 FROM accounting.ocr_jobs busy WHERE busy.firm_id=q.firm_id AND busy.company_id=q.company_id AND busy.status='running')
   AND NOT EXISTS(SELECT 1 FROM accounting.proposal_jobs busy WHERE busy.firm_id=q.firm_id AND busy.company_id=q.company_id AND busy.status='running')
   AND NOT EXISTS(SELECT 1 FROM accounting.assistant_turns busy WHERE busy.firm_id=q.firm_id AND busy.company_id=q.company_id AND busy.status='running')
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
CREATE OR REPLACE FUNCTION accounting.claim_processing_job(target_worker TEXT,lease_seconds INTEGER DEFAULT 180,global_limit INTEGER DEFAULT 2)
 RETURNS JSONB LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE kind TEXT; o accounting.ocr_jobs; p accounting.proposal_jobs;
 BEGIN
  IF length(btrim(target_worker)) NOT BETWEEN 1 AND 120 OR lease_seconds NOT BETWEEN 30 AND 600 OR global_limit NOT BETWEEN 1 AND 2 THEN
   RAISE EXCEPTION 'Invalid queue worker bounds' USING ERRCODE='22023'; END IF;
  PERFORM pg_advisory_xact_lock(6420031786281903101);
  PERFORM accounting.recover_ocr_jobs();PERFORM accounting.recover_proposal_jobs();
  IF (SELECT count(*) FROM accounting.ocr_jobs WHERE status='running')+(SELECT count(*) FROM accounting.proposal_jobs WHERE status='running')+(SELECT count(*) FROM accounting.assistant_turns WHERE status='running')>=global_limit THEN RETURN NULL; END IF;
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
   AND NOT EXISTS(SELECT 1 FROM accounting.assistant_turns busy WHERE busy.firm_id=q.firm_id AND busy.company_id=q.company_id AND busy.status='running')
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
REVOKE ALL ON FUNCTION accounting.model_cost_chat_identity_guard(),accounting.reserve_assistant_model_cost(TEXT,TEXT,TEXT,TEXT,TEXT,BIGINT) FROM PUBLIC,accounting_worker;
GRANT EXECUTE ON FUNCTION accounting.reserve_assistant_model_cost(TEXT,TEXT,TEXT,TEXT,TEXT,BIGINT) TO accounting_app;
