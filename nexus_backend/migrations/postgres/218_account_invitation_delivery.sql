-- compatibility: incremental
-- Opt-in invitation delivery. No activation secret is stored and no worker replays it.
CREATE TABLE IF NOT EXISTS accounting.account_invitation_deliveries(
 id TEXT PRIMARY KEY,request_id TEXT NOT NULL REFERENCES accounting.admin_account_requests(id),
 firm_id TEXT NOT NULL,company_id TEXT,request_attempt INTEGER NOT NULL CHECK(request_attempt>0),
 activation_digest TEXT NOT NULL CHECK(length(activation_digest)=64),
 recipient_email TEXT NOT NULL,idempotency_key TEXT NOT NULL CHECK(length(idempotency_key) BETWEEN 8 AND 120),
 created_by TEXT NOT NULL REFERENCES accounting.users(id),created_at TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN('sending','accepted','not_sent','unknown')),
 finished_at TEXT NOT NULL DEFAULT '',channel TEXT NOT NULL,
 UNIQUE(request_id,idempotency_key)
);
ALTER TABLE accounting.account_invitation_deliveries ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.account_invitation_deliveries FORCE ROW LEVEL SECURITY;
REVOKE ALL ON accounting.account_invitation_deliveries FROM PUBLIC,accounting_app,accounting_worker,accounting_system;
GRANT SELECT,INSERT,UPDATE ON accounting.account_invitation_deliveries TO accounting_app;
DROP POLICY IF EXISTS invitation_delivery_scope ON accounting.account_invitation_deliveries;
CREATE POLICY invitation_delivery_scope ON accounting.account_invitation_deliveries FOR ALL TO accounting_app
 USING(EXISTS(SELECT 1 FROM accounting.admin_account_requests r WHERE r.id=request_id AND r.firm_id=account_invitation_deliveries.firm_id AND r.company_id IS NOT DISTINCT FROM account_invitation_deliveries.company_id))
 WITH CHECK(EXISTS(SELECT 1 FROM accounting.admin_account_requests r WHERE r.id=request_id AND r.firm_id=account_invitation_deliveries.firm_id AND r.company_id IS NOT DISTINCT FROM account_invitation_deliveries.company_id));
CREATE OR REPLACE FUNCTION accounting.invitation_delivery_guard() RETURNS trigger
 LANGUAGE plpgsql SET search_path=pg_catalog,accounting,pg_temp AS $$
DECLARE r accounting.admin_account_requests;
BEGIN
 IF TG_OP='DELETE' THEN RAISE EXCEPTION 'Invitation evidence cannot be deleted' USING ERRCODE='23514'; END IF;
 IF NEW.created_by<>accounting.actor_user() OR NEW.firm_id<>accounting.actor_firm() THEN RAISE EXCEPTION 'Invitation actor mismatch' USING ERRCODE='42501'; END IF;
 IF TG_OP='INSERT' THEN
  SELECT * INTO r FROM accounting.admin_account_requests WHERE id=NEW.request_id;
  IF r.id IS NULL OR r.provisioning_mode<>'activation' OR r.status<>'awaiting_activation' OR r.expires_at<=extract(epoch FROM clock_timestamp())
   OR (NEW.firm_id,NEW.company_id,NEW.request_attempt,NEW.recipient_email) IS DISTINCT FROM (r.firm_id,r.company_id,r.attempts,r.email)
   OR NEW.state<>'sending' OR NEW.finished_at<>''
   OR NOT EXISTS(SELECT 1 FROM accounting.audit_events a WHERE a.object_id=r.id AND a.action='admin.account.link_created'
     AND a.details::jsonb->>'activation_digest'=NEW.activation_digest AND a.details::jsonb->>'request_attempt'=r.attempts::text)
   OR EXISTS(SELECT 1 FROM accounting.account_invitation_deliveries d WHERE d.request_id=r.id AND d.request_attempt=r.attempts AND d.state<>'not_sent')
   OR (SELECT count(*) FROM accounting.account_invitation_deliveries d WHERE d.request_id=r.id AND d.request_attempt=r.attempts)>=3
  THEN RAISE EXCEPTION 'Invalid or already attempted invitation' USING ERRCODE='23514'; END IF;
  NEW.created_at:=clock_timestamp()::text;
 ELSE
  IF OLD.state<>'sending' OR NEW.state NOT IN('accepted','not_sent','unknown')
    OR (to_jsonb(NEW)-'state'-'finished_at') IS DISTINCT FROM (to_jsonb(OLD)-'state'-'finished_at')
  THEN RAISE EXCEPTION 'Invitation result is fenced and immutable' USING ERRCODE='23514'; END IF;
  NEW.finished_at:=clock_timestamp()::text;
 END IF;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION accounting.invitation_delivery_guard() FROM PUBLIC;
DROP TRIGGER IF EXISTS invitation_delivery_guard ON accounting.account_invitation_deliveries;
CREATE TRIGGER invitation_delivery_guard BEFORE INSERT OR UPDATE OR DELETE ON accounting.account_invitation_deliveries FOR EACH ROW EXECUTE FUNCTION accounting.invitation_delivery_guard();
DROP TRIGGER IF EXISTS batch2_company_write_gate ON accounting.account_invitation_deliveries;
CREATE TRIGGER batch2_company_write_gate BEFORE INSERT OR UPDATE OR DELETE ON accounting.account_invitation_deliveries FOR EACH ROW EXECUTE FUNCTION accounting.company_write_table_guard();
DROP TRIGGER IF EXISTS batch2_statement_write_gate ON accounting.account_invitation_deliveries;
CREATE TRIGGER batch2_statement_write_gate BEFORE INSERT OR UPDATE OR DELETE ON accounting.account_invitation_deliveries FOR EACH STATEMENT EXECUTE FUNCTION accounting.compatibility_statement_gate();
