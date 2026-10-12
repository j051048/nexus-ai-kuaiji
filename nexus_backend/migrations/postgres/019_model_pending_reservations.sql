-- Reserved total means current-month pending held reservations, not historical caps.
CREATE OR REPLACE FUNCTION accounting.ops_company_metrics_internal(target_firm TEXT,target_company TEXT,target_period TEXT) RETURNS JSONB
 LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE p JSONB; costs JSONB; queue_stats JSONB; exceptions JSONB; workers JSONB; window_minutes INTEGER; stale_seconds INTEGER; carried NUMERIC; carried_unknown BIGINT; carried_risk TEXT; historical_overruns BIGINT;
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
