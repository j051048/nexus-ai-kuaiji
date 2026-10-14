CREATE TABLE admin_account_requests(
 id TEXT PRIMARY KEY,firm_id TEXT NOT NULL REFERENCES firms(id),
 created_by TEXT NOT NULL REFERENCES users(id),idempotency_key TEXT NOT NULL CHECK(length(trim(idempotency_key)) BETWEEN 8 AND 120),
 fingerprint TEXT NOT NULL CHECK(length(fingerprint)=64),name TEXT NOT NULL CHECK(length(trim(name)) BETWEEN 1 AND 120),email TEXT NOT NULL CHECK(email=lower(email) AND length(email) BETWEEN 3 AND 254),
 company_id TEXT,company_role TEXT,department_id TEXT,firm_role TEXT,
 status TEXT NOT NULL CHECK(status IN ('pending','awaiting_activation','completed','failed','cancelled')),
 attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts>=0),error TEXT NOT NULL DEFAULT '',
 token_digest TEXT NOT NULL DEFAULT '' CHECK(length(token_digest) IN(0,64)),
 expires_at BIGINT NOT NULL DEFAULT 0,lease_token TEXT NOT NULL DEFAULT '',lease_until BIGINT NOT NULL DEFAULT 0,
 provider_user_id TEXT,existing_user INTEGER NOT NULL DEFAULT 0 CHECK(existing_user IN(0,1)),
 completed_by TEXT REFERENCES users(id),created_at TEXT NOT NULL,updated_at TEXT NOT NULL,
 UNIQUE(firm_id,created_by,idempotency_key),
 FOREIGN KEY(firm_id,company_id) REFERENCES firm_clients(firm_id,company_id),
 FOREIGN KEY(department_id,company_id) REFERENCES departments(id,company_id),
 CHECK((company_id IS NOT NULL AND company_role IN('owner','admin','finance','employee') AND firm_role IS NULL)
 OR (company_id IS NULL AND company_role IS NULL AND department_id IS NULL AND firm_role IN('admin','operator','reviewer','viewer'))),
 CHECK((status='completed' AND completed_by IS NOT NULL AND completed_by=provider_user_id)
 OR (status<>'completed' AND completed_by IS NULL))
);
CREATE INDEX idx_admin_accounts_company ON admin_account_requests(firm_id,company_id,created_at);
CREATE TABLE admin_bootstrap_records(
 firm_id TEXT PRIMARY KEY REFERENCES firms(id),user_id TEXT NOT NULL REFERENCES users(id),created_at TEXT NOT NULL
);

CREATE TRIGGER admin_account_immutable BEFORE UPDATE ON admin_account_requests
WHEN NEW.id<>OLD.id OR NEW.firm_id<>OLD.firm_id OR NEW.created_by<>OLD.created_by OR
 NEW.idempotency_key<>OLD.idempotency_key OR NEW.fingerprint<>OLD.fingerprint OR
 NEW.name<>OLD.name OR NEW.email<>OLD.email OR NEW.company_id IS NOT OLD.company_id OR
 NEW.company_role IS NOT OLD.company_role OR NEW.department_id IS NOT OLD.department_id OR
 NEW.firm_role IS NOT OLD.firm_role OR NEW.created_at<>OLD.created_at OR
 (OLD.provider_user_id IS NOT NULL AND NEW.provider_user_id IS NOT OLD.provider_user_id) OR
 OLD.status IN('completed','cancelled')
BEGIN SELECT RAISE(ABORT,'Account request identity and terminal states are immutable'); END;
CREATE TRIGGER admin_account_no_delete BEFORE DELETE ON admin_account_requests
BEGIN SELECT RAISE(ABORT,'Account provisioning history cannot be deleted'); END;

CREATE TRIGGER admin_account_completion BEFORE UPDATE ON admin_account_requests WHEN NEW.status='completed' AND (
 OLD.status<>'awaiting_activation' OR NEW.token_digest IS NOT OLD.token_digest OR NEW.expires_at IS NOT OLD.expires_at
 OR NEW.expires_at<=CAST(strftime('%s','now') AS INTEGER)
 OR NOT EXISTS(SELECT 1 FROM users WHERE id=NEW.created_by AND active=1)
 OR NOT EXISTS(SELECT 1 FROM users WHERE id=NEW.completed_by AND active=1 AND lower(email)=NEW.email)
 OR (NEW.company_id IS NULL AND NOT EXISTS(SELECT 1 FROM memberships WHERE firm_id=NEW.firm_id AND user_id=NEW.created_by AND role IN('owner','admin')))
 OR (NEW.company_id IS NOT NULL AND (NOT EXISTS(SELECT 1 FROM companies c JOIN firm_clients fc ON fc.company_id=c.id WHERE c.id=NEW.company_id AND c.status='active' AND fc.firm_id=NEW.firm_id AND fc.status='active')
  OR NOT (EXISTS(SELECT 1 FROM memberships WHERE firm_id=NEW.firm_id AND user_id=NEW.created_by AND role IN('owner','admin'))
   OR (NEW.company_role<>'owner' AND EXISTS(SELECT 1 FROM company_memberships WHERE company_id=NEW.company_id AND user_id=NEW.created_by AND role IN('owner','admin'))))))
 OR (NEW.company_id IS NULL AND NOT EXISTS(SELECT 1 FROM memberships WHERE firm_id=NEW.firm_id AND user_id=NEW.completed_by AND role=NEW.firm_role))
 OR (NEW.company_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM company_memberships WHERE company_id=NEW.company_id AND user_id=NEW.completed_by AND role=NEW.company_role AND department_id IS NEW.department_id)))
BEGIN SELECT RAISE(ABORT,'Recipient acceptance requires active originator, matching identity and scoped grant'); END;
CREATE TRIGGER admin_account_audit BEFORE INSERT ON audit_events WHEN NEW.action LIKE 'admin.account.%' AND (
 NOT EXISTS(SELECT 1 FROM admin_account_requests r WHERE r.id=NEW.object_id AND r.firm_id=NEW.firm_id AND r.company_id IS NEW.company_id)
 OR (NEW.action='admin.account.accept' AND NOT EXISTS(SELECT 1 FROM admin_account_requests r WHERE r.id=NEW.object_id AND r.status='completed' AND r.created_by=NEW.user_id AND r.completed_by=json_extract(NEW.details,'$.accepted_by'))))
BEGIN SELECT RAISE(ABORT,'Account audit must match an authorized invitation'); END;
