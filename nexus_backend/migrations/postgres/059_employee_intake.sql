-- Private pre-business originals with current membership and shared model budgets.
CREATE TABLE accounting.intake_drafts(
 id TEXT PRIMARY KEY,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,department_id TEXT NOT NULL,created_by TEXT NOT NULL REFERENCES accounting.users(id),
 template_id TEXT NOT NULL CHECK(template_id IN ('reimbursement','procurement_expense','daily_cashflow')),
 fields_json TEXT NOT NULL CHECK(jsonb_typeof(fields_json::jsonb)='object'),
 status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','completed')),version INTEGER NOT NULL DEFAULT 1 CHECK(version>0),
 create_key TEXT NOT NULL,create_fingerprint TEXT NOT NULL CHECK(length(create_fingerprint)=64),
 event_id TEXT,completion_key TEXT,completion_fingerprint TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,
 UNIQUE(firm_id,created_by,create_key),UNIQUE(id,firm_id,company_id),
 FOREIGN KEY(firm_id,company_id) REFERENCES accounting.firm_clients(firm_id,company_id),FOREIGN KEY(department_id,company_id) REFERENCES accounting.departments(id,company_id),
 FOREIGN KEY(event_id,firm_id,company_id) REFERENCES accounting.business_events(id,firm_id,company_id),
 CHECK((status='draft' AND event_id IS NULL AND completion_key IS NULL AND completion_fingerprint IS NULL) OR (status='completed' AND event_id IS NOT NULL AND completion_key IS NOT NULL AND completion_fingerprint IS NOT NULL AND length(completion_fingerprint)=64))
);
CREATE INDEX idx_intake_own ON accounting.intake_drafts(firm_id,created_by,status,updated_at DESC,id);
CREATE TABLE accounting.intake_attachments(
 id TEXT PRIMARY KEY,intake_id TEXT NOT NULL,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,
 storage_key TEXT NOT NULL,filename TEXT NOT NULL CHECK(length(filename) BETWEEN 1 AND 150),
 media_type TEXT NOT NULL CHECK(media_type IN ('application/pdf','image/png','image/jpeg','image/webp')),
 sha256 TEXT NOT NULL CHECK(length(sha256)=64),size_bytes INTEGER NOT NULL CHECK(size_bytes BETWEEN 1 AND 10485760),created_at TEXT NOT NULL,created_by TEXT NOT NULL REFERENCES accounting.users(id),
 UNIQUE(intake_id,sha256),UNIQUE(id,intake_id),FOREIGN KEY(intake_id,firm_id,company_id) REFERENCES accounting.intake_drafts(id,firm_id,company_id)
);
CREATE TABLE accounting.intake_runs(
 id TEXT PRIMARY KEY,intake_id TEXT NOT NULL,attachment_id TEXT NOT NULL,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,created_by TEXT NOT NULL REFERENCES accounting.users(id),
 input_fingerprint TEXT NOT NULL CHECK(length(input_fingerprint)=64),idempotency_key TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('running','succeeded','failed','stale')),result_json TEXT CHECK(result_json IS NULL OR (jsonb_typeof(result_json::jsonb)='object')),
 provider TEXT NOT NULL,model TEXT NOT NULL,prompt_version TEXT NOT NULL,usage_json TEXT NOT NULL DEFAULT '{}',
 error_code TEXT NOT NULL DEFAULT '',error_detail TEXT NOT NULL DEFAULT '',checks_json TEXT NOT NULL DEFAULT '[]',created_at TEXT NOT NULL,finished_at TEXT,
 UNIQUE(intake_id,idempotency_key),UNIQUE(id,firm_id,company_id,created_by),
 FOREIGN KEY(intake_id,firm_id,company_id) REFERENCES accounting.intake_drafts(id,firm_id,company_id),FOREIGN KEY(attachment_id,intake_id) REFERENCES accounting.intake_attachments(id,intake_id),
 CHECK((status='running' AND result_json IS NULL AND finished_at IS NULL) OR (status='succeeded' AND result_json IS NOT NULL AND finished_at IS NOT NULL) OR (status IN ('failed','stale') AND result_json IS NULL AND finished_at IS NOT NULL))
);
CREATE INDEX idx_intake_runs_attachment ON accounting.intake_runs(intake_id,attachment_id,created_at DESC,id DESC);

