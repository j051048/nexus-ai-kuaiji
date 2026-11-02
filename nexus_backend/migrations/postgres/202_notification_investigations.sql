-- compatibility: incremental
SET LOCAL search_path=accounting,pg_catalog;
CREATE TABLE IF NOT EXISTS notification_investigations(
 id TEXT PRIMARY KEY,
 notification_id TEXT NOT NULL REFERENCES delivery_notifications(id),
 firm_id TEXT NOT NULL REFERENCES firms(id),
 company_id TEXT NOT NULL REFERENCES companies(id),
 book_id TEXT NOT NULL REFERENCES books(id),
 period TEXT NOT NULL,
 package_id TEXT NOT NULL REFERENCES monthly_delivery_packages(id),
 package_version INTEGER NOT NULL CHECK(package_version>0),
 manifest_sha256 TEXT NOT NULL CHECK(length(manifest_sha256)=64),
 recipient_user_id TEXT NOT NULL REFERENCES users(id),
 attempts INTEGER NOT NULL CHECK(attempts>=0),
 revision INTEGER NOT NULL CHECK(revision>0),
 conclusion TEXT NOT NULL CHECK(conclusion IN('accepted_by_channel','not_sent','unresolved')),
 evidence_ref TEXT NOT NULL CHECK(length(trim(evidence_ref)) BETWEEN 3 AND 1000),
 reason TEXT NOT NULL CHECK(length(trim(reason)) BETWEEN 3 AND 1000),
 idempotency_key TEXT NOT NULL CHECK(length(idempotency_key) BETWEEN 8 AND 120),
 created_by TEXT NOT NULL REFERENCES users(id),
 created_at TEXT NOT NULL,
 UNIQUE(notification_id,revision), UNIQUE(notification_id,idempotency_key)
);
CREATE INDEX IF NOT EXISTS notification_investigation_scope ON notification_investigations(firm_id,book_id,notification_id,revision);

ALTER TABLE accounting.notification_investigations ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.notification_investigations FORCE ROW LEVEL SECURITY;
REVOKE ALL ON accounting.notification_investigations FROM PUBLIC,accounting_app,accounting_worker;
GRANT SELECT,INSERT ON accounting.notification_investigations TO accounting_app;
DROP POLICY IF EXISTS notification_investigation_read ON accounting.notification_investigations;
CREATE POLICY notification_investigation_read ON accounting.notification_investigations FOR SELECT TO accounting_app
 USING(firm_id=accounting.actor_firm() AND accounting.book_allowed(book_id) AND EXISTS(
 SELECT 1 FROM accounting.memberships m WHERE m.firm_id=notification_investigations.firm_id
 AND m.user_id=accounting.actor_user() AND m.role IN('owner','admin','reviewer')));
DROP POLICY IF EXISTS notification_investigation_write ON accounting.notification_investigations;
CREATE POLICY notification_investigation_write ON accounting.notification_investigations FOR INSERT TO accounting_app
 WITH CHECK(firm_id=accounting.actor_firm() AND accounting.firm_manager(firm_id)
 AND accounting.book_allowed(book_id) AND created_by=accounting.actor_user());
CREATE OR REPLACE FUNCTION accounting.notification_investigation_guard() RETURNS TRIGGER
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
BEGIN
 IF TG_OP<>'INSERT' THEN RAISE EXCEPTION 'Investigation history is append only' USING ERRCODE='23514'; END IF;
 PERFORM accounting.lock_company_write(NEW.firm_id,NEW.company_id);
 IF NOT accounting.actor_active() OR NEW.firm_id IS DISTINCT FROM accounting.actor_firm()
 OR NOT accounting.firm_manager(NEW.firm_id) OR NOT accounting.book_allowed(NEW.book_id)
 OR NEW.created_by IS DISTINCT FROM accounting.actor_user() THEN
 RAISE EXCEPTION 'Current manager required' USING ERRCODE='42501'; END IF;
 IF EXISTS(SELECT 1 FROM accounting.restore_state WHERE hold<>FALSE OR hold IS NULL) THEN
 RAISE EXCEPTION 'Recovery hold prevents new investigations' USING ERRCODE='23514'; END IF;
 IF NOT EXISTS(SELECT 1 FROM accounting.delivery_notifications n WHERE n.id=NEW.notification_id
 AND n.firm_id=NEW.firm_id AND n.company_id=NEW.company_id AND n.book_id=NEW.book_id AND n.period=NEW.period
 AND n.package_id=NEW.package_id AND n.package_version=NEW.package_version AND n.manifest_sha256=NEW.manifest_sha256
 AND n.recipient_user_id=NEW.recipient_user_id AND n.attempts=NEW.attempts AND n.state='unknown')
 OR NEW.revision<>(SELECT COALESCE(MAX(revision),0)+1 FROM accounting.notification_investigations WHERE notification_id=NEW.notification_id)
 THEN RAISE EXCEPTION 'Unknown notification source or investigation revision changed' USING ERRCODE='23514'; END IF;
 NEW.created_at:=clock_timestamp()::text;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION accounting.notification_investigation_guard() FROM PUBLIC;
DROP TRIGGER IF EXISTS notification_investigation_guard ON accounting.notification_investigations;
CREATE TRIGGER notification_investigation_guard BEFORE INSERT OR UPDATE OR DELETE ON accounting.notification_investigations
 FOR EACH ROW EXECUTE FUNCTION accounting.notification_investigation_guard();
DROP TRIGGER IF EXISTS notification_investigation_company_gate ON accounting.notification_investigations;
CREATE TRIGGER notification_investigation_company_gate BEFORE INSERT OR UPDATE OR DELETE ON accounting.notification_investigations
 FOR EACH ROW EXECUTE FUNCTION accounting.company_write_table_guard();
DROP TRIGGER IF EXISTS notification_investigation_statement_gate ON accounting.notification_investigations;
CREATE TRIGGER notification_investigation_statement_gate BEFORE INSERT OR UPDATE OR DELETE ON accounting.notification_investigations
 FOR EACH STATEMENT EXECUTE FUNCTION accounting.compatibility_statement_gate();
