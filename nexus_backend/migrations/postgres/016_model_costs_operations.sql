-- Cost reservations are independent of ledger approval. All amounts are RMB
-- micro-units; supplier prices are explicit snapshots, never built-in quotes.
CREATE TABLE accounting.model_company_policies (
 firm_id TEXT NOT NULL,company_id TEXT NOT NULL,quota_enabled INTEGER NOT NULL DEFAULT 0 CHECK(quota_enabled IN (0,1)),
 monthly_limit_micro BIGINT NOT NULL DEFAULT 0 CHECK(monthly_limit_micro BETWEEN 0 AND 999999999999990000),
 ocr_reserve_micro BIGINT NOT NULL DEFAULT 0 CHECK(ocr_reserve_micro BETWEEN 0 AND 999999999999990000),
 proposal_reserve_micro BIGINT NOT NULL DEFAULT 0 CHECK(proposal_reserve_micro BETWEEN 0 AND 999999999999990000),
 failure_threshold INTEGER NOT NULL DEFAULT 3 CHECK(failure_threshold BETWEEN 1 AND 1000),
 failure_window_minutes INTEGER NOT NULL DEFAULT 60 CHECK(failure_window_minutes BETWEEN 1 AND 10080),
 backlog_threshold INTEGER NOT NULL DEFAULT 10 CHECK(backlog_threshold BETWEEN 1 AND 10000),
 backlog_age_minutes INTEGER NOT NULL DEFAULT 60 CHECK(backlog_age_minutes BETWEEN 1 AND 43200),
 queue_wait_minutes INTEGER NOT NULL DEFAULT 10 CHECK(queue_wait_minutes BETWEEN 1 AND 10080),
 worker_stale_seconds INTEGER NOT NULL DEFAULT 90 CHECK(worker_stale_seconds BETWEEN 15 AND 3600),
 version INTEGER NOT NULL DEFAULT 1 CHECK(version>0),updated_by TEXT NOT NULL REFERENCES accounting.users(id),updated_at TEXT NOT NULL,
 PRIMARY KEY(firm_id,company_id),FOREIGN KEY(firm_id,company_id) REFERENCES accounting.firm_clients(firm_id,company_id),
 CHECK(quota_enabled=0 OR (monthly_limit_micro>0 AND ocr_reserve_micro>0 AND proposal_reserve_micro>0))
);
CREATE UNIQUE INDEX idx_ai_run_cost_scope ON accounting.ai_runs(id,event_id,firm_id,company_id,kind,created_by);
CREATE TABLE accounting.model_costs (
 id TEXT PRIMARY KEY,run_id TEXT NOT NULL UNIQUE CHECK(id=run_id),firm_id TEXT NOT NULL,company_id TEXT NOT NULL,event_id TEXT NOT NULL,
 kind TEXT NOT NULL CHECK(kind IN ('ocr','proposal')),created_by TEXT NOT NULL REFERENCES accounting.users(id),
 period TEXT NOT NULL CHECK(period ~ '^[0-9]{4}-(0[1-9]|1[0-2])$'),provider TEXT NOT NULL,model TEXT NOT NULL,charge_token TEXT NOT NULL,
 pricing_json TEXT NOT NULL CHECK(jsonb_typeof(pricing_json::jsonb)='object'),quota_enabled INTEGER NOT NULL CHECK(quota_enabled IN (0,1)),
 reserved_micro BIGINT NOT NULL CHECK(reserved_micro BETWEEN 0 AND 999999999999990000),
 estimated_micro BIGINT CHECK(estimated_micro BETWEEN 0 AND 999999999999990000),confirmed_micro BIGINT CHECK(confirmed_micro BETWEEN 0 AND 999999999999990000),
 input_tokens BIGINT CHECK(input_tokens BETWEEN 0 AND 10000000),output_tokens BIGINT CHECK(output_tokens BETWEEN 0 AND 10000000),
 state TEXT NOT NULL CHECK(state IN ('reserved','sent','estimated','uncertain','released','reconciled')),
 version INTEGER NOT NULL DEFAULT 1 CHECK(version>0),created_at TEXT NOT NULL,sent_at TEXT,finished_at TEXT,
 overrun INTEGER NOT NULL DEFAULT 0 CHECK(overrun IN (0,1)),manual_reason TEXT NOT NULL DEFAULT '',manual_by TEXT REFERENCES accounting.users(id),
 FOREIGN KEY(run_id,event_id,firm_id,company_id,kind,created_by) REFERENCES accounting.ai_runs(id,event_id,firm_id,company_id,kind,created_by),
 UNIQUE(id,event_id,firm_id,company_id),
 CHECK((state IN ('reserved','sent') AND finished_at IS NULL) OR (state NOT IN ('reserved','sent') AND finished_at IS NOT NULL)),
 CHECK(state<>'estimated' OR estimated_micro IS NOT NULL),
 CHECK(state<>'reconciled' OR (confirmed_micro IS NOT NULL AND manual_by IS NOT NULL AND length(btrim(manual_reason))>0))
);
CREATE INDEX idx_model_costs_month ON accounting.model_costs(firm_id,company_id,period,state);
CREATE TABLE accounting.ops_exception_snapshots (
 event_id TEXT PRIMARY KEY,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,
 blocking_count INTEGER NOT NULL CHECK(blocking_count BETWEEN 0 AND 10000),warning_count INTEGER NOT NULL CHECK(warning_count BETWEEN 0 AND 10000),
 oldest_opened_at TEXT,context_fingerprint TEXT NOT NULL,updated_at TEXT NOT NULL,
 FOREIGN KEY(event_id,firm_id,company_id) REFERENCES accounting.business_events(id,firm_id,company_id)
);
CREATE INDEX idx_ops_exception_company ON accounting.ops_exception_snapshots(firm_id,company_id,blocking_count);
CREATE TABLE accounting.ops_alerts (
 id TEXT PRIMARY KEY,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,
 code TEXT NOT NULL CHECK(code IN ('budget_exhausted','quota_overrun','usage_uncertain','task_failures','exception_backlog','queue_wait','worker_unavailable')),
 severity TEXT NOT NULL CHECK(severity IN ('warning','critical')),state TEXT NOT NULL CHECK(state IN ('open','acknowledged','resolved')),
 fingerprint TEXT NOT NULL,payload_json TEXT NOT NULL CHECK(jsonb_typeof(payload_json::jsonb)='object'),
 opened_at TEXT NOT NULL,last_seen_at TEXT NOT NULL,resolved_at TEXT,version INTEGER NOT NULL DEFAULT 1 CHECK(version>0),
 UNIQUE(firm_id,company_id,code),UNIQUE(id,firm_id,company_id),
 FOREIGN KEY(firm_id,company_id) REFERENCES accounting.firm_clients(firm_id,company_id),
 CHECK((state='resolved' AND resolved_at IS NOT NULL) OR (state<>'resolved' AND resolved_at IS NULL))
);
CREATE TABLE accounting.ops_alert_actions (
 id TEXT PRIMARY KEY,alert_id TEXT NOT NULL,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,
 created_by TEXT NOT NULL REFERENCES accounting.users(id),reason TEXT NOT NULL CHECK(length(btrim(reason)) BETWEEN 1 AND 1000),created_at TEXT NOT NULL,
 FOREIGN KEY(alert_id,firm_id,company_id) REFERENCES accounting.ops_alerts(id,firm_id,company_id)
);
CREATE TABLE accounting.worker_heartbeats (
 worker_id TEXT PRIMARY KEY,started_at TEXT NOT NULL,heartbeat_at TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('active','idle','stopped')),inflight_count INTEGER NOT NULL CHECK(inflight_count BETWEEN 0 AND 2),
 CHECK(state='active' OR inflight_count=0)
);

