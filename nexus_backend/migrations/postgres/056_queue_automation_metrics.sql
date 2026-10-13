-- Durable opt-in scheduling and per-task cross-worker performance evidence.
CREATE TABLE accounting.queue_automation_policies(firm_id TEXT NOT NULL,company_id TEXT NOT NULL,enabled INTEGER NOT NULL CHECK(enabled IN(0,1)),allow_verified_supplements INTEGER NOT NULL CHECK(allow_verified_supplements IN(0,1)),version INTEGER NOT NULL CHECK(version>0),updated_by TEXT NOT NULL REFERENCES accounting.users(id),updated_at TEXT NOT NULL,last_scanned_at TEXT,PRIMARY KEY(firm_id,company_id),FOREIGN KEY(firm_id,company_id) REFERENCES accounting.firm_clients(firm_id,company_id));
CREATE TABLE accounting.proposal_resume_watch(job_id TEXT PRIMARY KEY REFERENCES accounting.proposal_jobs(id),generation INTEGER NOT NULL CHECK(generation>0),blocker TEXT NOT NULL,next_check_at TEXT NOT NULL,check_count INTEGER NOT NULL DEFAULT 0,claim_token TEXT);
CREATE INDEX idx_resume_due ON accounting.proposal_resume_watch(next_check_at,job_id);
CREATE TABLE accounting.proposal_resume_events(job_id TEXT NOT NULL REFERENCES accounting.proposal_jobs(id),generation INTEGER NOT NULL,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,book_id TEXT NOT NULL,event_id TEXT NOT NULL,execution_user_id TEXT NOT NULL REFERENCES accounting.users(id),reason_json TEXT NOT NULL CHECK(jsonb_typeof(reason_json::jsonb)='object'),created_at TEXT NOT NULL,PRIMARY KEY(job_id,generation));
CREATE TABLE accounting.queue_verified_evidence_versions(event_id TEXT NOT NULL,fact_version INTEGER NOT NULL,response_id TEXT NOT NULL UNIQUE REFERENCES accounting.case_responses(id),firm_id TEXT NOT NULL,company_id TEXT NOT NULL,book_id TEXT,source_id TEXT,draft_id TEXT,draft_version INTEGER,created_at TEXT NOT NULL,PRIMARY KEY(event_id,fact_version),FOREIGN KEY(event_id,firm_id,company_id) REFERENCES accounting.business_events(id,firm_id,company_id));
CREATE TABLE accounting.processing_job_metrics(job_kind TEXT NOT NULL CHECK(job_kind IN('ocr','proposal')),job_id TEXT NOT NULL,generation INTEGER NOT NULL,attempt_no INTEGER NOT NULL,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,event_id TEXT NOT NULL,book_id TEXT,stage TEXT NOT NULL CHECK(stage IN('dependency_wait','eligible_queue_wait','claim_lock','model_call','validation_persistence','ocr_cache_reuse')),duration_ms BIGINT NOT NULL CHECK(duration_ms BETWEEN 0 AND 2678400000),created_at TEXT NOT NULL,PRIMARY KEY(job_kind,job_id,generation,attempt_no,stage),FOREIGN KEY(firm_id,company_id) REFERENCES accounting.firm_clients(firm_id,company_id));
CREATE INDEX idx_job_metrics_company ON accounting.processing_job_metrics(firm_id,company_id,created_at,stage);
CREATE INDEX idx_resume_events_book ON accounting.proposal_resume_events(firm_id,book_id,created_at);
CREATE TABLE accounting.queue_resume_signals(firm_id TEXT NOT NULL,company_id TEXT NOT NULL,event_id TEXT NOT NULL DEFAULT '',kind TEXT NOT NULL,revision INTEGER NOT NULL DEFAULT 1,signalled_at TEXT NOT NULL,PRIMARY KEY(firm_id,company_id,event_id,kind),FOREIGN KEY(firm_id,company_id) REFERENCES accounting.firm_clients(firm_id,company_id));
DO $$ DECLARE table_name TEXT; BEGIN
 FOREACH table_name IN ARRAY ARRAY['queue_automation_policies','proposal_resume_watch','proposal_resume_events','queue_verified_evidence_versions','processing_job_metrics','queue_resume_signals'] LOOP
  EXECUTE format('ALTER TABLE accounting.%I ENABLE ROW LEVEL SECURITY',table_name);
  EXECUTE format('ALTER TABLE accounting.%I FORCE ROW LEVEL SECURITY',table_name);
  EXECUTE format('REVOKE ALL ON accounting.%I FROM PUBLIC,accounting_app,accounting_worker',table_name);
 END LOOP;
 FOREACH table_name IN ARRAY ARRAY['proposal_resume_events','queue_verified_evidence_versions','processing_job_metrics'] LOOP
  EXECUTE format('CREATE TRIGGER queue_history_immutable BEFORE UPDATE OR DELETE ON accounting.%I FOR EACH ROW EXECUTE FUNCTION accounting.ai_immutable_guard()',table_name);
 END LOOP;