CREATE FUNCTION accounting.intake_allowed(target_id TEXT,editable BOOLEAN DEFAULT false) RETURNS BOOLEAN LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 SELECT EXISTS(SELECT 1 FROM accounting.intake_drafts d JOIN accounting.company_memberships cm ON cm.company_id=d.company_id AND cm.user_id=d.created_by
 WHERE d.id=target_id AND d.firm_id=accounting.actor_firm() AND d.created_by=accounting.actor_user() AND accounting.business_member(d.firm_id,d.company_id)
 AND (NOT editable OR (d.status='draft' AND (cm.role<>'employee' OR cm.department_id=d.department_id))))
$$;
CREATE FUNCTION accounting.intake_process_allowed(target_id TEXT) RETURNS BOOLEAN LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 SELECT accounting.intake_allowed(target_id,true) AND EXISTS(SELECT 1 FROM accounting.intake_drafts d JOIN accounting.ai_company_settings s ON s.firm_id=d.firm_id AND s.company_id=d.company_id WHERE d.id=target_id AND s.enabled=1)
$$;
CREATE FUNCTION accounting.intake_draft_guard() RETURNS TRIGGER LANGUAGE plpgsql SET search_path=pg_catalog,accounting,pg_temp AS $$ BEGIN
 IF TG_OP='INSERT' THEN
  IF NEW.status<>'draft' OR NEW.version<>1 THEN RAISE EXCEPTION 'Intake starts incomplete at version one'; END IF;
 ELSE
  IF OLD.status<>'draft' OR NEW.version<>OLD.version+1 OR (NEW.id,NEW.firm_id,NEW.company_id,NEW.department_id,NEW.created_by,NEW.create_key,NEW.create_fingerprint,NEW.created_at) IS DISTINCT FROM (OLD.id,OLD.firm_id,OLD.company_id,OLD.department_id,OLD.created_by,OLD.create_key,OLD.create_fingerprint,OLD.created_at) THEN RAISE EXCEPTION 'Intake identity and completed evidence are immutable'; END IF;
  IF NEW.status='completed' AND (NOT EXISTS(SELECT 1 FROM accounting.business_events e WHERE e.id=NEW.event_id AND e.firm_id=NEW.firm_id AND e.company_id=NEW.company_id AND e.department_id=NEW.department_id AND e.applicant_id=NEW.created_by AND e.template_id=NEW.template_id AND e.payload::jsonb=NEW.fields_json::jsonb AND e.status IN ('draft','submitted')) OR EXISTS(SELECT 1 FROM accounting.intake_attachments a WHERE a.intake_id=NEW.id AND NOT EXISTS(SELECT 1 FROM accounting.business_attachments b WHERE b.id=a.id AND b.event_id=NEW.event_id AND b.storage_key=a.storage_key AND b.sha256=a.sha256 AND b.size_bytes=a.size_bytes))) THEN RAISE EXCEPTION 'Completion must reference matching confirmed facts and originals'; END IF;
 END IF;
 RETURN NEW; END $$;
CREATE TRIGGER intake_draft_scope BEFORE INSERT OR UPDATE ON accounting.intake_drafts FOR EACH ROW EXECUTE FUNCTION accounting.intake_draft_guard();
CREATE FUNCTION accounting.intake_attachment_guard() RETURNS TRIGGER LANGUAGE plpgsql SET search_path=pg_catalog,accounting,pg_temp AS $$ BEGIN
 IF NOT accounting.intake_allowed(NEW.intake_id,true) OR (SELECT count(*) FROM accounting.intake_attachments WHERE intake_id=NEW.intake_id)>=10 OR (SELECT coalesce(sum(size_bytes),0) FROM accounting.intake_attachments WHERE intake_id=NEW.intake_id)+NEW.size_bytes>52428800 THEN RAISE EXCEPTION 'Intake attachment scope or limits invalid'; END IF;
 RETURN NEW; END $$;
CREATE TRIGGER intake_attachment_scope BEFORE INSERT ON accounting.intake_attachments FOR EACH ROW EXECUTE FUNCTION accounting.intake_attachment_guard();
CREATE TRIGGER intake_original_immutable BEFORE UPDATE OR DELETE ON accounting.intake_attachments FOR EACH ROW EXECUTE FUNCTION accounting.ai_immutable_guard();
CREATE FUNCTION accounting.intake_run_guard() RETURNS TRIGGER LANGUAGE plpgsql SET search_path=pg_catalog,accounting,pg_temp AS $$ BEGIN
 IF OLD.status<>'running' OR NEW.status NOT IN ('succeeded','failed','stale') OR (NEW.id,NEW.intake_id,NEW.attachment_id,NEW.firm_id,NEW.company_id,NEW.created_by,NEW.input_fingerprint,NEW.idempotency_key,NEW.provider,NEW.model,NEW.prompt_version,NEW.created_at) IS DISTINCT FROM (OLD.id,OLD.intake_id,OLD.attachment_id,OLD.firm_id,OLD.company_id,OLD.created_by,OLD.input_fingerprint,OLD.idempotency_key,OLD.provider,OLD.model,OLD.prompt_version,OLD.created_at) THEN RAISE EXCEPTION 'Intake model inputs and terminal outputs are immutable'; END IF;
 RETURN NEW; END $$;