CREATE FUNCTION accounting.ops_company_allowed(target_firm TEXT,target_company TEXT) RETURNS BOOLEAN
 LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 SELECT target_firm=accounting.actor_firm() AND accounting.actor_active()
 AND EXISTS(SELECT 1 FROM accounting.firm_clients WHERE firm_id=target_firm AND company_id=target_company AND status='active')
 AND (accounting.firm_manager(target_firm) OR EXISTS(SELECT 1 FROM accounting.company_memberships WHERE company_id=target_company
  AND user_id=accounting.actor_user() AND role IN ('owner','admin','finance')))
$$;
CREATE FUNCTION accounting.model_cost_identity_guard() RETURNS TRIGGER LANGUAGE plpgsql SET search_path=pg_catalog,accounting,pg_temp AS $$
 BEGIN
  IF (NEW.id,NEW.run_id,NEW.firm_id,NEW.company_id,NEW.event_id,NEW.kind,NEW.created_by,NEW.period,NEW.provider,NEW.model,
   NEW.charge_token,NEW.pricing_json,NEW.quota_enabled,NEW.reserved_micro,NEW.created_at)
  IS DISTINCT FROM (OLD.id,OLD.run_id,OLD.firm_id,OLD.company_id,OLD.event_id,OLD.kind,OLD.created_by,OLD.period,OLD.provider,OLD.model,
   OLD.charge_token,OLD.pricing_json,OLD.quota_enabled,OLD.reserved_micro,OLD.created_at)
  OR NEW.version<>OLD.version+1 OR (OLD.sent_at IS NOT NULL AND NEW.sent_at IS DISTINCT FROM OLD.sent_at)
  OR (OLD.state IN ('estimated','uncertain','released','reconciled') AND NEW.state<>'reconciled')
  OR (OLD.state IN ('estimated','uncertain','released','reconciled') AND (NEW.estimated_micro,NEW.input_tokens,NEW.output_tokens) IS DISTINCT FROM (OLD.estimated_micro,OLD.input_tokens,OLD.output_tokens)) THEN
   RAISE EXCEPTION 'Model cost evidence and provenance are immutable' USING ERRCODE='23514';
  END IF;
  RETURN NEW;
 END
