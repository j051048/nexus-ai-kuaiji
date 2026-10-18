-- compatibility: incremental
CREATE TABLE IF NOT EXISTS accounting.firm_task_assignments(
 firm_id TEXT NOT NULL REFERENCES accounting.firms(id),
 kind TEXT NOT NULL,
 ref TEXT NOT NULL,
 assignee_id TEXT REFERENCES accounting.users(id),
 due_on TEXT,
 sla_state TEXT NOT NULL DEFAULT 'normal' CHECK(sla_state IN('normal','today','overdue','blocked')),
 version INTEGER NOT NULL DEFAULT 1 CHECK(version>0),
 note TEXT NOT NULL DEFAULT '',
 updated_by TEXT NOT NULL REFERENCES accounting.users(id),
 updated_at TEXT NOT NULL,
 PRIMARY KEY(firm_id,kind,ref)
);
CREATE TABLE IF NOT EXISTS accounting.client_messages(
 id TEXT PRIMARY KEY,
 firm_id TEXT NOT NULL REFERENCES accounting.firms(id),
 company_id TEXT NOT NULL REFERENCES accounting.companies(id),
 recipient_user_id TEXT REFERENCES accounting.users(id),
 kind TEXT NOT NULL CHECK(kind IN('reminder','return','system','approval')),
 subject TEXT NOT NULL,
 body TEXT NOT NULL,
 related_kind TEXT NOT NULL DEFAULT '',
 related_ref TEXT NOT NULL DEFAULT '',
 created_by TEXT NOT NULL REFERENCES accounting.users(id),
 created_at TEXT NOT NULL,
 read_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_firm_task_assignments_assignee
 ON accounting.firm_task_assignments(firm_id,assignee_id,due_on);
CREATE INDEX IF NOT EXISTS idx_client_messages_company
 ON accounting.client_messages(firm_id,company_id,created_at DESC,id);
CREATE INDEX IF NOT EXISTS idx_client_messages_recipient
 ON accounting.client_messages(firm_id,recipient_user_id,created_at DESC,id);

ALTER TABLE accounting.firm_task_assignments ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.firm_task_assignments FORCE ROW LEVEL SECURITY;
ALTER TABLE accounting.client_messages ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.client_messages FORCE ROW LEVEL SECURITY;
CREATE POLICY firm_task_assignments_read ON accounting.firm_task_assignments FOR SELECT TO accounting_app
 USING(firm_id=accounting.actor_firm());
CREATE POLICY firm_task_assignments_write ON accounting.firm_task_assignments FOR ALL TO accounting_app
 USING(firm_id=accounting.actor_firm() AND (
  assignee_id=accounting.actor_user() OR EXISTS(
   SELECT 1 FROM accounting.memberships m WHERE m.firm_id=firm_task_assignments.firm_id
   AND m.user_id=accounting.actor_user() AND m.role IN('owner','admin'))))
 WITH CHECK(firm_id=accounting.actor_firm() AND (
  assignee_id=accounting.actor_user() OR EXISTS(
   SELECT 1 FROM accounting.memberships m WHERE m.firm_id=firm_task_assignments.firm_id
   AND m.user_id=accounting.actor_user() AND m.role IN('owner','admin'))));
CREATE POLICY client_messages_read ON accounting.client_messages FOR SELECT TO accounting_app
 USING(firm_id=accounting.actor_firm() AND (
  recipient_user_id=accounting.actor_user() OR EXISTS(
   SELECT 1 FROM accounting.company_memberships cm WHERE cm.company_id=client_messages.company_id
   AND cm.user_id=accounting.actor_user()) OR EXISTS(
   SELECT 1 FROM accounting.memberships m WHERE m.firm_id=client_messages.firm_id
   AND m.user_id=accounting.actor_user() AND m.role IN('owner','admin'))));
CREATE POLICY client_messages_write ON accounting.client_messages FOR ALL TO accounting_app
 USING(firm_id=accounting.actor_firm() AND EXISTS(
  SELECT 1 FROM accounting.memberships m WHERE m.firm_id=client_messages.firm_id
  AND m.user_id=accounting.actor_user() AND m.role IN('owner','admin')))
 WITH CHECK(firm_id=accounting.actor_firm() AND EXISTS(
  SELECT 1 FROM accounting.memberships m WHERE m.firm_id=client_messages.firm_id
  AND m.user_id=accounting.actor_user() AND m.role IN('owner','admin')));
REVOKE ALL ON accounting.firm_task_assignments,accounting.client_messages FROM PUBLIC,accounting_app,accounting_worker;
GRANT SELECT,INSERT,UPDATE ON accounting.firm_task_assignments,accounting.client_messages TO accounting_app;
CREATE TRIGGER firm_task_assignments_write_gate BEFORE INSERT OR UPDATE OR DELETE ON accounting.firm_task_assignments FOR EACH ROW EXECUTE FUNCTION accounting.company_write_table_guard();
CREATE TRIGGER client_messages_write_gate BEFORE INSERT OR UPDATE OR DELETE ON accounting.client_messages FOR EACH ROW EXECUTE FUNCTION accounting.company_write_table_guard();