CREATE TRIGGER intake_run_identity BEFORE UPDATE ON accounting.intake_runs FOR EACH ROW EXECUTE FUNCTION accounting.intake_run_guard();
CREATE TRIGGER intake_runs_no_delete BEFORE DELETE ON accounting.intake_runs FOR EACH ROW EXECUTE FUNCTION accounting.ai_immutable_guard();
ALTER TABLE accounting.intake_drafts ENABLE ROW LEVEL SECURITY;ALTER TABLE accounting.intake_drafts FORCE ROW LEVEL SECURITY;
ALTER TABLE accounting.intake_attachments ENABLE ROW LEVEL SECURITY;ALTER TABLE accounting.intake_attachments FORCE ROW LEVEL SECURITY;
ALTER TABLE accounting.intake_runs ENABLE ROW LEVEL SECURITY;ALTER TABLE accounting.intake_runs FORCE ROW LEVEL SECURITY;
GRANT SELECT,INSERT,UPDATE ON accounting.intake_drafts,accounting.intake_runs TO accounting_app;
GRANT SELECT,INSERT ON accounting.intake_attachments TO accounting_app;
CREATE POLICY intake_drafts_read ON accounting.intake_drafts FOR SELECT TO accounting_app USING(accounting.intake_allowed(id));
CREATE POLICY intake_drafts_create ON accounting.intake_drafts FOR INSERT TO accounting_app WITH CHECK(firm_id=accounting.actor_firm() AND created_by=accounting.actor_user() AND accounting.business_member(firm_id,company_id) AND EXISTS(SELECT 1 FROM accounting.company_memberships cm WHERE cm.company_id=intake_drafts.company_id AND cm.user_id=created_by AND (cm.role<>'employee' OR cm.department_id=intake_drafts.department_id)));
CREATE POLICY intake_drafts_edit ON accounting.intake_drafts FOR UPDATE TO accounting_app USING(accounting.intake_allowed(id,true)) WITH CHECK(accounting.intake_allowed(id));
CREATE POLICY intake_attachments_read ON accounting.intake_attachments FOR SELECT TO accounting_app USING(accounting.intake_allowed(intake_id));
CREATE POLICY intake_attachments_create ON accounting.intake_attachments FOR INSERT TO accounting_app WITH CHECK(created_by=accounting.actor_user() AND accounting.intake_allowed(intake_id,true));
CREATE POLICY intake_runs_read ON accounting.intake_runs FOR SELECT TO accounting_app USING(accounting.intake_allowed(intake_id));
CREATE POLICY intake_runs_create ON accounting.intake_runs FOR INSERT TO accounting_app WITH CHECK(created_by=accounting.actor_user() AND status='running' AND accounting.intake_process_allowed(intake_id));
CREATE POLICY intake_runs_finish ON accounting.intake_runs FOR UPDATE TO accounting_app USING(created_by=accounting.actor_user() AND status='running' AND accounting.intake_process_allowed(intake_id)) WITH CHECK(accounting.intake_process_allowed(intake_id));
CREATE FUNCTION accounting.abandon_intake_run(target_id TEXT) RETURNS VOID LANGUAGE sql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 UPDATE accounting.intake_runs SET status='stale',result_json=NULL,error_code='scope_changed',error_detail='权限或企业处理配置已改变，结果未发布',finished_at=accounting.ocr_utc_now() WHERE id=target_id AND created_by=accounting.actor_user() AND firm_id=accounting.actor_firm() AND status='running'
$$;
ALTER TABLE accounting.model_costs ALTER COLUMN run_id DROP NOT NULL;
ALTER TABLE accounting.model_costs ALTER COLUMN event_id DROP NOT NULL;
ALTER TABLE accounting.model_costs ADD COLUMN intake_run_id TEXT UNIQUE;
ALTER TABLE accounting.model_costs ADD CONSTRAINT model_cost_intake_scope FOREIGN KEY(intake_run_id,firm_id,company_id,created_by) REFERENCES accounting.intake_runs(id,firm_id,company_id,created_by);
ALTER TABLE accounting.model_costs ADD CONSTRAINT model_cost_source_kind CHECK((intake_run_id IS NULL AND run_id IS NOT NULL AND event_id IS NOT NULL) OR (intake_run_id IS NOT NULL AND intake_run_id=id AND run_id IS NULL AND event_id IS NULL AND kind='ocr'));
CREATE FUNCTION accounting.model_cost_intake_identity_guard() RETURNS TRIGGER LANGUAGE plpgsql SET search_path=pg_catalog,accounting,pg_temp AS $$ BEGIN
 IF NEW.intake_run_id IS DISTINCT FROM OLD.intake_run_id THEN RAISE EXCEPTION 'Model intake provenance is immutable' USING ERRCODE='23514'; END IF;RETURN NEW;END $$;