$$;
CREATE TRIGGER model_cost_identity BEFORE UPDATE ON accounting.model_costs FOR EACH ROW EXECUTE FUNCTION accounting.model_cost_identity_guard();
CREATE TRIGGER model_costs_no_delete BEFORE DELETE ON accounting.model_costs FOR EACH ROW EXECUTE FUNCTION accounting.ai_immutable_guard();
CREATE TRIGGER ops_alert_actions_immutable BEFORE UPDATE OR DELETE ON accounting.ops_alert_actions FOR EACH ROW EXECUTE FUNCTION accounting.ai_immutable_guard();
CREATE FUNCTION accounting.ops_scope_guard() RETURNS TRIGGER LANGUAGE plpgsql SET search_path=pg_catalog,accounting,pg_temp AS $$
 BEGIN
  IF (NEW.firm_id,NEW.company_id) IS DISTINCT FROM (OLD.firm_id,OLD.company_id) THEN
   RAISE EXCEPTION 'Operations scope is immutable' USING ERRCODE='23514';
  END IF;
  IF TG_TABLE_NAME='ops_alerts' AND NEW.id IS DISTINCT FROM OLD.id THEN RAISE EXCEPTION 'Alert identity is immutable' USING ERRCODE='23514'; END IF;
  RETURN NEW;
 END
$$;
CREATE TRIGGER model_policy_scope BEFORE UPDATE ON accounting.model_company_policies FOR EACH ROW EXECUTE FUNCTION accounting.ops_scope_guard();
CREATE TRIGGER ops_alert_scope BEFORE UPDATE ON accounting.ops_alerts FOR EACH ROW EXECUTE FUNCTION accounting.ops_scope_guard();
CREATE TRIGGER ops_snapshot_scope BEFORE UPDATE ON accounting.ops_exception_snapshots FOR EACH ROW EXECUTE FUNCTION accounting.ops_scope_guard();
DO $$ DECLARE item TEXT; BEGIN
 FOREACH item IN ARRAY ARRAY['model_company_policies','model_costs','ops_exception_snapshots','ops_alerts','ops_alert_actions','worker_heartbeats'] LOOP
  EXECUTE format('ALTER TABLE accounting.%I ENABLE ROW LEVEL SECURITY',item);
  EXECUTE format('ALTER TABLE accounting.%I FORCE ROW LEVEL SECURITY',item);
 END LOOP;
END $$;
GRANT SELECT,INSERT,UPDATE ON accounting.model_company_policies,accounting.ops_exception_snapshots,accounting.ops_alerts TO accounting_app;
GRANT SELECT ON accounting.model_costs TO accounting_app;
GRANT SELECT,INSERT ON accounting.ops_alert_actions TO accounting_app;
CREATE POLICY model_policy_read ON accounting.model_company_policies FOR SELECT TO accounting_app USING(accounting.ops_company_allowed(firm_id,company_id));
CREATE POLICY model_policy_create ON accounting.model_company_policies FOR INSERT TO accounting_app WITH CHECK(updated_by=accounting.actor_user() AND firm_id=accounting.actor_firm() AND accounting.company_manager(company_id));
CREATE POLICY model_policy_update ON accounting.model_company_policies FOR UPDATE TO accounting_app USING(firm_id=accounting.actor_firm() AND accounting.company_manager(company_id)) WITH CHECK(updated_by=accounting.actor_user() AND firm_id=accounting.actor_firm() AND accounting.company_manager(company_id));
CREATE POLICY model_costs_read ON accounting.model_costs FOR SELECT TO accounting_app USING(accounting.ops_company_allowed(firm_id,company_id));
CREATE POLICY ops_snapshot_read ON accounting.ops_exception_snapshots FOR SELECT TO accounting_app USING(accounting.ops_company_allowed(firm_id,company_id));
CREATE POLICY ops_snapshot_create ON accounting.ops_exception_snapshots FOR INSERT TO accounting_app WITH CHECK(accounting.ai_event_processor(event_id,firm_id,company_id));
CREATE POLICY ops_snapshot_update ON accounting.ops_exception_snapshots FOR UPDATE TO accounting_app USING(accounting.ai_event_processor(event_id,firm_id,company_id)) WITH CHECK(accounting.ai_event_processor(event_id,firm_id,company_id));
CREATE POLICY ops_alert_read ON accounting.ops_alerts FOR SELECT TO accounting_app USING(accounting.ops_company_allowed(firm_id,company_id));
CREATE POLICY ops_alert_create ON accounting.ops_alerts FOR INSERT TO accounting_app WITH CHECK(accounting.ops_company_allowed(firm_id,company_id));
CREATE POLICY ops_alert_update ON accounting.ops_alerts FOR UPDATE TO accounting_app USING(accounting.ops_company_allowed(firm_id,company_id)) WITH CHECK(accounting.ops_company_allowed(firm_id,company_id));
CREATE POLICY ops_alert_action_read ON accounting.ops_alert_actions FOR SELECT TO accounting_app USING(accounting.ops_company_allowed(firm_id,company_id));
CREATE POLICY ops_alert_action_create ON accounting.ops_alert_actions FOR INSERT TO accounting_app WITH CHECK(created_by=accounting.actor_user() AND accounting.ops_company_allowed(firm_id,company_id));