END $$;
GRANT SELECT,INSERT,UPDATE ON accounting.queue_automation_policies TO accounting_app;
GRANT SELECT ON accounting.proposal_resume_watch,accounting.proposal_resume_events,accounting.queue_verified_evidence_versions,accounting.processing_job_metrics,accounting.queue_resume_signals TO accounting_app;
CREATE POLICY automation_policy_read ON accounting.queue_automation_policies FOR SELECT TO accounting_app USING(firm_id=accounting.actor_firm() AND accounting.company_visible(company_id));
CREATE POLICY automation_policy_insert ON accounting.queue_automation_policies FOR INSERT TO accounting_app WITH CHECK(firm_id=accounting.actor_firm() AND accounting.company_manager(company_id) AND updated_by=accounting.actor_user() AND version=1 AND last_scanned_at IS NULL);
CREATE POLICY automation_policy_update ON accounting.queue_automation_policies FOR UPDATE TO accounting_app USING(firm_id=accounting.actor_firm() AND accounting.company_manager(company_id)) WITH CHECK(firm_id=accounting.actor_firm() AND accounting.company_manager(company_id) AND updated_by=accounting.actor_user());
CREATE POLICY resume_watch_read ON accounting.proposal_resume_watch FOR SELECT TO accounting_app USING(accounting.proposal_scope(job_id));
CREATE POLICY resume_event_read ON accounting.proposal_resume_events FOR SELECT TO accounting_app USING(firm_id=accounting.actor_firm() AND accounting.proposal_scope(job_id));
CREATE POLICY evidence_version_read ON accounting.queue_verified_evidence_versions FOR SELECT TO accounting_app USING(firm_id=accounting.actor_firm() AND accounting.business_event_visible(event_id,firm_id,company_id));
CREATE POLICY job_metrics_read ON accounting.processing_job_metrics FOR SELECT TO accounting_app USING(firm_id=accounting.actor_firm() AND accounting.company_manager(company_id));
CREATE POLICY resume_signals_read ON accounting.queue_resume_signals FOR SELECT TO accounting_app USING(firm_id=accounting.actor_firm() AND accounting.company_visible(company_id) AND (event_id='' OR accounting.business_event_visible(event_id,firm_id,company_id)));
CREATE FUNCTION accounting.capture_resume_signal() RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE target_event TEXT:='';signal_kind TEXT;
 BEGIN
 IF TG_TABLE_NAME='case_tasks' THEN
  IF NEW.state<>'closed' OR OLD.state='closed' THEN RETURN NEW;END IF;target_event:=COALESCE(NEW.event_id,'');signal_kind:='case_closed';
 ELSIF TG_TABLE_NAME='ai_company_settings' THEN signal_kind:='processing_changed';
 ELSE signal_kind:='budget_changed';END IF;
 INSERT INTO accounting.queue_resume_signals(firm_id,company_id,event_id,kind,signalled_at) VALUES(NEW.firm_id,NEW.company_id,target_event,signal_kind,NEW.updated_at) ON CONFLICT(firm_id,company_id,event_id,kind) DO UPDATE SET revision=accounting.queue_resume_signals.revision+1,signalled_at=excluded.signalled_at;
 UPDATE accounting.proposal_resume_watch SET next_check_at=NEW.updated_at,claim_token=NULL WHERE job_id IN(SELECT id FROM accounting.proposal_jobs WHERE firm_id=NEW.firm_id AND company_id=NEW.company_id AND (target_event='' OR event_id=target_event) AND status='needs_attention' ORDER BY created_at,id LIMIT 200);
 RETURN NEW;
