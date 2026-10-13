-- OCR and journal candidates participate in the same operational alerts.
-- Repeated observation does not invalidate acknowledgement optimistic versions.
CREATE OR REPLACE FUNCTION accounting.ops_company_metrics_internal(target_firm TEXT,target_company TEXT,target_period TEXT) RETURNS JSONB
 LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE p JSONB; costs JSONB; queue_stats JSONB; exceptions JSONB; workers JSONB; window_minutes INTEGER; stale_seconds INTEGER; carried NUMERIC; carried_unknown BIGINT; carried_risk TEXT; historical_overruns BIGINT; historical_risk TEXT;
 BEGIN
  SELECT to_jsonb(policy)-'firm_id'-'company_id'-'updated_by'-'updated_at' INTO p FROM accounting.model_company_policies policy WHERE firm_id=target_firm AND company_id=target_company;
  p:=coalesce(p,jsonb_build_object('quota_enabled',0,'monthly_limit_micro',0,'ocr_reserve_micro',0,'proposal_reserve_micro',0,'failure_threshold',3,'failure_window_minutes',60,'backlog_threshold',10,'backlog_age_minutes',60,'queue_wait_minutes',10,'worker_stale_seconds',90,'version',0));
  window_minutes:=(p->>'failure_window_minutes')::integer;stale_seconds:=(p->>'worker_stale_seconds')::integer;
  SELECT jsonb_build_object('run_count',count(*),'reserved_micro',coalesce(sum(reserved_micro) FILTER(WHERE state IN ('reserved','sent','uncertain')),0),'estimated_micro',sum(estimated_micro),
   'confirmed_micro',sum(confirmed_micro),'exposure_micro',coalesce(sum(CASE WHEN state='released' THEN 0 ELSE coalesce(confirmed_micro,estimated_micro,reserved_micro) END),0),
   'input_tokens',sum(input_tokens),'output_tokens',sum(output_tokens),'reported_count',count(*) FILTER(WHERE input_tokens IS NOT NULL AND output_tokens IS NOT NULL),
   'risk_fingerprint',md5(coalesce(string_agg(id||':'||state||':'||version::text,',' ORDER BY id),'')),
   'unknown_count',count(*) FILTER(WHERE state='uncertain'),'overrun_count',count(*) FILTER(WHERE overrun=1)) INTO costs
   FROM accounting.model_costs WHERE firm_id=target_firm AND company_id=target_company AND period=target_period;
  SELECT coalesce(sum(reserved_micro),0),count(*) FILTER(WHERE state='uncertain'),md5(coalesce(string_agg(id||':'||state||':'||version::text,',' ORDER BY id),'')) INTO carried,carried_unknown,carried_risk
   FROM accounting.model_costs WHERE firm_id=target_firm AND company_id=target_company AND period<target_period AND state IN ('reserved','sent','uncertain');
  SELECT count(*),md5(coalesce(string_agg(id||':'||state||':'||version::text,',' ORDER BY id),'')) INTO historical_overruns,historical_risk FROM accounting.model_costs WHERE firm_id=target_firm AND company_id=target_company AND period<target_period AND overrun=1;
  costs:=costs||jsonb_build_object('historical_overrun_count',historical_overruns,'risk_fingerprint',md5((costs->>'risk_fingerprint')||coalesce(carried_risk,'')||coalesce(historical_risk,'')),'carried_reserved_micro',carried,'carried_unknown_count',carried_unknown,'exposure_micro',(costs->>'exposure_micro')::numeric+carried);
  SELECT jsonb_build_object('queued',count(*) FILTER(WHERE status='queued'),'running',count(*) FILTER(WHERE status='running'),
   'retry_wait',count(*) FILTER(WHERE status='retry_wait'),'succeeded',count(*) FILTER(WHERE status='succeeded'),
   'failed',count(*) FILTER(WHERE status='failed'),'interrupted',count(*) FILTER(WHERE status='interrupted'),
   'stale',count(*) FILTER(WHERE status='stale'),'cancelled',count(*) FILTER(WHERE status='cancelled'),
   'recent_failures',count(*) FILTER(WHERE status IN ('failed','interrupted') AND finished_at::timestamptz>=now()-make_interval(mins=>window_minutes)),
   'oldest_pending_at',min(created_at) FILTER(WHERE status IN ('queued','retry_wait')),
   'risk_fingerprint',md5(coalesce(string_agg(id||':'||status||':'||attempt_count::text||':'||coalesce(finished_at,''),',' ORDER BY id),''))) INTO queue_stats
   FROM (
    SELECT 'ocr:'||id AS id,status,attempt_count,finished_at,created_at FROM accounting.ocr_jobs WHERE firm_id=target_firm AND company_id=target_company
    UNION ALL
    SELECT 'proposal:'||id,CASE WHEN status='waiting' THEN 'queued' ELSE status END,attempt_count,finished_at,created_at FROM accounting.proposal_jobs WHERE firm_id=target_firm AND company_id=target_company
   ) processing_jobs;
  queue_stats:=queue_stats||jsonb_build_object(
   'waiting',(SELECT count(*) FROM accounting.proposal_jobs WHERE firm_id=target_firm AND company_id=target_company AND status='waiting'),
   'needs_attention',(SELECT count(*) FROM accounting.proposal_jobs WHERE firm_id=target_firm AND company_id=target_company AND status='needs_attention'));
  SELECT jsonb_build_object('blocking_events',count(*) FILTER(WHERE blocking_count>0),'blocking_count',coalesce(sum(blocking_count),0),
   'warning_count',coalesce(sum(warning_count),0),'updated_at',max(updated_at),'oldest_opened_at',min(oldest_opened_at) FILTER(WHERE blocking_count>0),
   'risk_fingerprint',md5(coalesce(string_agg(event_id||':'||context_fingerprint||':'||blocking_count::text||':'||warning_count::text,',' ORDER BY event_id),''))) INTO exceptions
   FROM (
    SELECT event_id,blocking_count,warning_count,updated_at,oldest_opened_at,context_fingerprint
     FROM accounting.ops_exception_snapshots x WHERE firm_id=target_firm AND company_id=target_company
     AND EXISTS(SELECT 1 FROM accounting.ai_company_settings settings WHERE settings.firm_id=x.firm_id AND settings.company_id=x.company_id AND settings.enabled=1)
     AND NOT EXISTS(SELECT 1 FROM accounting.business_mappings m JOIN accounting.drafts d ON d.id=m.draft_id AND d.book_id=m.book_id WHERE m.event_id=x.event_id AND d.status<>'draft')
    UNION ALL
    SELECT p.event_id,1,0,max(p.updated_at),min(p.created_at),md5(string_agg(p.id||':'||p.status||':'||p.updated_at,',' ORDER BY p.id))
     FROM accounting.proposal_jobs p JOIN accounting.drafts d ON d.id=p.draft_id AND d.book_id=p.book_id AND d.status='draft'
     JOIN accounting.ai_company_settings enabled ON enabled.firm_id=p.firm_id AND enabled.company_id=p.company_id AND enabled.enabled=1
     WHERE p.firm_id=target_firm AND p.company_id=target_company AND p.status='needs_attention'
     AND NOT EXISTS(SELECT 1 FROM accounting.ops_exception_snapshots existing WHERE existing.event_id=p.event_id AND existing.firm_id=p.firm_id AND existing.company_id=p.company_id AND existing.blocking_count>0)
     GROUP BY p.event_id
   ) attention_events;
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
   WHEN 'task_failures' THEN count_value:=(q->>'recent_failures')::numeric;triggered:=count_value>=(p->>'failure_threshold')::numeric;message:='近期后台处理失败较多，请检查服务';
   WHEN 'exception_backlog' THEN count_value:=(e->>'blocking_events')::numeric;triggered:=count_value>=(p->>'backlog_threshold')::numeric AND (e->>'oldest_opened_at')::timestamptz<=now()-make_interval(mins=>(p->>'backlog_age_minutes')::integer);message:='会计核对异常积压，需要人工处理';
   WHEN 'queue_wait' THEN count_value:=(q->>'queued')::numeric+(q->>'retry_wait')::numeric;triggered:=count_value>0 AND (q->>'oldest_pending_at')::timestamptz<=now()-make_interval(mins=>(p->>'queue_wait_minutes')::integer);message:='后台任务等待时间过长';
   WHEN 'worker_unavailable' THEN count_value:=(q->>'queued')::numeric+(q->>'retry_wait')::numeric+(q->>'running')::numeric;triggered:=count_value>0 AND NOT (w->>'available')::boolean;message:='有待处理任务，但后台进程心跳不可用';severity_key:='critical';
   END CASE;
   IF coalesce(triggered,false) THEN
    risk:=CASE WHEN code_key IN ('budget_exhausted','quota_overrun','usage_uncertain') THEN c->>'risk_fingerprint' WHEN code_key='exception_backlog' THEN e->>'risk_fingerprint' ELSE q->>'risk_fingerprint' END;
    payload:=jsonb_build_object('message',message,'count',count_value);fp:=md5(code_key||payload::text||coalesce(risk,'')||coalesce(p->>'version',''));
    INSERT INTO accounting.ops_alerts(id,firm_id,company_id,code,severity,state,fingerprint,payload_json,opened_at,last_seen_at)
     VALUES(md5(target_firm||target_company||code_key),target_firm,target_company,code_key,severity_key,'open',fp,payload::text,stamp,stamp)
     ON CONFLICT(firm_id,company_id,code) DO UPDATE SET state=CASE WHEN ops_alerts.state='resolved' OR (ops_alerts.state='acknowledged' AND ops_alerts.fingerprint<>excluded.fingerprint) THEN 'open' ELSE ops_alerts.state END,
      severity=excluded.severity,fingerprint=excluded.fingerprint,payload_json=excluded.payload_json,last_seen_at=stamp,
      opened_at=CASE WHEN ops_alerts.state='resolved' THEN stamp ELSE ops_alerts.opened_at END,resolved_at=NULL,version=ops_alerts.version+CASE WHEN ops_alerts.fingerprint IS DISTINCT FROM excluded.fingerprint OR ops_alerts.state='resolved' OR ops_alerts.severity IS DISTINCT FROM excluded.severity THEN 1 ELSE 0 END;
   ELSE
    UPDATE accounting.ops_alerts SET state='resolved',resolved_at=stamp,last_seen_at=stamp,version=version+1 WHERE firm_id=target_firm AND company_id=target_company AND code=code_key AND state<>'resolved';
   END IF;
  END LOOP;
  SELECT count(*) INTO open_count FROM accounting.ops_alerts WHERE firm_id=target_firm AND company_id=target_company AND state<>'resolved';
  RETURN jsonb_build_object('refreshed',7,'open',open_count);
 END
$$;

CREATE INDEX idx_stage7_draft_page ON accounting.drafts(book_id,status,id);
CREATE INDEX idx_stage7_source_page ON accounting.sources(book_id,date,id);
CREATE INDEX idx_stage7_proposal_monitor ON accounting.proposal_jobs(firm_id,company_id,status,finished_at);