CREATE FUNCTION accounting.valid_model_pricing(pricing TEXT,expected_provider TEXT,expected_model TEXT) RETURNS BOOLEAN LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog AS $$
 DECLARE p JSONB; keys TEXT[]; field_name TEXT; value JSONB;
 BEGIN
  p:=pricing::jsonb;
  IF jsonb_typeof(p)<>'object' THEN RETURN false; END IF;
  SELECT array_agg(k ORDER BY k) INTO keys FROM jsonb_object_keys(p) k;
  IF keys IS DISTINCT FROM ARRAY['configured','input_per_million_micro','model','output_per_million_micro','provider']
   OR jsonb_typeof(p->'configured')<>'boolean' OR jsonb_typeof(p->'provider')<>'string' OR jsonb_typeof(p->'model')<>'string'
   OR p->>'provider' IS DISTINCT FROM expected_provider OR p->>'model' IS DISTINCT FROM expected_model THEN RETURN false; END IF;
  FOREACH field_name IN ARRAY ARRAY['input_per_million_micro','output_per_million_micro'] LOOP
   value:=p->field_name;
   IF jsonb_typeof(value)='null' THEN
    IF (p->>'configured')::boolean THEN RETURN false; END IF;
   ELSIF jsonb_typeof(value)<>'number' OR value::text !~ '^[0-9]+$' OR value::text::numeric>999999999999990000 THEN RETURN false; END IF;
  END LOOP;
  RETURN true;
 EXCEPTION WHEN others THEN RETURN false;
 END
$$;
CREATE FUNCTION accounting.reserve_model_cost(target_run TEXT,target_token TEXT,target_provider TEXT,target_model TEXT,target_pricing TEXT,target_reserve BIGINT) RETURNS TEXT
 LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE r accounting.ai_runs; c accounting.model_costs; p accounting.model_company_policies; period_key TEXT; exposure NUMERIC; stamp TEXT; reserve_amount BIGINT;
 BEGIN
  PERFORM pg_advisory_xact_lock(6420031786281903101);
  SELECT * INTO r FROM accounting.ai_runs WHERE id=target_run AND firm_id=accounting.actor_firm() AND created_by=accounting.actor_user();
  IF r.id IS NULL THEN RETURN 'authorization_changed'; END IF;
  IF NOT accounting.ai_event_processor(r.event_id,r.firm_id,r.company_id) OR NOT accounting.ai_event_open(r.event_id,r.firm_id,r.company_id) THEN RETURN 'authorization_changed'; END IF;
  IF target_provider IS DISTINCT FROM r.provider OR target_model IS DISTINCT FROM r.model OR target_token IS NULL OR length(target_token) NOT BETWEEN 16 AND 200 THEN RETURN 'run_invalid'; END IF;
  IF NOT accounting.valid_model_pricing(target_pricing,target_provider,target_model) OR target_reserve IS NULL OR target_reserve NOT BETWEEN 0 AND 999999999999990000 THEN RETURN 'pricing_invalid'; END IF;
  SELECT * INTO c FROM accounting.model_costs WHERE run_id=target_run;
  IF c.id IS NOT NULL THEN
   IF c.charge_token<>target_token OR c.pricing_json::jsonb IS DISTINCT FROM target_pricing::jsonb THEN RETURN 'token_mismatch'; END IF;
   RETURN c.state;
  END IF;
  IF r.status<>'running' THEN RETURN 'run_invalid'; END IF;
  SELECT * INTO p FROM accounting.model_company_policies WHERE firm_id=r.firm_id AND company_id=r.company_id;
  period_key:=to_char(clock_timestamp() AT TIME ZONE 'Asia/Shanghai','YYYY-MM');reserve_amount:=target_reserve;
  IF p.quota_enabled=1 THEN
   IF NOT (target_pricing::jsonb->>'configured')::boolean THEN RETURN 'pricing_missing'; END IF;
   reserve_amount:=CASE WHEN r.kind='ocr' THEN p.ocr_reserve_micro ELSE p.proposal_reserve_micro END;
   IF p.monthly_limit_micro<=0 OR reserve_amount<=0 THEN RETURN 'policy_invalid'; END IF;
   IF EXISTS(SELECT 1 FROM accounting.model_costs WHERE firm_id=r.firm_id AND company_id=r.company_id AND period=period_key AND overrun=1) THEN RETURN 'quota_overrun'; END IF;
   SELECT coalesce(sum(CASE WHEN state='released' THEN 0 ELSE coalesce(confirmed_micro,estimated_micro,reserved_micro) END),0) INTO exposure FROM accounting.model_costs WHERE firm_id=r.firm_id AND company_id=r.company_id AND period=period_key;
   IF exposure+reserve_amount>p.monthly_limit_micro THEN RETURN 'quota_exhausted'; END IF;
  END IF;
  stamp:=accounting.ocr_utc_now();
  INSERT INTO accounting.model_costs(id,run_id,firm_id,company_id,event_id,kind,created_by,period,provider,model,charge_token,pricing_json,quota_enabled,reserved_micro,state,created_at)
   VALUES(r.id,r.id,r.firm_id,r.company_id,r.event_id,r.kind,r.created_by,period_key,r.provider,r.model,target_token,target_pricing,coalesce(p.quota_enabled,0),reserve_amount,'reserved',stamp);
  RETURN 'reserved';
 END
