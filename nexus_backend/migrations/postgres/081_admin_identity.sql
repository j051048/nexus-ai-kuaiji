CREATE TABLE accounting.admin_account_requests(
 id TEXT PRIMARY KEY,firm_id TEXT NOT NULL REFERENCES accounting.firms(id),
 created_by TEXT NOT NULL REFERENCES accounting.users(id),idempotency_key TEXT NOT NULL CHECK(length(trim(idempotency_key)) BETWEEN 8 AND 120),
 fingerprint TEXT NOT NULL CHECK(length(fingerprint)=64),name TEXT NOT NULL CHECK(length(trim(name)) BETWEEN 1 AND 120),email TEXT NOT NULL CHECK(email=lower(email) AND length(email) BETWEEN 3 AND 254),
 company_id TEXT,company_role TEXT,department_id TEXT,firm_role TEXT,
 status TEXT NOT NULL CHECK(status IN ('pending','awaiting_activation','completed','failed','cancelled')),
 attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts>=0),error TEXT NOT NULL DEFAULT '',
 token_digest TEXT NOT NULL DEFAULT '' CHECK(length(token_digest) IN(0,64)),
 expires_at BIGINT NOT NULL DEFAULT 0,lease_token TEXT NOT NULL DEFAULT '',lease_until BIGINT NOT NULL DEFAULT 0,
 provider_user_id TEXT,existing_user INTEGER NOT NULL DEFAULT 0 CHECK(existing_user IN(0,1)),
 completed_by TEXT REFERENCES accounting.users(id),created_at TEXT NOT NULL,updated_at TEXT NOT NULL,
 UNIQUE(firm_id,created_by,idempotency_key),
 FOREIGN KEY(firm_id,company_id) REFERENCES accounting.firm_clients(firm_id,company_id),
 FOREIGN KEY(department_id,company_id) REFERENCES accounting.departments(id,company_id),
 CHECK((company_id IS NOT NULL AND company_role IN('owner','admin','finance','employee') AND firm_role IS NULL)
 OR (company_id IS NULL AND company_role IS NULL AND department_id IS NULL AND firm_role IN('admin','operator','reviewer','viewer'))),
 CHECK((status='completed' AND completed_by IS NOT NULL AND completed_by=provider_user_id)
 OR (status<>'completed' AND completed_by IS NULL))
);
CREATE INDEX idx_admin_accounts_company ON accounting.admin_account_requests(firm_id,company_id,created_at);
CREATE TABLE accounting.admin_bootstrap_records(
 firm_id TEXT PRIMARY KEY REFERENCES accounting.firms(id),user_id TEXT NOT NULL REFERENCES accounting.users(id),created_at TEXT NOT NULL
);

ALTER TABLE accounting.admin_account_requests ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.admin_account_requests FORCE ROW LEVEL SECURITY;
ALTER TABLE accounting.admin_bootstrap_records ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.admin_bootstrap_records FORCE ROW LEVEL SECURITY;
GRANT SELECT,INSERT,UPDATE ON accounting.admin_account_requests TO accounting_app;
GRANT SELECT ON accounting.admin_account_requests TO accounting_system;
CREATE POLICY admin_account_identity_read ON accounting.admin_account_requests FOR SELECT TO accounting_system USING(true);
CREATE FUNCTION accounting.admin_account_manage(target_firm TEXT,target_company TEXT,target_role TEXT) RETURNS BOOLEAN
 LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 SELECT target_firm=accounting.actor_firm() AND accounting.actor_active() AND
 CASE WHEN target_company IS NULL THEN accounting.firm_manager(target_firm)
 ELSE accounting.company_manager(target_company) AND EXISTS(
 SELECT 1 FROM accounting.companies c JOIN accounting.firm_clients fc ON fc.company_id=c.id
 WHERE c.id=target_company AND c.status='active' AND fc.firm_id=target_firm AND fc.status='active')
 AND (target_role<>'owner' OR accounting.firm_manager(target_firm)) END
