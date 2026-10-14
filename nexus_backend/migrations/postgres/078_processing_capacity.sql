-- Fleet limits are administrator-controlled and apply to every worker process.
CREATE TABLE accounting.processing_runtime_limits(
 singleton INTEGER PRIMARY KEY CHECK(singleton=1),
 processing_global_limit INTEGER NOT NULL DEFAULT 2 CHECK(processing_global_limit BETWEEN 1 AND 8),
 provider_global_limit INTEGER NOT NULL DEFAULT 2 CHECK(provider_global_limit BETWEEN 1 AND 8),
 provider_default_limit INTEGER NOT NULL DEFAULT 2 CHECK(provider_default_limit BETWEEN 1 AND 8)
);
INSERT INTO accounting.processing_runtime_limits(singleton) VALUES(1);
CREATE TABLE accounting.provider_runtime_limits(provider TEXT PRIMARY KEY CHECK(provider ~ '^[a-z0-9.-]{1,253}$'),max_inflight INTEGER NOT NULL CHECK(max_inflight BETWEEN 1 AND 8));
CREATE TABLE accounting.provider_request_slots(
 id TEXT PRIMARY KEY CHECK(length(id)=32),firm_id TEXT NOT NULL,company_id TEXT NOT NULL,user_id TEXT NOT NULL,
 provider TEXT NOT NULL CHECK(provider ~ '^[a-z0-9.-]{1,253}$'),lease_until BIGINT NOT NULL,
 FOREIGN KEY(firm_id,company_id) REFERENCES accounting.firm_clients(firm_id,company_id),
 FOREIGN KEY(user_id) REFERENCES accounting.users(id)
);
CREATE INDEX provider_slots_expiration ON accounting.provider_request_slots(lease_until);
CREATE UNIQUE INDEX provider_slots_one_company ON accounting.provider_request_slots(firm_id,company_id);
ALTER TABLE accounting.processing_runtime_limits ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.processing_runtime_limits FORCE ROW LEVEL SECURITY;
ALTER TABLE accounting.provider_runtime_limits ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.provider_runtime_limits FORCE ROW LEVEL SECURITY;
ALTER TABLE accounting.provider_request_slots ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.provider_request_slots FORCE ROW LEVEL SECURITY;
REVOKE ALL ON accounting.processing_runtime_limits,accounting.provider_runtime_limits,accounting.provider_request_slots FROM PUBLIC,accounting_app,accounting_worker,accounting_system;

CREATE FUNCTION accounting.processing_limit(requested INTEGER) RETURNS INTEGER LANGUAGE plpgsql STABLE SECURITY DEFINER
 SET search_path=pg_catalog,accounting,pg_temp AS $$ BEGIN
 IF requested IS NULL OR requested NOT BETWEEN 1 AND 8 THEN RAISE EXCEPTION 'Bounded processing limit required' USING ERRCODE='22023';END IF;
 RETURN (SELECT least(processing_global_limit,requested) FROM accounting.processing_runtime_limits WHERE singleton=1);
 END $$;
CREATE FUNCTION accounting.lock_dispatch_write() RETURNS VOID LANGUAGE plpgsql VOLATILE SET search_path=pg_catalog,accounting,pg_temp AS $$ BEGIN
 IF coalesce(current_setting('app.write_scope_company',true),'')<>'' THEN RAISE EXCEPTION 'Dispatcher cannot enter a company transaction' USING ERRCODE='55000';END IF;
 PERFORM pg_advisory_xact_lock_shared(6420031786281903101);
 PERFORM pg_advisory_xact_lock(6420031786281903102);
 PERFORM set_config('app.write_dispatch','1',true);
 END $$;