CREATE TRIGGER model_cost_intake_identity BEFORE UPDATE ON accounting.model_costs FOR EACH ROW EXECUTE FUNCTION accounting.model_cost_intake_identity_guard();
CREATE FUNCTION accounting.reserve_intake_model_cost(target_run TEXT,target_token TEXT,target_provider TEXT,target_model TEXT,target_pricing TEXT,target_reserve BIGINT) RETURNS TEXT
 LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE r accounting.intake_runs; c accounting.model_costs; p accounting.model_company_policies; period_key TEXT; exposure NUMERIC; stamp TEXT; reserve_amount BIGINT;
 BEGIN
  PERFORM pg_advisory_xact_lock(6420031786281903101);
  SELECT * INTO r FROM accounting.intake_runs WHERE id=target_run AND firm_id=accounting.actor_firm() AND created_by=accounting.actor_user();
  IF r.id IS NULL THEN RETURN 'authorization_changed'; END IF;
  IF NOT accounting.intake_process_allowed(r.intake_id) THEN RETURN 'authorization_changed'; END IF;
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
   reserve_amount:=p.ocr_reserve_micro;
   IF p.monthly_limit_micro<=0 OR reserve_amount<=0 THEN RETURN 'policy_invalid'; END IF;
   IF EXISTS(SELECT 1 FROM accounting.model_costs WHERE firm_id=r.firm_id AND company_id=r.company_id AND overrun=1) THEN RETURN 'quota_overrun'; END IF;
   SELECT coalesce(sum(CASE WHEN period=period_key THEN CASE WHEN state='released' THEN 0 ELSE coalesce(confirmed_micro,estimated_micro,reserved_micro) END
    WHEN period<period_key AND state IN ('reserved','sent','uncertain') THEN reserved_micro ELSE 0 END),0) INTO exposure
    FROM accounting.model_costs WHERE firm_id=r.firm_id AND company_id=r.company_id AND period<=period_key;
   IF exposure+reserve_amount>p.monthly_limit_micro THEN RETURN 'quota_exhausted'; END IF;
  END IF;
  stamp:=accounting.ocr_utc_now();
  INSERT INTO accounting.model_costs(id,run_id,intake_run_id,firm_id,company_id,event_id,kind,created_by,period,provider,model,charge_token,pricing_json,quota_enabled,reserved_micro,state,created_at)
   VALUES(r.id,NULL,r.id,r.firm_id,r.company_id,NULL,'ocr',r.created_by,period_key,r.provider,r.model,target_token,target_pricing,coalesce(p.quota_enabled,0),reserve_amount,'reserved',stamp);
  RETURN 'reserved';
 END
$$;

CREATE OR REPLACE FUNCTION accounting.settle_model_cost(target_run TEXT,target_token TEXT,target_input_tokens BIGINT,target_output_tokens BIGINT,safe_rejection BOOLEAN DEFAULT false) RETURNS BOOLEAN
 LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE c accounting.model_costs; amount NUMERIC; next_state TEXT; stamp TEXT; overflowed BOOLEAN:=false;
 BEGIN
  PERFORM pg_advisory_xact_lock(6420031786281903101);
  SELECT * INTO c FROM accounting.model_costs WHERE id=target_run AND charge_token=target_token AND created_by=accounting.actor_user() AND firm_id=accounting.actor_firm();
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

