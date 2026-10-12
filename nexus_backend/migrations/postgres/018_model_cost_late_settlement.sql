-- Record late cost facts without publishing originals; retain all-month unresolved risk.

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
   IF EXISTS(SELECT 1 FROM accounting.model_costs WHERE firm_id=r.firm_id AND company_id=r.company_id AND overrun=1) THEN RETURN 'quota_overrun'; END IF;
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

CREATE OR REPLACE FUNCTION accounting.model_cost_identity_guard() RETURNS TRIGGER LANGUAGE plpgsql SET search_path=pg_catalog,accounting,pg_temp AS $$
 BEGIN
  IF (NEW.id,NEW.run_id,NEW.firm_id,NEW.company_id,NEW.event_id,NEW.kind,NEW.created_by,NEW.period,NEW.provider,NEW.model,
   NEW.charge_token,NEW.pricing_json,NEW.quota_enabled,NEW.reserved_micro,NEW.created_at)
  IS DISTINCT FROM (OLD.id,OLD.run_id,OLD.firm_id,OLD.company_id,OLD.event_id,OLD.kind,OLD.created_by,OLD.period,OLD.provider,OLD.model,
   OLD.charge_token,OLD.pricing_json,OLD.quota_enabled,OLD.reserved_micro,OLD.created_at)
  OR NEW.version<>OLD.version+1 OR (OLD.sent_at IS NOT NULL AND NEW.sent_at IS DISTINCT FROM OLD.sent_at)
  OR (OLD.state IN ('estimated','released','reconciled') AND NEW.state<>'reconciled')
  OR (OLD.state='uncertain' AND (OLD.estimated_micro IS NOT NULL OR OLD.input_tokens IS NOT NULL OR OLD.output_tokens IS NOT NULL) AND NEW.state<>'reconciled')
  OR (OLD.state='uncertain' AND OLD.estimated_micro IS NULL AND OLD.input_tokens IS NULL AND OLD.output_tokens IS NULL AND NEW.state NOT IN ('uncertain','estimated','released','reconciled'))
  OR ((OLD.state IN ('estimated','released','reconciled') OR (OLD.state='uncertain' AND (OLD.estimated_micro IS NOT NULL OR OLD.input_tokens IS NOT NULL OR OLD.output_tokens IS NOT NULL))) AND (NEW.estimated_micro,NEW.input_tokens,NEW.output_tokens) IS DISTINCT FROM (OLD.estimated_micro,OLD.input_tokens,OLD.output_tokens)) THEN
   RAISE EXCEPTION 'Model cost evidence and provenance are immutable' USING ERRCODE='23514';
  END IF;
  RETURN NEW;
 END
$$;

CREATE OR REPLACE FUNCTION accounting.settle_model_cost(target_run TEXT,target_token TEXT,target_input_tokens BIGINT,target_output_tokens BIGINT,safe_rejection BOOLEAN DEFAULT false) RETURNS BOOLEAN
 LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE c accounting.model_costs; amount NUMERIC; next_state TEXT; stamp TEXT; overflowed BOOLEAN:=false;
 BEGIN
  PERFORM pg_advisory_xact_lock(6420031786281903101);
  SELECT * INTO c FROM accounting.model_costs WHERE run_id=target_run AND charge_token=target_token AND created_by=accounting.actor_user() AND firm_id=accounting.actor_firm();
  IF c.id IS NULL THEN RETURN false; END IF;
  IF c.state IN ('estimated','released','reconciled') THEN RETURN true; END IF;
  IF c.state='uncertain' AND (c.estimated_micro IS NOT NULL OR c.input_tokens IS NOT NULL OR c.output_tokens IS NOT NULL) THEN RETURN true; END IF;
  IF c.state='uncertain' AND NOT safe_rejection AND (target_input_tokens IS NULL OR target_output_tokens IS NULL OR target_input_tokens NOT BETWEEN 0 AND 10000000 OR target_output_tokens NOT BETWEEN 0 AND 10000000) THEN RETURN true; END IF;
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

