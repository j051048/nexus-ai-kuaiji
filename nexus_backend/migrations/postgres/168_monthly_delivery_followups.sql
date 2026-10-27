-- compatibility: incremental
-- Delivery configuration and reminder tasks for already-locked historical periods.
-- A grant configures *who may receive and sign next*, never a past package: each
-- monthly_delivery_packages row keeps the contact_user_id frozen at generation time.
CREATE TABLE IF NOT EXISTS accounting.monthly_delivery_grants(
 id TEXT PRIMARY KEY,
 firm_id TEXT NOT NULL REFERENCES accounting.firms(id),
 company_id TEXT NOT NULL REFERENCES accounting.companies(id),
 book_id TEXT NOT NULL REFERENCES accounting.books(id),
 period TEXT NOT NULL,
 recipient_user_id TEXT NOT NULL REFERENCES accounting.users(id),
 version INTEGER NOT NULL CHECK(version>0),
 state TEXT NOT NULL CHECK(state IN('active','revoked')),
 granted_by TEXT NOT NULL REFERENCES accounting.users(id),
 granted_at TEXT NOT NULL,
 revoked_by TEXT REFERENCES accounting.users(id),
 revoked_at TEXT,
 reason TEXT NOT NULL,
 UNIQUE(book_id,period,version)
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_monthly_delivery_grants_active
 ON accounting.monthly_delivery_grants(book_id,period) WHERE state='active';
CREATE INDEX IF NOT EXISTS idx_monthly_delivery_grants_recipient
 ON accounting.monthly_delivery_grants(firm_id,recipient_user_id,period);

CREATE TABLE IF NOT EXISTS accounting.delivery_notifications(
 id TEXT PRIMARY KEY,
 firm_id TEXT NOT NULL REFERENCES accounting.firms(id),
 company_id TEXT NOT NULL REFERENCES accounting.companies(id),
 book_id TEXT NOT NULL REFERENCES accounting.books(id),
 period TEXT NOT NULL,
 package_id TEXT,
 kind TEXT NOT NULL CHECK(kind IN('signature_reminder','missing_contact_followup','signature_overdue')),
 recipient_user_id TEXT REFERENCES accounting.users(id),
 grant_id TEXT REFERENCES accounting.monthly_delivery_grants(id),
 dedupe_key TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN('pending','sink_written','failed','cancelled')),
 attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts>=0),
 last_error TEXT NOT NULL DEFAULT '',
 payload_json TEXT NOT NULL,
 sink_ref TEXT NOT NULL DEFAULT '',
 created_by TEXT NOT NULL REFERENCES accounting.users(id),
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL,
 dispatched_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_delivery_notifications_dedupe
 ON accounting.delivery_notifications(dedupe_key);
CREATE INDEX IF NOT EXISTS idx_delivery_notifications_pending
 ON accounting.delivery_notifications(state,created_at);

CREATE OR REPLACE FUNCTION accounting.delivery_grant_guard() RETURNS TRIGGER LANGUAGE plpgsql
 SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
DECLARE b RECORD;
BEGIN
 SELECT * INTO b FROM accounting.books WHERE id=NEW.book_id;
 IF NOT FOUND OR b.firm_id IS DISTINCT FROM NEW.firm_id OR b.company_id IS DISTINCT FROM NEW.company_id
 OR NEW.firm_id IS DISTINCT FROM accounting.actor_firm() THEN
  RAISE EXCEPTION 'Delivery grant must match the current firm and book' USING ERRCODE='42501';
 END IF;
 IF NEW.state='active' AND NOT EXISTS(
  SELECT 1 FROM accounting.company_memberships cm
  WHERE cm.company_id=NEW.company_id AND cm.user_id=NEW.recipient_user_id
 ) THEN
  RAISE EXCEPTION 'Delivery recipient must be a current member of that company' USING ERRCODE='23514';
 END IF;
 IF TG_OP='UPDATE' AND (NEW.book_id,NEW.period,NEW.version,NEW.recipient_user_id)
    IS DISTINCT FROM (OLD.book_id,OLD.period,OLD.version,OLD.recipient_user_id) THEN
  RAISE EXCEPTION 'Delivery grant identity is immutable; issue a new version' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION accounting.delivery_grant_guard() FROM PUBLIC;