CREATE OR REPLACE FUNCTION accounting.mark_model_cost_sent(target_run TEXT,target_token TEXT) RETURNS BOOLEAN
 LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE c accounting.model_costs;
 BEGIN
  PERFORM pg_advisory_xact_lock(6420031786281903101);
  SELECT * INTO c FROM accounting.model_costs WHERE id=target_run AND charge_token=target_token AND created_by=accounting.actor_user() AND firm_id=accounting.actor_firm();
  IF c.id IS NULL THEN RETURN false; END IF;
  IF c.intake_run_id IS NOT NULL THEN
   IF NOT EXISTS(SELECT 1 FROM accounting.intake_runs r WHERE r.id=c.intake_run_id AND accounting.intake_process_allowed(r.intake_id)) THEN RETURN false; END IF;
  ELSIF NOT accounting.ai_event_processor(c.event_id,c.firm_id,c.company_id) OR NOT accounting.ai_event_open(c.event_id,c.firm_id,c.company_id) THEN RETURN false; END IF;
  IF c.state='sent' THEN RETURN true; END IF;
  IF c.state<>'reserved' THEN RETURN false; END IF;
  UPDATE accounting.model_costs SET state='sent',sent_at=accounting.ocr_utc_now(),version=version+1 WHERE id=c.id;
  RETURN true;
 END
$$;
CREATE OR REPLACE FUNCTION accounting.manual_reconcile_model_cost(target_run TEXT,expected_version INTEGER,target_confirmed BIGINT,reason TEXT) RETURNS BOOLEAN
 LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE c accounting.model_costs; stamp TEXT;
 BEGIN
  IF target_confirmed IS NULL OR target_confirmed NOT BETWEEN 0 AND 999999999999990000 OR length(btrim(reason)) NOT BETWEEN 1 AND 1000 THEN RETURN false; END IF;
  PERFORM pg_advisory_xact_lock(6420031786281903101);
  SELECT * INTO c FROM accounting.model_costs WHERE id=target_run AND firm_id=accounting.actor_firm() AND accounting.company_manager(company_id);
  IF c.id IS NULL OR c.version<>expected_version OR c.state NOT IN ('uncertain','estimated','reconciled') THEN RETURN false; END IF;
  stamp:=accounting.ocr_utc_now();
  UPDATE accounting.model_costs SET confirmed_micro=target_confirmed,state='reconciled',overrun=0,manual_reason=btrim(reason),manual_by=accounting.actor_user(),version=version+1 WHERE id=c.id;
  INSERT INTO accounting.audit_events(firm_id,book_id,user_id,action,object_id,details,created_at)
   VALUES(c.firm_id,NULL,accounting.actor_user(),'model.cost.reconcile',c.id,jsonb_build_object('previous_confirmed_micro',c.confirmed_micro,'confirmed_micro',target_confirmed,'reason',btrim(reason),'version',c.version+1)::text,stamp);
  RETURN true;
 END
$$;
CREATE TRIGGER intake_cost_changes AFTER INSERT OR UPDATE ON accounting.intake_runs FOR EACH ROW EXECUTE FUNCTION accounting.ops_mark_changed();
REVOKE ALL ON FUNCTION accounting.intake_allowed(TEXT,BOOLEAN),accounting.intake_process_allowed(TEXT),accounting.intake_draft_guard(),accounting.intake_attachment_guard(),accounting.intake_run_guard(),accounting.abandon_intake_run(TEXT),accounting.model_cost_intake_identity_guard(),accounting.reserve_intake_model_cost(TEXT,TEXT,TEXT,TEXT,TEXT,BIGINT) FROM PUBLIC,accounting_worker;
GRANT EXECUTE ON FUNCTION accounting.intake_allowed(TEXT,BOOLEAN),accounting.intake_process_allowed(TEXT),accounting.abandon_intake_run(TEXT),accounting.reserve_intake_model_cost(TEXT,TEXT,TEXT,TEXT,TEXT,BIGINT) TO accounting_app;

CREATE FUNCTION accounting.intake_request_unresolved(target_draft TEXT,target_attachment TEXT) RETURNS BOOLEAN LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 SELECT accounting.intake_allowed(target_draft,true) AND EXISTS(SELECT 1 FROM accounting.model_costs c JOIN accounting.intake_runs r ON r.id=c.intake_run_id WHERE r.intake_id=target_draft AND r.attachment_id=target_attachment AND c.state IN ('reserved','sent','uncertain'))
$$;
REVOKE ALL ON FUNCTION accounting.intake_request_unresolved(TEXT,TEXT) FROM PUBLIC,accounting_worker;
GRANT EXECUTE ON FUNCTION accounting.intake_request_unresolved(TEXT,TEXT) TO accounting_app;
