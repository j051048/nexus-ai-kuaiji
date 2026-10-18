-- compatibility: incremental
-- The firm dashboard may observe shared processing availability, not worker
-- identities, process topology, or other firms' in-flight workload. Keep the
-- heartbeat table inaccessible to accounting_app; expose only this aggregate.
CREATE FUNCTION accounting.firm_worker_health() RETURNS JSONB
 LANGUAGE plpgsql STABLE SECURITY DEFINER
 SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE result JSONB;
 BEGIN
  IF NOT accounting.actor_active()
   OR NOT accounting.firm_manager(accounting.actor_firm()) THEN
   RAISE EXCEPTION 'Current firm manager required' USING ERRCODE='42501';
  END IF;
  SELECT jsonb_build_object(
   'available',coalesce(bool_or(state IN ('active','idle')
    AND heartbeat_at::timestamptz>=now()-interval '90 seconds'),false),
   'last_heartbeat_at',max(heartbeat_at),
   'stale_after_seconds',90
  ) INTO result FROM accounting.worker_heartbeats;
  RETURN result;
 END
$$;
REVOKE ALL ON FUNCTION accounting.firm_worker_health()
 FROM PUBLIC,accounting_app,accounting_worker,accounting_system;
GRANT EXECUTE ON FUNCTION accounting.firm_worker_health() TO accounting_app;
