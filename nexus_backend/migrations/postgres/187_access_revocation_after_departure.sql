-- compatibility: incremental
-- Removal of target membership must not prevent closing the grant history.
CREATE OR REPLACE FUNCTION accounting.access_scope_guard() RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$ BEGIN
 IF NOT accounting.actor_active() OR NEW.firm_id IS DISTINCT FROM accounting.actor_firm() OR (TG_OP='INSERT' AND NEW.created_by IS DISTINCT FROM accounting.actor_user()) THEN RAISE EXCEPTION 'Access grant identity mismatch' USING ERRCODE='42501'; END IF;
 IF TG_TABLE_NAME='staff_access_assignments' AND TG_OP='UPDATE' AND NEW.state='revoked' THEN
  IF NOT accounting.firm_manager(NEW.firm_id) OR (to_jsonb(NEW)-'state') IS DISTINCT FROM (to_jsonb(OLD)-'state') THEN RAISE EXCEPTION 'Only revoke an immutable grant' USING ERRCODE='42501'; END IF; RETURN NEW;
 END IF;
 IF TG_TABLE_NAME='staff_access_assignments' THEN
  IF NOT accounting.firm_manager(NEW.firm_id) OR NOT EXISTS(SELECT 1 FROM accounting.memberships m JOIN accounting.users u ON u.id=m.user_id AND u.active=1 WHERE m.firm_id=NEW.firm_id AND m.user_id=NEW.user_id AND (NEW.template='auditor' AND m.role IN('viewer','operator','reviewer') OR NEW.template IN('assistant','accountant') AND m.role IN('operator','reviewer') OR NEW.template='reviewer' AND m.role='reviewer'))
   OR (NEW.company_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM accounting.firm_clients fc JOIN accounting.companies c ON c.id=fc.company_id AND c.status='active' WHERE fc.firm_id=NEW.firm_id AND fc.company_id=NEW.company_id AND fc.status='active')) THEN RAISE EXCEPTION 'Unauthorized template or scope' USING ERRCODE='42501'; END IF;
  IF NEW.valid_from IS NOT NULL THEN PERFORM NEW.valid_from::timestamptz; END IF;
 ELSE
  IF NOT accounting.book_review_allowed(NEW.book_id) OR NOT EXISTS(SELECT 1 FROM accounting.company_memberships cm JOIN accounting.users u ON u.id=cm.user_id AND u.active=1 WHERE cm.company_id=NEW.company_id AND cm.user_id=NEW.user_id) THEN RAISE EXCEPTION 'Unauthorized package recipient' USING ERRCODE='42501'; END IF;
 END IF;
 IF TG_OP='UPDATE' AND OLD.state='revoked' AND NEW.state='active' THEN RAISE EXCEPTION 'Revoked grants cannot be reactivated; issue a new grant' USING ERRCODE='42501'; END IF;
 IF NEW.valid_to IS NOT NULL THEN PERFORM NEW.valid_to::timestamptz; END IF;
 IF TG_OP='UPDATE' AND TG_TABLE_NAME='staff_access_assignments' AND (to_jsonb(NEW)-'state') IS DISTINCT FROM (to_jsonb(OLD)-'state') THEN RAISE EXCEPTION 'Grant is immutable; revoke and issue a new grant' USING ERRCODE='42501'; END IF;
 RETURN NEW; END $$;