$$;
CREATE FUNCTION accounting.mark_model_cost_sent(target_run TEXT,target_token TEXT) RETURNS BOOLEAN
 LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE c accounting.model_costs;
 BEGIN
  PERFORM pg_advisory_xact_lock(6420031786281903101);
  SELECT * INTO c FROM accounting.model_costs WHERE run_id=target_run AND charge_token=target_token AND created_by=accounting.actor_user() AND firm_id=accounting.actor_firm();
  IF c.id IS NULL OR NOT accounting.ai_event_processor(c.event_id,c.firm_id,c.company_id) OR NOT accounting.ai_event_open(c.event_id,c.firm_id,c.company_id) THEN RETURN false; END IF;
  IF c.state='sent' THEN RETURN true; END IF;
  IF c.state<>'reserved' THEN RETURN false; END IF;
  UPDATE accounting.model_costs SET state='sent',sent_at=accounting.ocr_utc_now(),version=version+1 WHERE id=c.id;
  RETURN true;
 END
$$;
CREATE FUNCTION accounting.settle_model_cost(target_run TEXT,target_token TEXT,target_input_tokens BIGINT,target_output_tokens BIGINT,safe_rejection BOOLEAN DEFAULT false) RETURNS BOOLEAN
 LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE c accounting.model_costs; amount NUMERIC; next_state TEXT; stamp TEXT; overflowed BOOLEAN:=false;
 BEGIN
  PERFORM pg_advisory_xact_lock(6420031786281903101);
  SELECT * INTO c FROM accounting.model_costs WHERE run_id=target_run AND charge_token=target_token AND created_by=accounting.actor_user() AND firm_id=accounting.actor_firm();
  IF c.id IS NULL THEN RETURN false; END IF;
  IF c.state IN ('estimated','uncertain','released','reconciled') THEN RETURN true; END IF;
  IF safe_rejection THEN next_state:='released';
  ELSIF c.state='reserved' THEN next_state:='released';
  ELSIF target_input_tokens IS NULL OR target_output_tokens IS NULL OR target_input_tokens NOT BETWEEN 0 AND 10000000 OR target_output_tokens NOT BETWEEN 0 AND 10000000
   OR NOT (c.pricing_json::jsonb->>'configured')::boolean THEN next_state:='uncertain';
  ELSE
   amount:=ceil((target_input_tokens::numeric*(c.pricing_json::jsonb->>'input_per_million_micro')::numeric
    + target_output_tokens::numeric*(c.pricing_json::jsonb->>'output_per_million_micro')::numeric)/1000000);
   IF amount>999999999999990000 THEN next_state:='uncertain';amount:=NULL;overflowed:=true; ELSE next_state:='estimated'; END IF;
  END IF;
  stamp:=accounting.ocr_utc_now();
  UPDATE accounting.model_costs SET state=next_state,estimated_micro=CASE WHEN next_state='estimated' THEN amount::bigint ELSE NULL END,
   input_tokens=CASE WHEN target_input_tokens BETWEEN 0 AND 10000000 THEN target_input_tokens ELSE NULL END,
   output_tokens=CASE WHEN target_output_tokens BETWEEN 0 AND 10000000 THEN target_output_tokens ELSE NULL END,
   overrun=CASE WHEN quota_enabled=1 AND (overflowed OR amount>reserved_micro) THEN 1 ELSE 0 END,finished_at=stamp,version=version+1 WHERE id=c.id;
  RETURN true;
 END
