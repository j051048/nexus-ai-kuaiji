-- Passwords and password hashes never enter this schema. Persist only the
-- immutable provisioning mode so invitations cannot be retried as password grants.
ALTER TABLE accounting.admin_account_requests ADD COLUMN provisioning_mode TEXT NOT NULL DEFAULT 'activation'
 CHECK(provisioning_mode IN('activation','password') AND
 (provisioning_mode='activation' OR (company_id IS NOT NULL AND firm_role IS NULL)));

CREATE OR REPLACE FUNCTION accounting.admin_account_guard() RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER
 SET search_path=pg_catalog,accounting,pg_temp AS $$
 BEGIN
 IF (TG_OP='INSERT' OR NEW.status IN('pending','completed'))
  AND NEW.provisioning_mode='password' AND NEW.company_role='employee' AND NEW.department_id IS NULL
  AND COALESCE((SELECT mode FROM accounting.business_submission_policies
    WHERE firm_id=NEW.firm_id AND company_id=NEW.company_id),'department')='department' THEN
  RAISE EXCEPTION 'Department approval requires an employee department before password provisioning' USING ERRCODE='23514';
 END IF;
 IF TG_OP='INSERT' THEN RETURN NEW; END IF;
 IF (NEW.id,NEW.firm_id,NEW.created_by,NEW.idempotency_key,NEW.fingerprint,NEW.name,NEW.email,
 NEW.company_id,NEW.company_role,NEW.department_id,NEW.firm_role,NEW.provisioning_mode,NEW.created_at)
 IS DISTINCT FROM
 (OLD.id,OLD.firm_id,OLD.created_by,OLD.idempotency_key,OLD.fingerprint,OLD.name,OLD.email,
 OLD.company_id,OLD.company_role,OLD.department_id,OLD.firm_role,OLD.provisioning_mode,OLD.created_at)
 OR (OLD.provider_user_id IS NOT NULL AND NEW.provider_user_id IS DISTINCT FROM OLD.provider_user_id)
 OR OLD.status IN('completed','cancelled') THEN
 RAISE EXCEPTION 'Account request identity and terminal states are immutable' USING ERRCODE='23514';
 END IF;
 IF NEW.status='completed' THEN
  IF (NEW.provisioning_mode='activation' AND (
    OLD.status<>'awaiting_activation' OR NEW.token_digest IS DISTINCT FROM OLD.token_digest
    OR NEW.expires_at IS DISTINCT FROM OLD.expires_at OR NEW.expires_at<=extract(epoch from clock_timestamp())::bigint))
   OR (NEW.provisioning_mode='password' AND (
    OLD.status<>'pending' OR OLD.lease_token='' OR OLD.lease_until<=extract(epoch from clock_timestamp())::bigint
    OR NEW.existing_user<>0 OR NEW.token_digest<>'' OR NEW.expires_at<>0))
   OR NOT EXISTS(SELECT 1 FROM accounting.users WHERE id=NEW.created_by AND active=1)
   OR NOT EXISTS(SELECT 1 FROM accounting.users WHERE id=NEW.completed_by AND active=1 AND lower(email)=NEW.email)
   OR (NEW.company_id IS NULL AND NOT EXISTS(SELECT 1 FROM accounting.memberships WHERE firm_id=NEW.firm_id AND user_id=NEW.created_by AND role IN('owner','admin')))
   OR (NEW.company_id IS NOT NULL AND (NOT EXISTS(SELECT 1 FROM accounting.companies c JOIN accounting.firm_clients fc ON fc.company_id=c.id WHERE c.id=NEW.company_id AND c.status='active' AND fc.firm_id=NEW.firm_id AND fc.status='active')
    OR NOT (EXISTS(SELECT 1 FROM accounting.memberships WHERE firm_id=NEW.firm_id AND user_id=NEW.created_by AND role IN('owner','admin'))
      OR (NEW.company_role<>'owner' AND EXISTS(SELECT 1 FROM accounting.company_memberships WHERE company_id=NEW.company_id AND user_id=NEW.created_by AND role IN('owner','admin'))))))
   OR (NEW.company_id IS NULL AND NOT EXISTS(SELECT 1 FROM accounting.memberships WHERE firm_id=NEW.firm_id AND user_id=NEW.completed_by AND role=NEW.firm_role))
   OR (NEW.company_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM accounting.company_memberships WHERE company_id=NEW.company_id AND user_id=NEW.completed_by AND role=NEW.company_role AND department_id IS NOT DISTINCT FROM NEW.department_id)) THEN
   RAISE EXCEPTION 'Account completion requires active originator, matching identity and scoped grant' USING ERRCODE='23514';
  END IF;
 END IF;
 RETURN NEW;
 END $$;
REVOKE ALL ON FUNCTION accounting.admin_account_guard() FROM PUBLIC;
CREATE TRIGGER admin_account_insert_guard BEFORE INSERT ON accounting.admin_account_requests
 FOR EACH ROW EXECUTE FUNCTION accounting.admin_account_guard();

CREATE OR REPLACE FUNCTION accounting.admin_account_audit_guard() RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER
 SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE invitation RECORD; BEGIN
 IF NEW.action NOT LIKE 'admin.account.%' THEN RETURN NEW; END IF;
 SELECT * INTO invitation FROM accounting.admin_account_requests WHERE id=NEW.object_id AND firm_id=NEW.firm_id;
 IF NOT FOUND OR NEW.company_id IS DISTINCT FROM invitation.company_id OR NEW.book_id IS NOT NULL
  OR NOT accounting.admin_account_manage(invitation.firm_id,invitation.company_id,invitation.company_role)
  OR NEW.user_id<>accounting.actor_user() THEN
  RAISE EXCEPTION 'Account audit must match an authorized invitation' USING ERRCODE='23514';
 END IF;
 IF NEW.action='admin.account.accept' AND (invitation.provisioning_mode<>'activation' OR invitation.status<>'completed'
   OR invitation.completed_by IS DISTINCT FROM NEW.details::jsonb->>'accepted_by'
   OR invitation.created_by<>NEW.user_id) THEN
  RAISE EXCEPTION 'Account acceptance audit requires matching recipient and completed grant' USING ERRCODE='23514';
 END IF;
 IF NEW.action='admin.account.password_created' AND (invitation.provisioning_mode<>'password'
   OR invitation.status<>'completed' OR invitation.completed_by IS DISTINCT FROM NEW.details::jsonb->>'created_user_id') THEN
  RAISE EXCEPTION 'Password account audit requires a completed scoped account' USING ERRCODE='23514';
 END IF;
 RETURN NEW; END $$;
REVOKE ALL ON FUNCTION accounting.admin_account_audit_guard() FROM PUBLIC;