END $$;
CREATE TRIGGER resume_signal_case_closed AFTER UPDATE OF state ON accounting.case_tasks FOR EACH ROW EXECUTE FUNCTION accounting.capture_resume_signal();
CREATE TRIGGER resume_signal_processing AFTER UPDATE ON accounting.ai_company_settings FOR EACH ROW EXECUTE FUNCTION accounting.capture_resume_signal();
CREATE TRIGGER resume_signal_policy AFTER UPDATE ON accounting.model_company_policies FOR EACH ROW EXECUTE FUNCTION accounting.capture_resume_signal();
CREATE FUNCTION accounting.automation_policy_guard() RETURNS TRIGGER LANGUAGE plpgsql SET search_path=pg_catalog,accounting,pg_temp AS $$ BEGIN
 IF (NEW.firm_id,NEW.company_id) IS DISTINCT FROM(OLD.firm_id,OLD.company_id) OR (current_user='accounting_app' AND (NEW.version<>OLD.version+1 OR NEW.last_scanned_at IS DISTINCT FROM OLD.last_scanned_at)) THEN RAISE EXCEPTION 'Policy scope/version are protected' USING ERRCODE='23514';END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER automation_policy_guard BEFORE UPDATE ON accounting.queue_automation_policies FOR EACH ROW EXECUTE FUNCTION accounting.automation_policy_guard();
CREATE FUNCTION accounting.capture_resume_watch() RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$ BEGIN
 IF NEW.status='needs_attention' AND NEW.error_code IN('processing_disabled','ocr_unavailable','provider_unavailable','evidence_missing','case_pending','queue_capacity','quota_exhausted','pricing_missing','policy_invalid') THEN
  IF TG_OP='INSERT' THEN NULL; ELSE IF (NEW.status,NEW.error_code,NEW.generation) IS NOT DISTINCT FROM(OLD.status,OLD.error_code,OLD.generation) THEN RETURN NEW;END IF;END IF;
  INSERT INTO accounting.proposal_resume_watch(job_id,generation,blocker,next_check_at) VALUES(NEW.id,NEW.generation,NEW.error_code,NEW.updated_at) ON CONFLICT(job_id) DO UPDATE SET generation=excluded.generation,blocker=excluded.blocker,next_check_at=excluded.next_check_at,claim_token=NULL;
 END IF;RETURN NEW;
END $$;
CREATE TRIGGER resume_watch_insert AFTER INSERT OR UPDATE ON accounting.proposal_jobs FOR EACH ROW EXECUTE FUNCTION accounting.capture_resume_watch();
INSERT INTO accounting.proposal_resume_watch(job_id,generation,blocker,next_check_at) SELECT id,generation,error_code,updated_at FROM accounting.proposal_jobs WHERE status='needs_attention' AND error_code IN('processing_disabled','ocr_unavailable','provider_unavailable','evidence_missing','case_pending','queue_capacity','quota_exhausted','pricing_missing','policy_invalid');
CREATE FUNCTION accounting.capture_verified_evidence_version() RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$ BEGIN
 IF NEW.fact_version=OLD.fact_version+1 AND NEW.payload=OLD.payload THEN
  INSERT INTO accounting.queue_verified_evidence_versions(event_id,fact_version,response_id,firm_id,company_id,book_id,source_id,draft_id,draft_version,created_at)
  SELECT NEW.id,NEW.fact_version,r.id,NEW.firm_id,NEW.company_id,m.book_id,m.source_id,m.draft_id,d.version,NEW.updated_at FROM accounting.case_responses r JOIN accounting.case_tasks t ON t.id=r.case_id LEFT JOIN accounting.business_mappings m ON m.event_id=t.event_id LEFT JOIN accounting.drafts d ON d.id=m.draft_id AND d.book_id=m.book_id WHERE t.event_id=NEW.id AND r.state='accepted' AND r.reviewed_by<>r.created_by AND t.state='in_review' AND t.evidence_revision=t.accepted_fact_revision+1 AND NOT EXISTS(SELECT 1 FROM accounting.queue_verified_evidence_versions v WHERE v.response_id=r.id) ORDER BY r.reviewed_at DESC,r.id DESC LIMIT 1;
 END IF;RETURN NEW;
END $$;
CREATE TRIGGER queue_capture_evidence_fact AFTER UPDATE OF fact_version ON accounting.business_events FOR EACH ROW EXECUTE FUNCTION accounting.capture_verified_evidence_version();