$$;
CREATE FUNCTION accounting.manual_reconcile_model_cost(target_run TEXT,expected_version INTEGER,target_confirmed BIGINT,reason TEXT) RETURNS BOOLEAN
 LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE c accounting.model_costs; stamp TEXT;
 BEGIN
  IF target_confirmed IS NULL OR target_confirmed NOT BETWEEN 0 AND 999999999999990000 OR length(btrim(reason)) NOT BETWEEN 1 AND 1000 THEN RETURN false; END IF;
  PERFORM pg_advisory_xact_lock(6420031786281903101);
  SELECT * INTO c FROM accounting.model_costs WHERE run_id=target_run AND firm_id=accounting.actor_firm() AND accounting.company_manager(company_id);
  IF c.id IS NULL OR c.version<>expected_version OR c.state NOT IN ('uncertain','estimated','reconciled') THEN RETURN false; END IF;
  stamp:=accounting.ocr_utc_now();
  UPDATE accounting.model_costs SET confirmed_micro=target_confirmed,state='reconciled',overrun=0,manual_reason=btrim(reason),manual_by=accounting.actor_user(),version=version+1 WHERE id=c.id;
  INSERT INTO accounting.audit_events(firm_id,book_id,user_id,action,object_id,details,created_at)
   VALUES(c.firm_id,NULL,accounting.actor_user(),'model.cost.reconcile',c.id,jsonb_build_object('previous_confirmed_micro',c.confirmed_micro,'confirmed_micro',target_confirmed,'reason',btrim(reason),'version',c.version+1)::text,stamp);
  RETURN true;
 END
$$;
CREATE FUNCTION accounting.record_worker_heartbeat(target_worker TEXT,target_state TEXT,target_count INTEGER) RETURNS BOOLEAN
 LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE stamp TEXT;
 BEGIN
  IF length(btrim(target_worker)) NOT BETWEEN 1 AND 120 OR target_state NOT IN ('active','idle','stopped') OR target_count NOT BETWEEN 0 AND 2 OR (target_state<>'active' AND target_count<>0) THEN RETURN false; END IF;
  stamp:=accounting.ocr_utc_now();
  INSERT INTO accounting.worker_heartbeats(worker_id,started_at,heartbeat_at,state,inflight_count) VALUES(target_worker,stamp,stamp,target_state,target_count)
   ON CONFLICT(worker_id) DO UPDATE SET started_at=CASE WHEN worker_heartbeats.state='stopped' AND excluded.state<>'stopped' THEN excluded.started_at ELSE worker_heartbeats.started_at END,
    heartbeat_at=excluded.heartbeat_at,state=excluded.state,inflight_count=excluded.inflight_count;
  RETURN true;
 END
$$;

-- Internal aggregate: no public grants and no original evidence/hidden IDs.
CREATE FUNCTION accounting.ops_company_metrics_internal(target_firm TEXT,target_company TEXT,target_period TEXT) RETURNS JSONB
 LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE p JSONB; costs JSONB; queue_stats JSONB; exceptions JSONB; workers JSONB; window_minutes INTEGER; stale_seconds INTEGER;
 BEGIN
  SELECT to_jsonb(policy)-'firm_id'-'company_id'-'updated_by'-'updated_at' INTO p FROM accounting.model_company_policies policy WHERE firm_id=target_firm AND company_id=target_company;
  p:=coalesce(p,jsonb_build_object('quota_enabled',0,'monthly_limit_micro',0,'ocr_reserve_micro',0,'proposal_reserve_micro',0,'failure_threshold',3,'failure_window_minutes',60,'backlog_threshold',10,'backlog_age_minutes',60,'queue_wait_minutes',10,'worker_stale_seconds',90,'version',0));
  window_minutes:=(p->>'failure_window_minutes')::integer;stale_seconds:=(p->>'worker_stale_seconds')::integer;
  SELECT jsonb_build_object('run_count',count(*),'reserved_micro',coalesce(sum(reserved_micro),0),'estimated_micro',sum(estimated_micro),
   'confirmed_micro',sum(confirmed_micro),'exposure_micro',coalesce(sum(CASE WHEN state='released' THEN 0 ELSE coalesce(confirmed_micro,estimated_micro,reserved_micro) END),0),
   'input_tokens',sum(input_tokens),'output_tokens',sum(output_tokens),'reported_count',count(*) FILTER(WHERE input_tokens IS NOT NULL AND output_tokens IS NOT NULL),
   'risk_fingerprint',md5(coalesce(string_agg(id||':'||state||':'||version::text,',' ORDER BY id),'')),
   'unknown_count',count(*) FILTER(WHERE state='uncertain'),'overrun_count',count(*) FILTER(WHERE overrun=1)) INTO costs
   FROM accounting.model_costs WHERE firm_id=target_firm AND company_id=target_company AND period=target_period;
  SELECT jsonb_build_object('queued',count(*) FILTER(WHERE status='queued'),'running',count(*) FILTER(WHERE status='running'),
   'retry_wait',count(*) FILTER(WHERE status='retry_wait'),'succeeded',count(*) FILTER(WHERE status='succeeded'),
   'failed',count(*) FILTER(WHERE status='failed'),'interrupted',count(*) FILTER(WHERE status='interrupted'),
   'stale',count(*) FILTER(WHERE status='stale'),'cancelled',count(*) FILTER(WHERE status='cancelled'),
   'recent_failures',count(*) FILTER(WHERE status IN ('failed','interrupted') AND finished_at::timestamptz>=now()-make_interval(mins=>window_minutes)),
   'oldest_pending_at',min(created_at) FILTER(WHERE status IN ('queued','retry_wait')),
   'risk_fingerprint',md5(coalesce(string_agg(id||':'||status||':'||attempt_count::text||':'||coalesce(finished_at,''),',' ORDER BY id),''))) INTO queue_stats
   FROM accounting.ocr_jobs WHERE firm_id=target_firm AND company_id=target_company;
  SELECT jsonb_build_object('blocking_events',count(*) FILTER(WHERE blocking_count>0),'blocking_count',coalesce(sum(blocking_count),0),
   'warning_count',coalesce(sum(warning_count),0),'oldest_opened_at',min(oldest_opened_at) FILTER(WHERE blocking_count>0),
   'risk_fingerprint',md5(coalesce(string_agg(event_id||':'||context_fingerprint||':'||blocking_count::text||':'||warning_count::text,',' ORDER BY event_id),''))) INTO exceptions
   FROM accounting.ops_exception_snapshots WHERE firm_id=target_firm AND company_id=target_company;
  SELECT jsonb_build_object('available',coalesce(bool_or(state IN ('active','idle') AND heartbeat_at::timestamptz>=now()-make_interval(secs=>stale_seconds)),false),
   'last_heartbeat_at',max(heartbeat_at),'last_age_seconds',CASE WHEN max(heartbeat_at) IS NULL THEN NULL ELSE greatest(0,extract(epoch FROM now()-max(heartbeat_at)::timestamptz)) END) INTO workers FROM accounting.worker_heartbeats;
  RETURN jsonb_build_object('period',target_period,'policy',p,'costs',costs,'queue',queue_stats,'exceptions',exceptions,'worker',workers);
 END
