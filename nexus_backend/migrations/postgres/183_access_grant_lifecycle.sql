-- compatibility: incremental
CREATE OR REPLACE FUNCTION accounting.access_scope_guard() RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$ BEGIN
 IF NOT accounting.actor_active() OR NEW.firm_id IS DISTINCT FROM accounting.actor_firm() OR (TG_OP='INSERT' AND NEW.created_by IS DISTINCT FROM accounting.actor_user()) THEN RAISE EXCEPTION 'Access grant identity mismatch' USING ERRCODE='42501'; END IF;
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
CREATE OR REPLACE FUNCTION accounting.n03_recipient_eligible(
 target_book TEXT,target_user TEXT,target_firm TEXT,target_company TEXT)
 RETURNS BOOLEAN LANGUAGE sql STABLE SECURITY DEFINER
 SET search_path=pg_catalog,accounting,pg_temp AS $$
 SELECT EXISTS(
 SELECT 1 FROM accounting.books b
 JOIN accounting.companies c ON c.id=b.company_id AND c.status='active'
 JOIN accounting.firm_clients fc ON fc.firm_id=b.firm_id AND fc.company_id=b.company_id AND fc.status='active'
 JOIN accounting.service_plan_versions p ON p.book_id=b.id
  AND p.version=(SELECT MAX(version) FROM accounting.service_plan_versions WHERE book_id=b.id)
 JOIN accounting.users u ON u.id=p.contact_user_id AND u.active=1
 JOIN accounting.company_memberships cm ON cm.company_id=b.company_id AND cm.user_id=u.id
 LEFT JOIN accounting.memberships m ON m.firm_id=b.firm_id AND m.user_id=u.id
 LEFT JOIN accounting.access_all_book_roles ba ON ba.book_id=b.id AND ba.firm_id=b.firm_id AND ba.user_id=u.id
 WHERE b.id=target_book AND b.firm_id=target_firm AND b.company_id=target_company AND u.id=target_user
 AND (m.role IN('owner','admin') OR
  (ba.user_id IS NOT NULL AND (m.role IS NOT NULL OR cm.role IN('owner','admin','finance'))) OR EXISTS(SELECT 1 FROM accounting.delivery_contact_access g WHERE g.book_id=b.id AND g.user_id=u.id AND g.state='active' AND (g.valid_to IS NULL OR g.valid_to::timestamptz>statement_timestamp())))) $$;
DROP TRIGGER IF EXISTS access_company_guard ON accounting.staff_access_assignments;
CREATE TRIGGER access_company_guard BEFORE INSERT OR UPDATE OR DELETE ON accounting.staff_access_assignments FOR EACH ROW EXECUTE FUNCTION accounting.company_write_table_guard();
DROP TRIGGER IF EXISTS access_company_guard ON accounting.delivery_contact_access;
CREATE TRIGGER access_company_guard BEFORE INSERT OR UPDATE OR DELETE ON accounting.delivery_contact_access FOR EACH ROW EXECUTE FUNCTION accounting.company_write_table_guard();