-- Claims and bounded lease recovery use the same short scheduler mutex. Model
-- requests and company result writes never hold that mutex. Preserve all lease,
-- retry, queue input and current-authorization checks in the latest definition.
DO $$ DECLARE fn RECORD;definition TEXT;BEGIN
 FOR fn IN SELECT p.oid,p.proname FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
 WHERE n.nspname='accounting' AND p.proname=ANY(ARRAY['claim_ocr_job','claim_proposal_job','claim_processing_job','assistant_claim','recover_ocr_jobs','recover_proposal_jobs','assistant_recover','worker_refresh_proposal_dependencies']) LOOP
  definition:=replace(pg_get_functiondef(fn.oid),'accounting.lock_legacy_write()','accounting.lock_dispatch_write()');
  IF fn.proname LIKE 'claim_%' THEN
   definition:=replace(definition,'global_limit NOT BETWEEN 1 AND 2','global_limit NOT BETWEEN 1 AND 8');
   definition:=replace(definition,'>=global_limit','>=accounting.processing_limit(global_limit)');
  ELSIF fn.proname='assistant_claim' THEN
   definition:=replace(definition,'>=2 THEN RETURN','>=accounting.processing_limit(coalesce(nullif(current_setting(''app.processing_requested_limit'',true),''''),''2'')::integer) THEN RETURN');
   definition:=replace(definition,'accounting.assistant_company_schedule','accounting.ocr_company_schedule');
  END IF;
  IF fn.proname=ANY(ARRAY['recover_ocr_jobs','recover_proposal_jobs','worker_refresh_proposal_dependencies']) THEN
   definition:=replace(definition,'ORDER BY id FOR UPDATE LOOP','ORDER BY id LIMIT 100 FOR UPDATE SKIP LOCKED LOOP');
  END IF;
  EXECUTE definition;
 END LOOP;
END $$;

CREATE FUNCTION accounting.acquire_provider_slot(target_company TEXT,target_provider TEXT,target_id TEXT,lease_seconds INTEGER) RETURNS BOOLEAN
 LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE bounds accounting.processing_runtime_limits;provider_limit INTEGER;stamp BIGINT;BEGIN
 IF target_id IS NULL OR target_id!~'^[a-f0-9]{32}$' OR target_provider IS NULL OR target_provider!~'^[a-z0-9.-]{1,253}$'
 OR lease_seconds IS NULL OR lease_seconds NOT BETWEEN 20 AND 120 THEN RAISE EXCEPTION 'Invalid supplier slot bounds' USING ERRCODE='22023';END IF;
 IF NOT accounting.actor_active() OR NOT accounting.company_visible(target_company) THEN RAISE EXCEPTION 'Current company access required' USING ERRCODE='42501';END IF;
 PERFORM pg_advisory_xact_lock(6420031786281903103);
 stamp:=floor(extract(epoch FROM clock_timestamp()));
 DELETE FROM accounting.provider_request_slots WHERE lease_until<=stamp;
 SELECT * INTO bounds FROM accounting.processing_runtime_limits WHERE singleton=1;
 SELECT max_inflight INTO provider_limit FROM accounting.provider_runtime_limits WHERE provider=target_provider;
 provider_limit:=coalesce(provider_limit,bounds.provider_default_limit);
 IF EXISTS(SELECT 1 FROM accounting.provider_request_slots WHERE id=target_id) THEN RETURN false;END IF;
 IF (SELECT count(*) FROM accounting.provider_request_slots)>=bounds.provider_global_limit
 OR (SELECT count(*) FROM accounting.provider_request_slots WHERE provider=target_provider)>=provider_limit
 OR EXISTS(SELECT 1 FROM accounting.provider_request_slots WHERE firm_id=accounting.actor_firm() AND company_id=target_company) THEN RETURN false;END IF;
 INSERT INTO accounting.provider_request_slots(id,firm_id,company_id,user_id,provider,lease_until)
 VALUES(target_id,accounting.actor_firm(),target_company,accounting.actor_user(),target_provider,stamp+lease_seconds);
 RETURN true;END $$;
CREATE FUNCTION accounting.release_provider_slot(target_id TEXT) RETURNS VOID LANGUAGE plpgsql VOLATILE SECURITY DEFINER
 SET search_path=pg_catalog,accounting,pg_temp AS $$ BEGIN
 PERFORM pg_advisory_xact_lock(6420031786281903103);
 DELETE FROM accounting.provider_request_slots WHERE id=target_id AND firm_id=accounting.actor_firm() AND user_id=accounting.actor_user();
 END $$;
REVOKE ALL ON FUNCTION accounting.processing_limit(INTEGER),accounting.lock_dispatch_write(),accounting.acquire_provider_slot(TEXT,TEXT,TEXT,INTEGER),accounting.release_provider_slot(TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION accounting.processing_limit(INTEGER),accounting.lock_dispatch_write() TO accounting_worker;
GRANT EXECUTE ON FUNCTION accounting.acquire_provider_slot(TEXT,TEXT,TEXT,INTEGER),accounting.release_provider_slot(TEXT) TO accounting_app;
CREATE INDEX IF NOT EXISTS batch2_ocr_due ON accounting.ocr_jobs(status,available_at,firm_id,company_id,created_at,id);
CREATE INDEX IF NOT EXISTS batch2_proposal_due ON accounting.proposal_jobs(status,available_at,firm_id,company_id,created_at,id);
CREATE INDEX IF NOT EXISTS batch2_assistant_due ON accounting.assistant_turns(status,available_at,firm_id,company_id,created_at,id);