$$;
CREATE FUNCTION accounting.ops_company_metrics(target_firm TEXT,target_company TEXT,target_period TEXT) RETURNS JSONB
 LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 BEGIN
  IF NOT accounting.ops_company_allowed(target_firm,target_company) THEN RAISE EXCEPTION 'Company operations access denied' USING ERRCODE='42501'; END IF;
  IF target_period !~ '^[0-9]{4}-(0[1-9]|1[0-2])$' THEN RAISE EXCEPTION 'Invalid cost period' USING ERRCODE='22023'; END IF;
  RETURN accounting.ops_company_metrics_internal(target_firm,target_company,target_period);
 END
$$;
CREATE FUNCTION accounting.ops_refresh_company_alerts(target_firm TEXT,target_company TEXT) RETURNS JSONB
 LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE m JSONB; p JSONB; c JSONB; q JSONB; e JSONB; w JSONB; code_key TEXT; triggered BOOLEAN; count_value NUMERIC; risk TEXT;
  message TEXT; severity_key TEXT; stamp TEXT; fp TEXT; open_count INTEGER; payload JSONB;
 BEGIN
  IF NOT pg_has_role(session_user,'accounting_worker','MEMBER') AND NOT accounting.ops_company_allowed(target_firm,target_company) THEN
   RAISE EXCEPTION 'Company operations access denied' USING ERRCODE='42501';
  END IF;
  IF NOT EXISTS(SELECT 1 FROM accounting.firm_clients WHERE firm_id=target_firm AND company_id=target_company AND status='active') THEN RETURN jsonb_build_object('refreshed',0,'open',0); END IF;
  PERFORM pg_advisory_xact_lock(6420031786281903101);
  m:=accounting.ops_company_metrics_internal(target_firm,target_company,to_char(clock_timestamp() AT TIME ZONE 'Asia/Shanghai','YYYY-MM'));
  p:=m->'policy';c:=m->'costs';q:=m->'queue';e:=m->'exceptions';w:=m->'worker';stamp:=accounting.ocr_utc_now();
  FOREACH code_key IN ARRAY ARRAY['budget_exhausted','quota_overrun','usage_uncertain','task_failures','exception_backlog','queue_wait','worker_unavailable'] LOOP
   severity_key:='warning';count_value:=0;
   CASE code_key
   WHEN 'budget_exhausted' THEN count_value:=(c->>'exposure_micro')::numeric;triggered:=(p->>'quota_enabled')::integer=1 AND count_value>=(p->>'monthly_limit_micro')::numeric;message:='本月模型费用额度已用尽';severity_key:='critical';
   WHEN 'quota_overrun' THEN count_value:=(c->>'overrun_count')::numeric;triggered:=count_value>0;message:='模型费用超过单次预留金额，须核对成本';severity_key:='critical';
   WHEN 'usage_uncertain' THEN count_value:=(c->>'unknown_count')::numeric;triggered:=count_value>0;message:='部分模型调用的用量或费用尚未确认';
   WHEN 'task_failures' THEN count_value:=(q->>'recent_failures')::numeric;triggered:=count_value>=(p->>'failure_threshold')::numeric;message:='近期票据处理失败较多，请检查服务';
   WHEN 'exception_backlog' THEN count_value:=(e->>'blocking_events')::numeric;triggered:=count_value>=(p->>'backlog_threshold')::numeric AND (e->>'oldest_opened_at')::timestamptz<=now()-make_interval(mins=>(p->>'backlog_age_minutes')::integer);message:='会计核对异常积压，需要人工处理';
   WHEN 'queue_wait' THEN count_value:=(q->>'queued')::numeric+(q->>'retry_wait')::numeric;triggered:=count_value>0 AND (q->>'oldest_pending_at')::timestamptz<=now()-make_interval(mins=>(p->>'queue_wait_minutes')::integer);message:='票据任务等待时间过长';
   WHEN 'worker_unavailable' THEN count_value:=(q->>'queued')::numeric+(q->>'retry_wait')::numeric+(q->>'running')::numeric;triggered:=count_value>0 AND NOT (w->>'available')::boolean;message:='有待处理票据，但后台进程心跳不可用';severity_key:='critical';
   END CASE;
   IF coalesce(triggered,false) THEN
    risk:=CASE WHEN code_key IN ('budget_exhausted','quota_overrun','usage_uncertain') THEN c->>'risk_fingerprint' WHEN code_key='exception_backlog' THEN e->>'risk_fingerprint' ELSE q->>'risk_fingerprint' END;
    payload:=jsonb_build_object('message',message,'count',count_value);fp:=md5(code_key||payload::text||coalesce(risk,'')||coalesce(p->>'version',''));
    INSERT INTO accounting.ops_alerts(id,firm_id,company_id,code,severity,state,fingerprint,payload_json,opened_at,last_seen_at)
     VALUES(md5(target_firm||target_company||code_key),target_firm,target_company,code_key,severity_key,'open',fp,payload::text,stamp,stamp)
     ON CONFLICT(firm_id,company_id,code) DO UPDATE SET state=CASE WHEN ops_alerts.state='resolved' OR (ops_alerts.state='acknowledged' AND ops_alerts.fingerprint<>excluded.fingerprint) THEN 'open' ELSE ops_alerts.state END,
      severity=excluded.severity,fingerprint=excluded.fingerprint,payload_json=excluded.payload_json,last_seen_at=stamp,
      opened_at=CASE WHEN ops_alerts.state='resolved' THEN stamp ELSE ops_alerts.opened_at END,resolved_at=NULL,version=ops_alerts.version+1;
   ELSE
    UPDATE accounting.ops_alerts SET state='resolved',resolved_at=stamp,last_seen_at=stamp,version=version+1 WHERE firm_id=target_firm AND company_id=target_company AND code=code_key AND state<>'resolved';
   END IF;
  END LOOP;
  SELECT count(*) INTO open_count FROM accounting.ops_alerts WHERE firm_id=target_firm AND company_id=target_company AND state<>'resolved';
  RETURN jsonb_build_object('refreshed',7,'open',open_count);
 END