DROP TRIGGER IF EXISTS delivery_grant_guard ON accounting.monthly_delivery_grants;
CREATE TRIGGER delivery_grant_guard BEFORE INSERT OR UPDATE ON accounting.monthly_delivery_grants
 FOR EACH ROW EXECUTE FUNCTION accounting.delivery_grant_guard();

CREATE OR REPLACE FUNCTION accounting.delivery_notification_guard() RETURNS TRIGGER LANGUAGE plpgsql
 SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
BEGIN
 IF TG_OP='UPDATE' AND OLD.state='cancelled' THEN
  RAISE EXCEPTION 'Cancelled delivery notification is terminal' USING ERRCODE='23514';
 END IF;
 IF TG_OP='UPDATE' AND (NEW.dedupe_key,NEW.book_id,NEW.period,NEW.kind)
    IS DISTINCT FROM (OLD.dedupe_key,OLD.book_id,OLD.period,OLD.kind) THEN
  RAISE EXCEPTION 'Delivery notification identity is immutable' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION accounting.delivery_notification_guard() FROM PUBLIC;
DROP TRIGGER IF EXISTS delivery_notification_guard ON accounting.delivery_notifications;
CREATE TRIGGER delivery_notification_guard BEFORE UPDATE ON accounting.delivery_notifications
 FOR EACH ROW EXECUTE FUNCTION accounting.delivery_notification_guard();

ALTER TABLE accounting.monthly_delivery_grants ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.monthly_delivery_grants FORCE ROW LEVEL SECURITY;
ALTER TABLE accounting.delivery_notifications ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.delivery_notifications FORCE ROW LEVEL SECURITY;
REVOKE ALL ON accounting.monthly_delivery_grants FROM PUBLIC,accounting_app,accounting_worker,accounting_system,accounting_projection;
REVOKE ALL ON accounting.delivery_notifications FROM PUBLIC,accounting_app,accounting_worker,accounting_system,accounting_projection;
GRANT SELECT,INSERT,UPDATE ON accounting.monthly_delivery_grants TO accounting_app;
GRANT SELECT,INSERT,UPDATE ON accounting.delivery_notifications TO accounting_app;
CREATE POLICY monthly_delivery_grants_read ON accounting.monthly_delivery_grants FOR SELECT TO accounting_app
 USING(firm_id=accounting.actor_firm() AND accounting.book_allowed(book_id));
CREATE POLICY monthly_delivery_grants_insert ON accounting.monthly_delivery_grants FOR INSERT TO accounting_app
 WITH CHECK(firm_id=accounting.actor_firm() AND granted_by=accounting.actor_user()
 AND accounting.book_allowed(book_id) AND EXISTS(
  SELECT 1 FROM accounting.memberships m WHERE m.firm_id=monthly_delivery_grants.firm_id
  AND m.user_id=accounting.actor_user() AND m.role IN('owner','admin','reviewer')));
CREATE POLICY monthly_delivery_grants_update ON accounting.monthly_delivery_grants FOR UPDATE TO accounting_app
 USING(firm_id=accounting.actor_firm() AND accounting.book_allowed(book_id))
 WITH CHECK(firm_id=accounting.actor_firm() AND accounting.book_allowed(book_id));
CREATE POLICY delivery_notifications_read ON accounting.delivery_notifications FOR SELECT TO accounting_app
 USING(firm_id=accounting.actor_firm() AND accounting.book_allowed(book_id));
CREATE POLICY delivery_notifications_insert ON accounting.delivery_notifications FOR INSERT TO accounting_app
 WITH CHECK(firm_id=accounting.actor_firm() AND created_by=accounting.actor_user()
 AND accounting.book_allowed(book_id));
CREATE POLICY delivery_notifications_update ON accounting.delivery_notifications FOR UPDATE TO accounting_app
 USING(firm_id=accounting.actor_firm() AND accounting.book_allowed(book_id))
 WITH CHECK(firm_id=accounting.actor_firm() AND accounting.book_allowed(book_id));
