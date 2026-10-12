-- Preserve migration 016; tighten price bounds, China-month carryover and scoped snapshots.

CREATE OR REPLACE FUNCTION accounting.ops_scope_guard() RETURNS TRIGGER LANGUAGE plpgsql SET search_path=pg_catalog,accounting,pg_temp AS $$
 BEGIN
  IF (NEW.firm_id,NEW.company_id) IS DISTINCT FROM (OLD.firm_id,OLD.company_id) THEN
   RAISE EXCEPTION 'Operations scope is immutable' USING ERRCODE='23514';
  END IF;
  IF TG_TABLE_NAME='ops_alerts' THEN
   IF (NEW.id,NEW.code) IS DISTINCT FROM (OLD.id,OLD.code) THEN RAISE EXCEPTION 'Alert identity is immutable' USING ERRCODE='23514'; END IF;
  ELSIF TG_TABLE_NAME='ops_exception_snapshots' THEN
   IF NEW.event_id IS DISTINCT FROM OLD.event_id THEN RAISE EXCEPTION 'Snapshot event is immutable' USING ERRCODE='23514'; END IF;
  END IF;
  RETURN NEW;
 END
$$;

CREATE OR REPLACE FUNCTION accounting.valid_model_pricing(pricing TEXT,expected_provider TEXT,expected_model TEXT) RETURNS BOOLEAN LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog AS $$
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
   ELSIF jsonb_typeof(value)<>'number' OR value::text !~ '^[0-9]+$' OR value::text::numeric>1000000000000 THEN RETURN false; END IF;
  END LOOP;
  RETURN true;
 EXCEPTION WHEN others THEN RETURN false;
 END
$$;

CREATE OR REPLACE FUNCTION accounting.reserve_model_cost(target_run TEXT,target_token TEXT,target_provider TEXT,target_model TEXT,target_pricing TEXT,target_reserve BIGINT) RETURNS TEXT
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
   SELECT coalesce(sum(CASE WHEN period=period_key THEN CASE WHEN state='released' THEN 0 ELSE coalesce(confirmed_micro,estimated_micro,reserved_micro) END
    WHEN period<period_key AND state IN ('reserved','sent','uncertain') THEN reserved_micro ELSE 0 END),0) INTO exposure
    FROM accounting.model_costs WHERE firm_id=r.firm_id AND company_id=r.company_id AND period<=period_key;
   IF exposure+reserve_amount>p.monthly_limit_micro THEN RETURN 'quota_exhausted'; END IF;
  END IF;
  stamp:=accounting.ocr_utc_now();
  INSERT INTO accounting.model_costs(id,run_id,firm_id,company_id,event_id,kind,created_by,period,provider,model,charge_token,pricing_json,quota_enabled,reserved_micro,state,created_at)
   VALUES(r.id,r.id,r.firm_id,r.company_id,r.event_id,r.kind,r.created_by,period_key,r.provider,r.model,target_token,target_pricing,coalesce(p.quota_enabled,0),reserve_amount,'reserved',stamp);
  RETURN 'reserved';
 END
$$;

