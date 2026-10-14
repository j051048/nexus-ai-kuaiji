-- Lifecycle changes preserve the company, originals, grants and accounting history.
ALTER TABLE companies ADD COLUMN status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','suspended','archived'));
ALTER TABLE companies ADD COLUMN version INTEGER NOT NULL DEFAULT 1 CHECK(version>0);
UPDATE companies SET status='suspended' WHERE EXISTS(SELECT 1 FROM firm_clients fc WHERE fc.company_id=companies.id AND fc.status='suspended') AND NOT EXISTS(SELECT 1 FROM firm_clients fc WHERE fc.company_id=companies.id AND fc.status='active');
-- Deliberately fails on historical sharing: it requires an explicit migration, never an implicit merge.
CREATE UNIQUE INDEX idx_admin_single_servicing_firm ON firm_clients(company_id);
CREATE INDEX idx_admin_company_status ON companies(status,name,id);
ALTER TABLE audit_events ADD COLUMN company_id TEXT REFERENCES companies(id);
CREATE INDEX idx_admin_audit_scope ON audit_events(firm_id,company_id,id);
UPDATE audit_events SET company_id=(SELECT b.company_id FROM books b WHERE b.id=audit_events.book_id) WHERE book_id IS NOT NULL;
UPDATE audit_events SET company_id=json_extract(details,'$.company_id') WHERE company_id IS NULL AND json_valid(details) AND EXISTS(SELECT 1 FROM firm_clients fc WHERE fc.company_id=json_extract(audit_events.details,'$.company_id') AND fc.firm_id=audit_events.firm_id);
UPDATE audit_events SET company_id=object_id WHERE company_id IS NULL AND action='company.create' AND EXISTS(SELECT 1 FROM firm_clients fc WHERE fc.company_id=audit_events.object_id AND fc.firm_id=audit_events.firm_id);
CREATE TRIGGER admin_audit_scope BEFORE INSERT ON audit_events WHEN NEW.company_id IS NOT NULL AND (
 NOT EXISTS(SELECT 1 FROM firm_clients fc WHERE fc.company_id=NEW.company_id AND fc.firm_id=NEW.firm_id)
 OR (NEW.book_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM books b WHERE b.id=NEW.book_id AND b.company_id=NEW.company_id AND b.firm_id=NEW.firm_id)))
BEGIN SELECT RAISE(ABORT,'Audit company and firm scope disagree'); END;
CREATE TRIGGER admin_company_version BEFORE UPDATE ON companies
WHEN NEW.id<>OLD.id OR NEW.version<>OLD.version+1
BEGIN SELECT RAISE(ABORT,'Company identity and consecutive version are protected'); END;
CREATE TRIGGER admin_company_lifecycle AFTER UPDATE OF status ON companies
BEGIN UPDATE firm_clients SET status=CASE WHEN NEW.status='active' THEN 'active' ELSE 'suspended' END WHERE company_id=NEW.id; END;
CREATE TRIGGER admin_client_identity BEFORE UPDATE ON firm_clients
WHEN NEW.firm_id<>OLD.firm_id OR NEW.company_id<>OLD.company_id OR (NEW.status='active' AND EXISTS(SELECT 1 FROM companies c WHERE c.id=NEW.company_id AND c.status<>'active'))
BEGIN SELECT RAISE(ABORT,'Company service identity and lifecycle are protected'); END;
CREATE TRIGGER admin_client_insert BEFORE INSERT ON firm_clients
WHEN NEW.status='active' AND EXISTS(SELECT 1 FROM companies c WHERE c.id=NEW.company_id AND c.status<>'active')
BEGIN SELECT RAISE(ABORT,'Inactive companies cannot receive active service'); END;
CREATE TRIGGER admin_client_preserve BEFORE DELETE ON firm_clients
BEGIN SELECT RAISE(ABORT,'Company service history is retained'); END;
CREATE TRIGGER admin_company_preserve BEFORE DELETE ON companies
BEGIN SELECT RAISE(ABORT,'Archive organizations; accounting evidence cannot be deleted'); END;
CREATE TRIGGER admin_owner_update BEFORE UPDATE ON memberships
WHEN NEW.firm_id<>OLD.firm_id OR NEW.user_id<>OLD.user_id OR (OLD.role='owner' AND NEW.role<>'owner')
BEGIN SELECT RAISE(ABORT,'Owner membership and identity are protected'); END;
CREATE TRIGGER admin_owner_delete BEFORE DELETE ON memberships WHEN OLD.role='owner'
BEGIN SELECT RAISE(ABORT,'Owner membership is protected'); END;
CREATE TRIGGER admin_company_member_delete_cleanup AFTER DELETE ON company_memberships
BEGIN
 DELETE FROM migration_project_access WHERE user_id=OLD.user_id AND project_id IN (SELECT p.id FROM migration_projects p WHERE p.company_id=OLD.company_id
 AND NOT EXISTS(SELECT 1 FROM memberships m WHERE m.firm_id=p.firm_id AND m.user_id=OLD.user_id));
 DELETE FROM ledger_managers WHERE user_id=OLD.user_id AND ledger_id IN (SELECT l.id FROM ledgers l WHERE l.company_id=OLD.company_id
 AND NOT EXISTS(SELECT 1 FROM memberships m WHERE m.firm_id=l.firm_id AND m.user_id=OLD.user_id));
 DELETE FROM book_access WHERE user_id=OLD.user_id AND book_id IN (SELECT b.id FROM books b WHERE b.company_id=OLD.company_id
 AND NOT EXISTS(SELECT 1 FROM memberships m WHERE m.firm_id=b.firm_id AND m.user_id=OLD.user_id));
END;
CREATE TRIGGER admin_company_member_downgrade_cleanup AFTER UPDATE OF role ON company_memberships WHEN NEW.role='employee' AND OLD.role<>'employee'
BEGIN
 DELETE FROM migration_project_access WHERE user_id=NEW.user_id AND project_id IN (SELECT p.id FROM migration_projects p WHERE p.company_id=NEW.company_id
 AND NOT EXISTS(SELECT 1 FROM memberships m WHERE m.firm_id=p.firm_id AND m.user_id=NEW.user_id));
 DELETE FROM ledger_managers WHERE user_id=NEW.user_id AND ledger_id IN (SELECT l.id FROM ledgers l WHERE l.company_id=NEW.company_id
 AND NOT EXISTS(SELECT 1 FROM memberships m WHERE m.firm_id=l.firm_id AND m.user_id=NEW.user_id));
 DELETE FROM book_access WHERE user_id=NEW.user_id AND book_id IN (SELECT b.id FROM books b WHERE b.company_id=NEW.company_id
 AND NOT EXISTS(SELECT 1 FROM memberships m WHERE m.firm_id=b.firm_id AND m.user_id=NEW.user_id));
END;
CREATE TRIGGER admin_firm_member_cleanup AFTER DELETE ON memberships
BEGIN
 DELETE FROM migration_project_access WHERE user_id=OLD.user_id AND project_id IN (SELECT id FROM migration_projects WHERE firm_id=OLD.firm_id);
 DELETE FROM ledger_managers WHERE user_id=OLD.user_id AND ledger_id IN (SELECT id FROM ledgers WHERE firm_id=OLD.firm_id);
 DELETE FROM book_access WHERE user_id=OLD.user_id AND firm_id=OLD.firm_id;
END;
