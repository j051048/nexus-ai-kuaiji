-- compatibility: incremental
-- A client opening records that the contact reached this exact frozen version.
-- It is view evidence, never a signature: the acknowledgement table stays the only receipt.
CREATE TABLE IF NOT EXISTS accounting.monthly_delivery_opens(
 package_id TEXT PRIMARY KEY,book_id TEXT NOT NULL,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,
 opened_by TEXT NOT NULL REFERENCES accounting.users(id),opened_at TEXT NOT NULL,
 FOREIGN KEY(package_id,book_id,firm_id,company_id) REFERENCES accounting.monthly_delivery_packages(id,book_id,firm_id,company_id)
);
CREATE OR REPLACE FUNCTION accounting.monthly_delivery_open_guard() RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER
 SET search_path=pg_catalog,accounting,pg_temp AS $$
DECLARE p RECORD;
BEGIN
 SELECT * INTO p FROM accounting.monthly_delivery_packages WHERE id=NEW.package_id AND book_id=NEW.book_id;
 IF NOT FOUND OR p.firm_id IS DISTINCT FROM NEW.firm_id OR p.company_id IS DISTINCT FROM NEW.company_id
 OR p.contact_user_id IS DISTINCT FROM NEW.opened_by
 OR NEW.opened_by IS DISTINCT FROM accounting.actor_user()
 OR NEW.firm_id IS DISTINCT FROM accounting.actor_firm()
 OR NOT accounting.monthly_delivery_contact_eligible(NEW.book_id,NEW.opened_by) THEN
  RAISE EXCEPTION 'Current client contact required to open a package' USING ERRCODE='42501';
 END IF;
 PERFORM accounting.lock_company_write(p.firm_id,p.company_id);
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION accounting.monthly_delivery_open_guard() FROM PUBLIC;
ALTER TABLE accounting.monthly_delivery_opens ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.monthly_delivery_opens FORCE ROW LEVEL SECURITY;
REVOKE ALL ON accounting.monthly_delivery_opens FROM PUBLIC,accounting_app,accounting_worker,accounting_system,accounting_projection;
GRANT SELECT,INSERT ON accounting.monthly_delivery_opens TO accounting_app;
CREATE POLICY monthly_delivery_open_read ON accounting.monthly_delivery_opens FOR SELECT TO accounting_app
 USING(firm_id=accounting.actor_firm() AND accounting.book_allowed(book_id));
CREATE POLICY monthly_delivery_open_insert ON accounting.monthly_delivery_opens FOR INSERT TO accounting_app
 WITH CHECK(firm_id=accounting.actor_firm() AND opened_by=accounting.actor_user()
 AND accounting.monthly_delivery_contact_eligible(book_id,opened_by));
DROP TRIGGER IF EXISTS monthly_delivery_open_guard ON accounting.monthly_delivery_opens;
CREATE TRIGGER monthly_delivery_open_guard BEFORE INSERT ON accounting.monthly_delivery_opens
 FOR EACH ROW EXECUTE FUNCTION accounting.monthly_delivery_open_guard();
DROP TRIGGER IF EXISTS monthly_delivery_open_immutable ON accounting.monthly_delivery_opens;
CREATE TRIGGER monthly_delivery_open_immutable BEFORE UPDATE OR DELETE ON accounting.monthly_delivery_opens
 FOR EACH ROW EXECUTE FUNCTION accounting.delivery_immutable();