$$;
CREATE FUNCTION accounting.ops_scan_all_alerts() RETURNS INTEGER LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE item RECORD; count_value INTEGER:=0;
 BEGIN
  FOR item IN SELECT firm_id,company_id FROM accounting.firm_clients WHERE status='active' ORDER BY firm_id,company_id LOOP
   PERFORM accounting.ops_refresh_company_alerts(item.firm_id,item.company_id);count_value:=count_value+1;
  END LOOP;
  RETURN count_value;
 END
$$;
REVOKE ALL ON FUNCTION accounting.ops_company_allowed(TEXT,TEXT),accounting.model_cost_identity_guard(),accounting.ops_scope_guard(),accounting.valid_model_pricing(TEXT,TEXT,TEXT),
 accounting.reserve_model_cost(TEXT,TEXT,TEXT,TEXT,TEXT,BIGINT),accounting.mark_model_cost_sent(TEXT,TEXT),accounting.settle_model_cost(TEXT,TEXT,BIGINT,BIGINT,BOOLEAN),
 accounting.manual_reconcile_model_cost(TEXT,INTEGER,BIGINT,TEXT),accounting.record_worker_heartbeat(TEXT,TEXT,INTEGER),accounting.ops_company_metrics_internal(TEXT,TEXT,TEXT),
 accounting.ops_company_metrics(TEXT,TEXT,TEXT),accounting.ops_refresh_company_alerts(TEXT,TEXT),accounting.ops_scan_all_alerts() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION accounting.ops_company_allowed(TEXT,TEXT),accounting.reserve_model_cost(TEXT,TEXT,TEXT,TEXT,TEXT,BIGINT),accounting.mark_model_cost_sent(TEXT,TEXT),
 accounting.settle_model_cost(TEXT,TEXT,BIGINT,BIGINT,BOOLEAN),accounting.manual_reconcile_model_cost(TEXT,INTEGER,BIGINT,TEXT),accounting.ops_company_metrics(TEXT,TEXT,TEXT),accounting.ops_refresh_company_alerts(TEXT,TEXT) TO accounting_app;
GRANT EXECUTE ON FUNCTION accounting.record_worker_heartbeat(TEXT,TEXT,INTEGER),accounting.ops_refresh_company_alerts(TEXT,TEXT),accounting.ops_scan_all_alerts() TO accounting_worker;
