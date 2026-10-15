ALTER TABLE admin_account_requests ADD COLUMN provisioning_mode TEXT NOT NULL DEFAULT 'activation'
 CHECK(provisioning_mode IN('activation','password') AND
 (provisioning_mode='activation' OR (company_id IS NOT NULL AND firm_role IS NULL)));
CREATE TRIGGER admin_account_mode_immutable BEFORE UPDATE ON admin_account_requests
 WHEN NEW.provisioning_mode<>OLD.provisioning_mode
 BEGIN SELECT RAISE(ABORT,'Account provisioning mode is immutable'); END;
CREATE TRIGGER admin_password_department_insert BEFORE INSERT ON admin_account_requests
 WHEN NEW.provisioning_mode='password' AND NEW.company_role='employee' AND NEW.department_id IS NULL
 AND COALESCE((SELECT mode FROM business_submission_policies WHERE firm_id=NEW.firm_id AND company_id=NEW.company_id),'department')='department'
 BEGIN SELECT RAISE(ABORT,'Department approval requires an employee department before password provisioning'); END;
CREATE TRIGGER admin_password_department_update BEFORE UPDATE ON admin_account_requests
 WHEN NEW.status IN('pending','completed') AND NEW.provisioning_mode='password' AND NEW.company_role='employee' AND NEW.department_id IS NULL
 AND COALESCE((SELECT mode FROM business_submission_policies WHERE firm_id=NEW.firm_id AND company_id=NEW.company_id),'department')='department'
 BEGIN SELECT RAISE(ABORT,'Department approval requires an employee department before password provisioning'); END;

DROP TRIGGER admin_account_completion;
CREATE TRIGGER admin_account_completion BEFORE UPDATE ON admin_account_requests WHEN NEW.status='completed' AND (
 (NEW.provisioning_mode='activation' AND (OLD.status<>'awaiting_activation'
  OR NEW.token_digest IS NOT OLD.token_digest OR NEW.expires_at IS NOT OLD.expires_at
  OR NEW.expires_at<=CAST(strftime('%s','now') AS INTEGER)))
 OR (NEW.provisioning_mode='password' AND (OLD.status<>'pending' OR OLD.lease_token=''
  OR OLD.lease_until<=CAST(strftime('%s','now') AS INTEGER) OR NEW.existing_user<>0 OR NEW.token_digest<>'' OR NEW.expires_at<>0))
 OR NOT EXISTS(SELECT 1 FROM users WHERE id=NEW.created_by AND active=1)
 OR NOT EXISTS(SELECT 1 FROM users WHERE id=NEW.completed_by AND active=1 AND lower(email)=NEW.email)
 OR (NEW.company_id IS NULL AND NOT EXISTS(SELECT 1 FROM memberships WHERE firm_id=NEW.firm_id AND user_id=NEW.created_by AND role IN('owner','admin')))
 OR (NEW.company_id IS NOT NULL AND (NOT EXISTS(SELECT 1 FROM companies c JOIN firm_clients fc ON fc.company_id=c.id WHERE c.id=NEW.company_id AND c.status='active' AND fc.firm_id=NEW.firm_id AND fc.status='active')
  OR NOT (EXISTS(SELECT 1 FROM memberships WHERE firm_id=NEW.firm_id AND user_id=NEW.created_by AND role IN('owner','admin'))
   OR (NEW.company_role<>'owner' AND EXISTS(SELECT 1 FROM company_memberships WHERE company_id=NEW.company_id AND user_id=NEW.created_by AND role IN('owner','admin'))))))
 OR (NEW.company_id IS NULL AND NOT EXISTS(SELECT 1 FROM memberships WHERE firm_id=NEW.firm_id AND user_id=NEW.completed_by AND role=NEW.firm_role))
 OR (NEW.company_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM company_memberships WHERE company_id=NEW.company_id AND user_id=NEW.completed_by AND role=NEW.company_role AND department_id IS NEW.department_id)))
BEGIN SELECT RAISE(ABORT,'Account completion requires active originator, matching identity and scoped grant'); END;

DROP TRIGGER admin_account_audit;
CREATE TRIGGER admin_account_audit BEFORE INSERT ON audit_events WHEN NEW.action LIKE 'admin.account.%' AND (
 NOT EXISTS(SELECT 1 FROM admin_account_requests r WHERE r.id=NEW.object_id AND r.firm_id=NEW.firm_id AND r.company_id IS NEW.company_id)
 OR (NEW.action='admin.account.accept' AND NOT EXISTS(SELECT 1 FROM admin_account_requests r WHERE r.id=NEW.object_id AND r.provisioning_mode='activation' AND r.status='completed' AND r.created_by=NEW.user_id AND r.completed_by=json_extract(NEW.details,'$.accepted_by')))
 OR (NEW.action='admin.account.password_created' AND NOT EXISTS(SELECT 1 FROM admin_account_requests r WHERE r.id=NEW.object_id AND r.provisioning_mode='password' AND r.status='completed' AND r.completed_by=json_extract(NEW.details,'$.created_user_id'))))
BEGIN SELECT RAISE(ABORT,'Account audit must match an authorized request'); END;
