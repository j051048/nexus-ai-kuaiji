-- compatibility: incremental
CREATE TRIGGER staff_assignment_identity BEFORE INSERT ON staff_access_assignments BEGIN
 SELECT CASE WHEN NEW.firm_id IS NOT accounting_actor_firm() OR NEW.created_by IS NOT accounting_actor_user()
 OR NOT EXISTS(SELECT 1 FROM memberships m JOIN users u ON u.id=m.user_id AND u.active=1 WHERE m.firm_id=NEW.firm_id AND m.user_id=accounting_actor_user() AND m.role IN('owner','admin'))
 OR NOT EXISTS(SELECT 1 FROM memberships m JOIN users u ON u.id=m.user_id AND u.active=1 WHERE m.firm_id=NEW.firm_id AND m.user_id=NEW.user_id AND (NEW.template='auditor' AND m.role IN('viewer','operator','reviewer') OR NEW.template IN('assistant','accountant') AND m.role IN('operator','reviewer') OR NEW.template='reviewer' AND m.role='reviewer'))
 OR (NEW.company_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM firm_clients fc JOIN companies c ON c.id=fc.company_id AND c.status='active' WHERE fc.firm_id=NEW.firm_id AND fc.company_id=NEW.company_id AND fc.status='active'))
 OR (NEW.valid_from IS NOT NULL AND julianday(NEW.valid_from) IS NULL) OR (NEW.valid_to IS NOT NULL AND julianday(NEW.valid_to) IS NULL)
 THEN RAISE(ABORT,'Authorized firm template and scope required') END;
END;
CREATE TRIGGER staff_assignment_update BEFORE UPDATE ON staff_access_assignments BEGIN
 SELECT CASE WHEN NEW.id IS NOT OLD.id OR NEW.firm_id IS NOT OLD.firm_id OR NEW.user_id IS NOT OLD.user_id OR NEW.template IS NOT OLD.template OR NEW.company_id IS NOT OLD.company_id OR NEW.valid_from IS NOT OLD.valid_from OR NEW.valid_to IS NOT OLD.valid_to OR NEW.reason IS NOT OLD.reason OR NEW.created_by IS NOT OLD.created_by OR NEW.created_at IS NOT OLD.created_at
 OR OLD.state='revoked' AND NEW.state='active'
 OR NEW.firm_id IS NOT accounting_actor_firm() OR NOT EXISTS(SELECT 1 FROM memberships WHERE firm_id=NEW.firm_id AND user_id=accounting_actor_user() AND role IN('owner','admin'))
 THEN RAISE(ABORT,'Grant is immutable; revoke and issue a new grant') END;
END;
CREATE TRIGGER staff_assignment_no_delete BEFORE DELETE ON staff_access_assignments BEGIN SELECT RAISE(ABORT,'Keep grant history'); END;
CREATE TRIGGER contact_access_identity BEFORE INSERT ON delivery_contact_access BEGIN
 SELECT CASE WHEN NEW.firm_id IS NOT accounting_actor_firm() OR NEW.created_by IS NOT accounting_actor_user()
 OR NOT EXISTS(SELECT 1 FROM service_book_user_permissions WHERE book_id=NEW.book_id AND user_id=accounting_actor_user() AND can_review)
 OR NOT EXISTS(SELECT 1 FROM company_memberships cm JOIN users u ON u.id=cm.user_id AND u.active=1 WHERE cm.company_id=NEW.company_id AND cm.user_id=NEW.user_id)
 OR NEW.valid_to IS NOT NULL AND julianday(NEW.valid_to) IS NULL
 THEN RAISE(ABORT,'Current reviewer and company contact required') END;
END;
CREATE TRIGGER contact_access_update BEFORE UPDATE ON delivery_contact_access BEGIN
 SELECT CASE WHEN NEW.book_id IS NOT OLD.book_id OR NEW.user_id IS NOT OLD.user_id OR NEW.firm_id IS NOT OLD.firm_id OR NEW.company_id IS NOT OLD.company_id
 OR NEW.firm_id IS NOT accounting_actor_firm() OR NEW.created_by IS NOT accounting_actor_user()
 OR NOT EXISTS(SELECT 1 FROM service_book_user_permissions WHERE book_id=NEW.book_id AND user_id=accounting_actor_user() AND can_review)
 OR NOT EXISTS(SELECT 1 FROM company_memberships cm JOIN users u ON u.id=cm.user_id AND u.active=1 WHERE cm.company_id=NEW.company_id AND cm.user_id=NEW.user_id)
 OR NEW.valid_to IS NOT NULL AND julianday(NEW.valid_to) IS NULL
 THEN RAISE(ABORT,'Current reviewer and company contact required') END;
END;