CREATE OR REPLACE FUNCTION accounting.ops_company_metrics_internal(target_firm TEXT,target_company TEXT,target_period TEXT) RETURNS JSONB
 LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE p JSONB; costs JSONB; queue_stats JSONB; exceptions JSONB; workers JSONB; window_minutes INTEGER; stale_seconds INTEGER; carried NUMERIC; carried_unknown BIGINT; carried_risk TEXT; historical_overruns BIGINT;
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
  SELECT coalesce(sum(reserved_micro),0),count(*) FILTER(WHERE state='uncertain'),md5(coalesce(string_agg(id||':'||state||':'||version::text,',' ORDER BY id),'')) INTO carried,carried_unknown,carried_risk
   FROM accounting.model_costs WHERE firm_id=target_firm AND company_id=target_company AND period<target_period AND state IN ('reserved','sent','uncertain');
  SELECT count(*) INTO historical_overruns FROM accounting.model_costs WHERE firm_id=target_firm AND company_id=target_company AND period<target_period AND overrun=1;
  costs:=costs||jsonb_build_object('historical_overrun_count',historical_overruns,'risk_fingerprint',md5((costs->>'risk_fingerprint')||coalesce(carried_risk,'')),'carried_reserved_micro',carried,'carried_unknown_count',carried_unknown,'exposure_micro',(costs->>'exposure_micro')::numeric+carried);
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

CREATE OR REPLACE FUNCTION accounting.ops_refresh_company_alerts(target_firm TEXT,target_company TEXT) RETURNS JSONB
 LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE m JSONB; p JSONB; c JSONB; q JSONB; e JSONB; w JSONB; code_key TEXT; triggered BOOLEAN; count_value NUMERIC; risk TEXT;
  message TEXT; severity_key TEXT; stamp TEXT; fp TEXT; open_count INTEGER; payload JSONB;
 BEGIN
  IF NOT pg_has_role(session_user,'accounting_worker','MEMBER') AND NOT accounting.ops_company_allowed(target_firm,target_company) THEN
   RAISE EXCEPTION 'Company operations access denied' USING ERRCODE='42501';
  END IF;
  IF NOT EXISTS(SELECT 1 FROM accounting.firm_clients WHERE firm_id=target_firm AND company_id=target_company AND status='active') THEN RETURN jsonb_build_object('refreshed',0,'open',0); END IF;
  PERFORM pg_advisory_xact_lock(6420031786281903101);
  UPDATE accounting.model_costs SET state='uncertain',finished_at=accounting.ocr_utc_now(),version=version+1
   WHERE firm_id=target_firm AND company_id=target_company AND state IN ('reserved','sent')
   AND coalesce(sent_at,created_at)::timestamptz<=clock_timestamp()-interval '180 seconds';
  m:=accounting.ops_company_metrics_internal(target_firm,target_company,to_char(clock_timestamp() AT TIME ZONE 'Asia/Shanghai','YYYY-MM'));
  p:=m->'policy';c:=m->'costs';q:=m->'queue';e:=m->'exceptions';w:=m->'worker';stamp:=accounting.ocr_utc_now();
  FOREACH code_key IN ARRAY ARRAY['budget_exhausted','quota_overrun','usage_uncertain','task_failures','exception_backlog','queue_wait','worker_unavailable'] LOOP
   severity_key:='warning';count_value:=0;
   CASE code_key
   WHEN 'budget_exhausted' THEN count_value:=(c->>'exposure_micro')::numeric;triggered:=(p->>'quota_enabled')::integer=1 AND count_value>=(p->>'monthly_limit_micro')::numeric;message:='本月模型费用额度已用尽';severity_key:='critical';
   WHEN 'quota_overrun' THEN count_value:=(c->>'overrun_count')::numeric+coalesce((c->>'historical_overrun_count')::numeric,0);triggered:=count_value>0;message:='模型费用超过单次预留金额，须核对成本';severity_key:='critical';
   WHEN 'usage_uncertain' THEN count_value:=(c->>'unknown_count')::numeric+coalesce((c->>'carried_unknown_count')::numeric,0);triggered:=count_value>0;message:='部分模型调用的用量或费用尚未确认';
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