CREATE FUNCTION accounting.worker_claim_resume_candidates(target_limit INTEGER DEFAULT 12) RETURNS JSONB
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE item RECORD;token TEXT;stamp TEXT;result JSONB:='[]';
 BEGIN
 IF NOT pg_has_role(session_user,'accounting_worker','MEMBER') OR target_limit IS NULL OR target_limit NOT BETWEEN 1 AND 12 THEN RAISE EXCEPTION 'Bounded worker scope required' USING ERRCODE='42501';END IF;
 PERFORM pg_advisory_xact_lock(6420031786281903101);stamp:=accounting.ocr_utc_now();
 FOR item IN WITH candidates AS(
  SELECT w.job_id,w.generation,j.firm_id,j.company_id,j.execution_user_id,p.last_scanned_at,w.next_check_at,
   row_number() OVER(PARTITION BY j.firm_id,j.company_id ORDER BY w.next_check_at,w.job_id) AS company_rank
  FROM accounting.proposal_resume_watch w JOIN accounting.proposal_jobs j ON j.id=w.job_id JOIN accounting.queue_automation_policies p ON p.firm_id=j.firm_id AND p.company_id=j.company_id JOIN accounting.firm_clients fc ON fc.firm_id=j.firm_id AND fc.company_id=j.company_id
  WHERE p.enabled=1 AND fc.status='active' AND j.status='needs_attention' AND w.generation=j.generation AND w.next_check_at::timestamptz<=clock_timestamp())
  SELECT * FROM candidates WHERE company_rank=1 ORDER BY COALESCE(last_scanned_at,''),next_check_at,job_id LIMIT target_limit LOOP
  token:=md5(random()::text||clock_timestamp()::text||item.job_id);
  UPDATE accounting.proposal_resume_watch SET claim_token=token,next_check_at=to_char((clock_timestamp()+interval '60 seconds') AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"+00:00"'),check_count=check_count+1 WHERE job_id=item.job_id;
  UPDATE accounting.queue_automation_policies SET last_scanned_at=stamp WHERE firm_id=item.firm_id AND company_id=item.company_id;
  result:=result||jsonb_build_array(jsonb_build_object('job_id',item.job_id,'generation',item.generation,'firm_id',item.firm_id,'company_id',item.company_id,'execution_user_id',item.execution_user_id,'claim_token',token));
 END LOOP;RETURN result;
END $$;
CREATE FUNCTION accounting.worker_resume_proposal(target_job TEXT,target_token TEXT,target_config TEXT,target_policy TEXT) RETURNS BOOLEAN
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE j accounting.proposal_jobs;w accounting.proposal_resume_watch;p accounting.queue_automation_policies;e accounting.business_events;d accounting.drafts;stamp TEXT;accepted JSONB;
 BEGIN
 IF NOT pg_has_role(session_user,'accounting_worker','MEMBER') THEN RAISE EXCEPTION 'Worker scope required' USING ERRCODE='42501';END IF;
 IF target_config IS NULL OR target_config!~'^[a-f0-9]{64}$' OR target_policy IS NULL OR target_policy!~'^[a-f0-9]{64}$' THEN RAISE EXCEPTION 'Configuration fingerprints required' USING ERRCODE='23514';END IF;
 PERFORM pg_advisory_xact_lock(6420031786281903101);
 SELECT * INTO j FROM accounting.proposal_jobs WHERE id=target_job FOR UPDATE;
 SELECT * INTO w FROM accounting.proposal_resume_watch WHERE job_id=target_job FOR UPDATE;
 IF j.id IS NULL OR w.job_id IS NULL OR w.claim_token IS DISTINCT FROM target_token OR w.next_check_at::timestamptz<=clock_timestamp() OR j.generation<>w.generation OR j.status<>'needs_attention' OR j.error_code NOT IN('processing_disabled','ocr_unavailable','provider_unavailable','evidence_missing','case_pending','queue_capacity','quota_exhausted','pricing_missing','policy_invalid') OR j.current_run_id IS NOT NULL OR j.attempt_count>=30 THEN RETURN false;END IF;
 IF j.execution_user_id<>accounting.actor_user() OR j.firm_id<>accounting.actor_firm() OR NOT accounting.proposal_scope(j.id,true) OR NOT accounting.ai_event_processor(j.event_id,j.firm_id,j.company_id) THEN RETURN false;END IF;
 SELECT * INTO p FROM accounting.queue_automation_policies WHERE firm_id=j.firm_id AND company_id=j.company_id;
 SELECT * INTO e FROM accounting.business_events WHERE id=j.event_id;
 SELECT * INTO d FROM accounting.drafts WHERE id=j.draft_id AND book_id=j.book_id;
 IF p.enabled IS DISTINCT FROM 1 OR d.status<>'draft' OR d.last_manual_edit_version<>0 OR d.version<>j.expected_draft_version OR target_policy<>j.policy_fingerprint OR e.status<>'handed_off'
  OR NOT EXISTS(SELECT 1 FROM accounting.business_mappings m WHERE m.event_id=j.event_id AND m.book_id=j.book_id AND m.draft_id=j.draft_id AND m.source_id=d.source_id)
  OR accounting.source_inactive(j.book_id,d.source_id) OR accounting.revision_source_blocked(j.book_id,d.source_id) OR accounting.case_source_blocked(j.book_id,d.source_id)
  OR NOT EXISTS(SELECT 1 FROM accounting.ai_company_settings s WHERE s.firm_id=j.firm_id AND s.company_id=j.company_id AND s.enabled=1)
  OR EXISTS(SELECT 1 FROM accounting.proposal_attempts a WHERE a.job_id=j.id AND a.ai_run_id IS NOT NULL)
  OR EXISTS(SELECT 1 FROM accounting.proposal_jobs other WHERE other.id<>j.id AND other.draft_id=j.draft_id AND other.status IN('waiting','queued','running','retry_wait')) THEN RETURN false;END IF;
 IF e.fact_version<>j.expected_fact_version THEN
  IF p.allow_verified_supplements<>1 OR e.fact_version<j.expected_fact_version OR (SELECT count(*) FROM accounting.queue_verified_evidence_versions v JOIN accounting.case_responses r ON r.id=v.response_id JOIN accounting.case_tasks t ON t.id=r.case_id WHERE v.event_id=e.id AND v.fact_version>j.expected_fact_version AND v.fact_version<=e.fact_version AND v.book_id=j.book_id AND v.draft_id=j.draft_id AND v.source_id=d.source_id AND v.draft_version=d.version AND r.state='accepted' AND r.reviewed_by<>r.created_by AND t.state='closed' AND t.accepted_fact_revision=t.evidence_revision)<>e.fact_version-j.expected_fact_version THEN RETURN false;END IF;
 END IF;
 SELECT COALESCE(jsonb_agg(v.response_id ORDER BY v.fact_version),'[]'::jsonb) INTO accepted FROM accounting.queue_verified_evidence_versions v WHERE v.event_id=e.id AND v.fact_version>j.expected_fact_version AND v.fact_version<=e.fact_version;
 stamp:=accounting.ocr_utc_now();
 INSERT INTO accounting.proposal_resume_events(job_id,generation,firm_id,company_id,book_id,event_id,execution_user_id,reason_json,created_at) VALUES(j.id,j.generation+1,j.firm_id,j.company_id,j.book_id,j.event_id,j.execution_user_id,jsonb_build_object('blocker',j.error_code,'previous_generation',j.generation,'old_fact_version',j.expected_fact_version,'new_fact_version',e.fact_version,'old_draft_version',j.expected_draft_version,'new_draft_version',d.version,'old_configuration',j.configuration_fingerprint,'new_configuration',target_config,'accepted_response_ids',accepted,'policy_version',p.version)::text,stamp);
 UPDATE accounting.proposal_jobs SET status='queued',expected_fact_version=e.fact_version,configuration_fingerprint=target_config,generation=generation+1,max_attempts=least(30,attempt_count+3),available_at=stamp,dependency_deadline=to_char((clock_timestamp()+interval '30 minutes') AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"+00:00"'),error_code='',error_detail='',finished_at=NULL,updated_at=stamp WHERE id=j.id;
 RETURN true;
END $$;

CREATE FUNCTION accounting.capture_job_wait_metric() RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE kind TEXT;stage_name TEXT;gen INTEGER;target_book TEXT;started TEXT;elapsed BIGINT;
 BEGIN
 IF OLD.status='waiting' AND NEW.status='queued' THEN stage_name:='dependency_wait';started:=OLD.updated_at;
 ELSIF OLD.status IN('queued','retry_wait') AND NEW.status='running' THEN stage_name:='eligible_queue_wait';started:=greatest(OLD.updated_at,OLD.available_at);
 ELSE RETURN NEW;END IF;
 IF TG_TABLE_NAME='proposal_jobs' THEN kind:='proposal';gen:=NEW.generation;target_book:=NEW.book_id;ELSE kind:='ocr';gen:=1;target_book:=NULL;END IF;
 elapsed:=greatest(0,least(2678400000,(extract(epoch FROM(NEW.updated_at::timestamptz-started::timestamptz))*1000)::bigint));
 INSERT INTO accounting.processing_job_metrics(job_kind,job_id,generation,attempt_no,firm_id,company_id,event_id,book_id,stage,duration_ms,created_at) VALUES(kind,NEW.id,gen,NEW.attempt_count,NEW.firm_id,NEW.company_id,NEW.event_id,target_book,stage_name,elapsed,NEW.updated_at) ON CONFLICT DO NOTHING;
 RETURN NEW;
END $$;
CREATE TRIGGER proposal_wait_metric AFTER UPDATE OF status ON accounting.proposal_jobs FOR EACH ROW EXECUTE FUNCTION accounting.capture_job_wait_metric();
CREATE TRIGGER ocr_wait_metric AFTER UPDATE OF status ON accounting.ocr_jobs FOR EACH ROW EXECUTE FUNCTION accounting.capture_job_wait_metric();
CREATE FUNCTION accounting.worker_record_job_metric(target_kind TEXT,target_job TEXT,target_token TEXT,target_stage TEXT,target_duration BIGINT) RETURNS BOOLEAN
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE j RECORD;
 BEGIN
 IF NOT pg_has_role(session_user,'accounting_worker','MEMBER') OR target_kind IS NULL OR target_kind NOT IN('ocr','proposal') OR target_stage IS NULL OR target_stage NOT IN('claim_lock','model_call','validation_persistence','ocr_cache_reuse') OR target_duration IS NULL OR target_duration NOT BETWEEN 0 AND 86400000 THEN RAISE EXCEPTION 'Bounded worker metric required' USING ERRCODE='42501';END IF;
 IF target_kind='proposal' THEN SELECT id,firm_id,company_id,event_id,book_id,generation,attempt_count INTO j FROM accounting.proposal_jobs WHERE id=target_job AND status='running' AND lease_token=target_token AND lease_until::timestamptz>clock_timestamp();
 ELSE SELECT id,firm_id,company_id,event_id,NULL::text AS book_id,1 AS generation,attempt_count INTO j FROM accounting.ocr_jobs WHERE id=target_job AND status='running' AND lease_token=target_token AND lease_until::timestamptz>clock_timestamp();END IF;
 IF NOT FOUND THEN RETURN false;END IF;
 INSERT INTO accounting.processing_job_metrics(job_kind,job_id,generation,attempt_no,firm_id,company_id,event_id,book_id,stage,duration_ms,created_at) VALUES(target_kind,j.id,j.generation,j.attempt_count,j.firm_id,j.company_id,j.event_id,j.book_id,target_stage,target_duration,accounting.ocr_utc_now()) ON CONFLICT DO NOTHING;
 RETURN true;
END $$;
CREATE FUNCTION accounting.cancel_resumable_proposal(target_job TEXT,target_reason TEXT) RETURNS BOOLEAN
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$ BEGIN
 IF target_reason IS NULL OR length(btrim(target_reason)) NOT BETWEEN 1 AND 1000 OR NOT accounting.proposal_scope(target_job) THEN RAISE EXCEPTION 'Current proposal editor and cancellation reason required' USING ERRCODE='42501';END IF;
 PERFORM pg_advisory_xact_lock(6420031786281903101);
 UPDATE accounting.proposal_jobs SET status='cancelled',error_code='cancelled',error_detail=btrim(target_reason),updated_at=accounting.ocr_utc_now(),finished_at=accounting.ocr_utc_now() WHERE id=target_job AND status='needs_attention';
 RETURN FOUND;
END $$;
REVOKE ALL ON FUNCTION accounting.cancel_resumable_proposal(TEXT,TEXT) FROM PUBLIC,accounting_app,accounting_worker;
GRANT EXECUTE ON FUNCTION accounting.cancel_resumable_proposal(TEXT,TEXT) TO accounting_app;
REVOKE ALL ON FUNCTION accounting.automation_policy_guard(),accounting.capture_resume_signal(),accounting.capture_resume_watch(),accounting.capture_verified_evidence_version(),accounting.worker_claim_resume_candidates(INTEGER),accounting.worker_resume_proposal(TEXT,TEXT,TEXT,TEXT),accounting.capture_job_wait_metric(),accounting.worker_record_job_metric(TEXT,TEXT,TEXT,TEXT,BIGINT) FROM PUBLIC,accounting_app,accounting_worker;
GRANT EXECUTE ON FUNCTION accounting.worker_claim_resume_candidates(INTEGER),accounting.worker_resume_proposal(TEXT,TEXT,TEXT,TEXT),accounting.worker_record_job_metric(TEXT,TEXT,TEXT,TEXT,BIGINT) TO accounting_worker;
