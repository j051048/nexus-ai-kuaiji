-- compatibility: incremental
-- Read receipts belong to a reader; reading a broadcast must not read it for others.
CREATE TABLE IF NOT EXISTS accounting.client_message_reads (
 message_id TEXT NOT NULL REFERENCES accounting.client_messages(id),
 firm_id TEXT NOT NULL REFERENCES accounting.firms(id),
 company_id TEXT NOT NULL REFERENCES accounting.companies(id),
 user_id TEXT NOT NULL REFERENCES accounting.users(id),
 read_at TEXT NOT NULL,
 PRIMARY KEY(message_id,user_id)
);
ALTER TABLE accounting.client_message_reads ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.client_message_reads FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS client_messages_read ON accounting.client_messages;
DROP POLICY IF EXISTS client_messages_write ON accounting.client_messages;
CREATE POLICY client_messages_read ON accounting.client_messages FOR SELECT TO accounting_app
 USING(firm_id=accounting.actor_firm() AND accounting.actor_active()
 AND accounting.company_visible(company_id)
 AND EXISTS(SELECT 1 FROM accounting.companies c WHERE c.id=company_id AND c.status='active')
 AND (accounting.firm_manager(firm_id) OR recipient_user_id=accounting.actor_user()
 OR (recipient_user_id IS NULL AND EXISTS(SELECT 1 FROM accounting.company_memberships cm
 WHERE cm.company_id=client_messages.company_id AND cm.user_id=accounting.actor_user()))));
DROP POLICY IF EXISTS client_messages_insert ON accounting.client_messages;
CREATE POLICY client_messages_insert ON accounting.client_messages FOR INSERT TO accounting_app
 WITH CHECK(firm_id=accounting.actor_firm() AND accounting.firm_manager(firm_id)
 AND accounting.company_visible(company_id));
DROP POLICY IF EXISTS client_messages_update ON accounting.client_messages;
CREATE POLICY client_messages_update ON accounting.client_messages FOR UPDATE TO accounting_app
 USING(firm_id=accounting.actor_firm() AND accounting.firm_manager(firm_id) AND accounting.company_visible(company_id))
 WITH CHECK(firm_id=accounting.actor_firm() AND accounting.firm_manager(firm_id) AND accounting.company_visible(company_id));

DROP POLICY IF EXISTS client_message_reads_read ON accounting.client_message_reads;
CREATE POLICY client_message_reads_read ON accounting.client_message_reads FOR SELECT TO accounting_app
 USING(user_id=accounting.actor_user() AND firm_id=accounting.actor_firm() AND accounting.actor_active()
 AND EXISTS(SELECT 1 FROM accounting.client_messages m WHERE m.id=message_id AND m.firm_id=client_message_reads.firm_id AND m.company_id=client_message_reads.company_id));
DROP POLICY IF EXISTS client_message_reads_insert ON accounting.client_message_reads;
CREATE POLICY client_message_reads_insert ON accounting.client_message_reads FOR INSERT TO accounting_app
 WITH CHECK(user_id=accounting.actor_user() AND firm_id=accounting.actor_firm() AND accounting.actor_active()
 AND EXISTS(SELECT 1 FROM accounting.client_messages m WHERE m.id=message_id AND m.firm_id=client_message_reads.firm_id AND m.company_id=client_message_reads.company_id));
CREATE OR REPLACE FUNCTION accounting.client_message_receipt_stamp() RETURNS trigger
 LANGUAGE plpgsql SET search_path=pg_catalog,accounting,pg_temp AS $$
BEGIN
 NEW.read_at := clock_timestamp()::text;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION accounting.client_message_receipt_stamp() FROM PUBLIC;
DROP TRIGGER IF EXISTS client_message_receipt_stamp ON accounting.client_message_reads;
CREATE TRIGGER client_message_receipt_stamp BEFORE INSERT ON accounting.client_message_reads
 FOR EACH ROW EXECUTE FUNCTION accounting.client_message_receipt_stamp();
DROP TRIGGER IF EXISTS client_message_reads_write_gate ON accounting.client_message_reads;
CREATE TRIGGER client_message_reads_write_gate BEFORE INSERT OR UPDATE OR DELETE ON accounting.client_message_reads
 FOR EACH ROW EXECUTE FUNCTION accounting.company_write_table_guard();
REVOKE ALL ON accounting.client_message_reads FROM PUBLIC,accounting_app,accounting_worker;
GRANT SELECT,INSERT ON accounting.client_message_reads TO accounting_app;