CREATE OR REPLACE FUNCTION accounting.ops_company_metrics_internal(target_firm TEXT,target_company TEXT,target_period TEXT) RETURNS JSONB
 LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE p JSONB; costs JSONB; queue_stats JSONB; exceptions JSONB; workers JSONB; window_minutes INTEGER; stale_seconds INTEGER; carried NUMERIC; carried_unknown BIGINT;
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
  SELECT coalesce(sum(reserved_micro),0),count(*) FILTER(WHERE state='uncertain') INTO carried,carried_unknown
   FROM accounting.model_costs WHERE firm_id=target_firm AND company_id=target_company AND period<target_period AND state IN ('reserved','sent','uncertain');
  costs:=costs||jsonb_build_object('carried_reserved_micro',carried,'carried_unknown_count',carried_unknown,'exposure_micro',(costs->>'exposure_micro')::numeric+carried);
  SELECT jsonb_build_object('queued',count(*) FILTER(WHERE status='queued'),'running',count(*) FILTER(WHERE status='running'),
   'retry_wait',count(*) FILTER(WHERE status='retry_wait'),'succeeded',count(*) FILTER(WHERE status='succeeded'),
   'failed',count(*) FILTER(WHERE status='failed'),'interrupted',count(*) FILTER(WHERE status='interrupted'),
   'stale',count(*) FILTER(WHERE status='stale'),'cancelled',count(*) FILTER(WHERE status='cancelled'),
   'recent_failures',count(*) FILTER(WHERE status IN ('failed','interrupted') AND finished_at::timestamptz>=now()-make_interval(mins=>window_minutes)),
   'oldest_pending_at',min(created_at) FILTER(WHERE status IN ('queued','retry_wait')),
   'risk_fingerprint',md5(coalesce(string_agg(id||':'||status||':'||attempt_count::text||':'||coalesce(finished_at,''),',' ORDER BY id),''))) INTO queue_stats
   FROM accounting.ocr_jobs WHERE firm_id=target_firm AND company_id=target_company;
  SELECT jsonb_build_object('blocking_events',count(*) FILTER(WHERE blocking_count>0),'blocking_count',coalesce(sum(blocking_count),0),
   'warning_count',coalesce(sum(warning_count),0),'updated_at',max(updated_at),'oldest_opened_at',min(oldest_opened_at) FILTER(WHERE blocking_count>0),
   'risk_fingerprint',md5(coalesce(string_agg(event_id||':'||context_fingerprint||':'||blocking_count::text||':'||warning_count::text,',' ORDER BY event_id),''))) INTO exceptions
   FROM accounting.ops_exception_snapshots x WHERE firm_id=target_firm AND company_id=target_company
   AND EXISTS(SELECT 1 FROM accounting.ai_company_settings settings WHERE settings.firm_id=x.firm_id AND settings.company_id=x.company_id AND settings.enabled=1)
   AND NOT EXISTS(SELECT 1 FROM accounting.business_mappings m JOIN accounting.drafts d ON d.id=m.draft_id AND d.book_id=m.book_id WHERE m.event_id=x.event_id AND d.status<>'draft');
  SELECT jsonb_build_object('available',coalesce(bool_or(state IN ('active','idle') AND heartbeat_at::timestamptz>=now()-make_interval(secs=>stale_seconds)),false),
   'last_heartbeat_at',max(heartbeat_at),'last_age_seconds',CASE WHEN max(heartbeat_at) IS NULL THEN NULL ELSE greatest(0,extract(epoch FROM now()-max(heartbeat_at)::timestamptz)) END) INTO workers FROM accounting.worker_heartbeats;
  RETURN jsonb_build_object('period',target_period,'policy',p,'costs',costs,'queue',queue_stats,'exceptions',exceptions,'worker',workers);
 END
$$;

CREATE FUNCTION accounting.record_ops_exception_snapshot(target_event TEXT,target_blocking INTEGER,target_warning INTEGER,target_oldest TEXT,target_fingerprint TEXT) RETURNS BOOLEAN
 LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE e accounting.business_events; stamp TEXT;
 BEGIN
  IF target_blocking IS NULL OR target_warning IS NULL OR target_blocking NOT BETWEEN 0 AND 10000 OR target_warning NOT BETWEEN 0 AND 10000
   OR target_fingerprint IS NULL OR length(target_fingerprint) NOT BETWEEN 1 AND 200 THEN RETURN false; END IF;
  PERFORM pg_advisory_xact_lock(6420031786281903101);
  SELECT * INTO e FROM accounting.business_events WHERE id=target_event AND firm_id=accounting.actor_firm();
  IF e.id IS NULL OR NOT accounting.ai_event_processor(e.id,e.firm_id,e.company_id) THEN RETURN false; END IF;
  stamp:=accounting.ocr_utc_now();
  INSERT INTO accounting.ops_exception_snapshots(event_id,firm_id,company_id,blocking_count,warning_count,oldest_opened_at,context_fingerprint,updated_at)
   VALUES(e.id,e.firm_id,e.company_id,target_blocking,target_warning,CASE WHEN target_blocking>0 THEN coalesce(target_oldest,stamp) ELSE NULL END,target_fingerprint,stamp)
   ON CONFLICT(event_id) DO UPDATE SET blocking_count=excluded.blocking_count,warning_count=excluded.warning_count,
    oldest_opened_at=CASE WHEN excluded.blocking_count=0 THEN NULL WHEN ops_exception_snapshots.blocking_count>0 THEN coalesce(ops_exception_snapshots.oldest_opened_at,excluded.oldest_opened_at) ELSE excluded.oldest_opened_at END,
    context_fingerprint=excluded.context_fingerprint,updated_at=excluded.updated_at
   WHERE (ops_exception_snapshots.blocking_count,ops_exception_snapshots.warning_count,ops_exception_snapshots.context_fingerprint)
    IS DISTINCT FROM (excluded.blocking_count,excluded.warning_count,excluded.context_fingerprint);
  RETURN true;
 END
$$;
REVOKE ALL ON FUNCTION accounting.record_ops_exception_snapshot(TEXT,INTEGER,INTEGER,TEXT,TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION accounting.record_ops_exception_snapshot(TEXT,INTEGER,INTEGER,TEXT,TEXT) TO accounting_app;