$$;
REVOKE ALL ON FUNCTION accounting.admin_account_manage(TEXT,TEXT,TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION accounting.admin_account_manage(TEXT,TEXT,TEXT) TO accounting_app;
CREATE POLICY admin_account_read ON accounting.admin_account_requests FOR SELECT TO accounting_app
 USING(accounting.admin_account_manage(firm_id,company_id,company_role));
CREATE POLICY admin_account_insert ON accounting.admin_account_requests FOR INSERT TO accounting_app
 WITH CHECK(created_by=accounting.actor_user() AND status='pending' AND attempts=0
 AND provider_user_id IS NULL AND completed_by IS NULL
 AND accounting.admin_account_manage(firm_id,company_id,company_role));
CREATE POLICY admin_account_update ON accounting.admin_account_requests FOR UPDATE TO accounting_app
 USING(accounting.admin_account_manage(firm_id,company_id,company_role))
 WITH CHECK(accounting.admin_account_manage(firm_id,company_id,company_role));
CREATE FUNCTION accounting.admin_account_guard() RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER
 SET search_path=pg_catalog,accounting,pg_temp AS $$
 BEGIN
 IF (NEW.id,NEW.firm_id,NEW.created_by,NEW.idempotency_key,NEW.fingerprint,NEW.name,NEW.email,
 NEW.company_id,NEW.company_role,NEW.department_id,NEW.firm_role,NEW.created_at)
 IS DISTINCT FROM
 (OLD.id,OLD.firm_id,OLD.created_by,OLD.idempotency_key,OLD.fingerprint,OLD.name,OLD.email,
 OLD.company_id,OLD.company_role,OLD.department_id,OLD.firm_role,OLD.created_at)
 OR (OLD.provider_user_id IS NOT NULL AND NEW.provider_user_id IS DISTINCT FROM OLD.provider_user_id)
 OR OLD.status IN('completed','cancelled') THEN
 RAISE EXCEPTION 'Account request identity and terminal states are immutable' USING ERRCODE='23514';
 END IF;
 IF NEW.status='completed' THEN
  IF OLD.status<>'awaiting_activation' OR NEW.token_digest IS DISTINCT FROM OLD.token_digest
   OR NEW.expires_at IS DISTINCT FROM OLD.expires_at OR NEW.expires_at<=extract(epoch from clock_timestamp())::bigint
   OR NOT EXISTS(SELECT 1 FROM accounting.users WHERE id=NEW.created_by AND active=1)
   OR NOT EXISTS(SELECT 1 FROM accounting.users WHERE id=NEW.completed_by AND active=1 AND lower(email)=NEW.email)
   OR (NEW.company_id IS NULL AND NOT EXISTS(SELECT 1 FROM accounting.memberships WHERE firm_id=NEW.firm_id AND user_id=NEW.created_by AND role IN('owner','admin')))
   OR (NEW.company_id IS NOT NULL AND (NOT EXISTS(SELECT 1 FROM accounting.companies c JOIN accounting.firm_clients fc ON fc.company_id=c.id WHERE c.id=NEW.company_id AND c.status='active' AND fc.firm_id=NEW.firm_id AND fc.status='active')
    OR NOT (EXISTS(SELECT 1 FROM accounting.memberships WHERE firm_id=NEW.firm_id AND user_id=NEW.created_by AND role IN('owner','admin'))
      OR (NEW.company_role<>'owner' AND EXISTS(SELECT 1 FROM accounting.company_memberships WHERE company_id=NEW.company_id AND user_id=NEW.created_by AND role IN('owner','admin'))))))
   OR (NEW.company_id IS NULL AND NOT EXISTS(SELECT 1 FROM accounting.memberships WHERE firm_id=NEW.firm_id AND user_id=NEW.completed_by AND role=NEW.firm_role))
   OR (NEW.company_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM accounting.company_memberships WHERE company_id=NEW.company_id AND user_id=NEW.completed_by AND role=NEW.company_role AND department_id IS NOT DISTINCT FROM NEW.department_id)) THEN
   RAISE EXCEPTION 'Recipient acceptance requires active originator, matching identity and scoped grant' USING ERRCODE='23514';
  END IF;
 END IF;
 RETURN NEW;
 END $$;
REVOKE ALL ON FUNCTION accounting.admin_account_guard() FROM PUBLIC;
CREATE TRIGGER admin_account_immutable BEFORE UPDATE ON accounting.admin_account_requests
 FOR EACH ROW EXECUTE FUNCTION accounting.admin_account_guard();

CREATE FUNCTION accounting.admin_account_audit_guard() RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER
 SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE invitation RECORD; BEGIN
 IF NEW.action NOT LIKE 'admin.account.%' THEN RETURN NEW; END IF;
 SELECT * INTO invitation FROM accounting.admin_account_requests WHERE id=NEW.object_id AND firm_id=NEW.firm_id;
 IF NOT FOUND OR NEW.company_id IS DISTINCT FROM invitation.company_id OR NEW.book_id IS NOT NULL
  OR NOT accounting.admin_account_manage(invitation.firm_id,invitation.company_id,invitation.company_role)
  OR NEW.user_id<>accounting.actor_user() THEN
  RAISE EXCEPTION 'Account audit must match an authorized invitation' USING ERRCODE='23514';
 END IF;
 IF NEW.action='admin.account.accept' AND (invitation.status<>'completed'
   OR invitation.completed_by IS DISTINCT FROM NEW.details::jsonb->>'accepted_by'
   OR invitation.created_by<>NEW.user_id) THEN
  RAISE EXCEPTION 'Account acceptance audit requires matching recipient and completed grant' USING ERRCODE='23514';
 END IF;
 RETURN NEW; END $$;
REVOKE ALL ON FUNCTION accounting.admin_account_audit_guard() FROM PUBLIC;
CREATE TRIGGER admin_account_audit_guard BEFORE INSERT ON accounting.audit_events
 FOR EACH ROW EXECUTE FUNCTION accounting.admin_account_audit_guard();
