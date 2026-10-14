-- No auth identity secrets or administrator email addresses belong in this migration.
ALTER TABLE accounting.companies ADD COLUMN status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','suspended','archived'));
ALTER TABLE accounting.companies ADD COLUMN version INTEGER NOT NULL DEFAULT 1 CHECK(version>0);
UPDATE accounting.companies c SET status='suspended' WHERE EXISTS(SELECT 1 FROM accounting.firm_clients fc WHERE fc.company_id=c.id AND fc.status='suspended') AND NOT EXISTS(SELECT 1 FROM accounting.firm_clients fc WHERE fc.company_id=c.id AND fc.status='active');
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM accounting.firm_clients GROUP BY company_id HAVING count(*)>1) THEN
  RAISE EXCEPTION 'A company is linked to multiple firms; resolve sharing explicitly before admin migration';
 END IF;
END $$;
CREATE UNIQUE INDEX idx_admin_single_servicing_firm ON accounting.firm_clients(company_id);
CREATE INDEX idx_admin_company_status ON accounting.companies(status,name,id);
ALTER TABLE accounting.audit_events ADD COLUMN company_id TEXT REFERENCES accounting.companies(id);
CREATE INDEX idx_admin_audit_scope ON accounting.audit_events(firm_id,company_id,id);
UPDATE accounting.audit_events a SET company_id=b.company_id FROM accounting.books b WHERE a.book_id=b.id;
UPDATE accounting.audit_events a SET company_id=a.details::jsonb->>'company_id' WHERE company_id IS NULL AND EXISTS(SELECT 1 FROM accounting.firm_clients fc WHERE fc.company_id=a.details::jsonb->>'company_id' AND fc.firm_id=a.firm_id);
UPDATE accounting.audit_events a SET company_id=a.object_id WHERE a.company_id IS NULL AND a.action='company.create' AND EXISTS(SELECT 1 FROM accounting.firm_clients fc WHERE fc.company_id=a.object_id AND fc.firm_id=a.firm_id);

-- Administrative visibility does not change book_allowed or business_event_visible.
CREATE FUNCTION accounting.admin_company_manager(target TEXT) RETURNS BOOLEAN LANGUAGE sql STABLE SECURITY DEFINER
 SET search_path=pg_catalog,accounting,pg_temp AS $$
 SELECT accounting.actor_active() AND EXISTS(SELECT 1 FROM accounting.firm_clients fc
 WHERE fc.company_id=target AND fc.firm_id=accounting.actor_firm()
 AND (accounting.firm_manager(fc.firm_id) OR (fc.status='active' AND EXISTS(
  SELECT 1 FROM accounting.company_memberships cm WHERE cm.company_id=target AND cm.user_id=accounting.actor_user() AND cm.role IN ('owner','admin')))))
$$;
CREATE OR REPLACE FUNCTION accounting.company_visible(target TEXT) RETURNS BOOLEAN LANGUAGE sql STABLE SECURITY DEFINER
 SET search_path=pg_catalog,accounting,pg_temp AS $$
 SELECT accounting.actor_active() AND EXISTS(SELECT 1 FROM accounting.firm_clients fc WHERE fc.company_id=target
 AND fc.firm_id=accounting.actor_firm() AND fc.status='active' AND (
  accounting.firm_manager(fc.firm_id)
  OR EXISTS(SELECT 1 FROM accounting.company_memberships cm WHERE cm.company_id=target AND cm.user_id=accounting.actor_user())
  OR (accounting.firm_role(fc.firm_id) IS NOT NULL AND EXISTS(SELECT 1 FROM accounting.books b JOIN accounting.book_access ba ON ba.book_id=b.id AND ba.firm_id=b.firm_id WHERE b.company_id=target AND b.firm_id=fc.firm_id AND ba.user_id=accounting.actor_user()))))
$$;
CREATE POLICY companies_admin_read ON accounting.companies FOR SELECT TO accounting_app USING(accounting.admin_company_manager(id));
CREATE POLICY companies_admin_update ON accounting.companies FOR UPDATE TO accounting_app USING(accounting.admin_company_manager(id)) WITH CHECK(accounting.admin_company_manager(id));
CREATE POLICY audit_admin_read ON accounting.audit_events FOR SELECT TO accounting_app
 USING(firm_id=accounting.actor_firm() AND accounting.admin_company_manager(company_id)
 AND (action LIKE 'company.%' OR action LIKE 'book.member.%' OR action LIKE 'admin.%'));
CREATE FUNCTION accounting.admin_audit_scope_guard() RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER
 SET search_path=pg_catalog,accounting,pg_temp AS $$ BEGIN
 IF NEW.company_id IS NOT NULL AND (NOT EXISTS(SELECT 1 FROM accounting.firm_clients fc WHERE fc.company_id=NEW.company_id AND fc.firm_id=NEW.firm_id)
  OR (NEW.book_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM accounting.books b WHERE b.id=NEW.book_id AND b.company_id=NEW.company_id AND b.firm_id=NEW.firm_id))) THEN
  RAISE EXCEPTION 'Audit company and firm scope disagree' USING ERRCODE='23514'; END IF;
 IF coalesce(current_setting('role',true),'')='accounting_app' AND (NEW.action LIKE 'company.%' OR NEW.action LIKE 'book.member.%')
  AND (NEW.company_id IS NULL OR NOT accounting.admin_company_manager(NEW.company_id)) THEN
  RAISE EXCEPTION 'Administrative company audit requires current management authorization' USING ERRCODE='42501'; END IF;
 RETURN NEW; END $$;
CREATE TRIGGER admin_audit_scope_guard BEFORE INSERT ON accounting.audit_events FOR EACH ROW EXECUTE FUNCTION accounting.admin_audit_scope_guard();

CREATE FUNCTION accounting.admin_company_guard() RETURNS TRIGGER LANGUAGE plpgsql
 SET search_path=pg_catalog,accounting,pg_temp AS $$ BEGIN
 IF TG_OP='DELETE' THEN RAISE EXCEPTION 'Archive organizations; accounting evidence cannot be deleted' USING ERRCODE='23514'; END IF;
 IF NEW.id<>OLD.id OR NEW.version<>OLD.version+1 THEN
  RAISE EXCEPTION 'Company identity and consecutive version are protected' USING ERRCODE='23514';
 END IF;
 IF NEW.status<>OLD.status AND current_user='accounting_app' AND NOT accounting.firm_manager(accounting.actor_firm()) THEN
  RAISE EXCEPTION 'Only firm managers change organization lifecycle' USING ERRCODE='42501';
 END IF;
 RETURN NEW; END $$;
CREATE TRIGGER admin_company_guard BEFORE UPDATE OR DELETE ON accounting.companies FOR EACH ROW EXECUTE FUNCTION accounting.admin_company_guard();
CREATE FUNCTION accounting.admin_company_sync() RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER
 SET search_path=pg_catalog,accounting,pg_temp AS $$ BEGIN
 UPDATE accounting.firm_clients SET status=CASE WHEN NEW.status='active' THEN 'active' ELSE 'suspended' END WHERE company_id=NEW.id;
 RETURN NEW; END $$;
CREATE TRIGGER admin_company_sync AFTER UPDATE OF status ON accounting.companies FOR EACH ROW EXECUTE FUNCTION accounting.admin_company_sync();
CREATE FUNCTION accounting.admin_client_guard() RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER
 SET search_path=pg_catalog,accounting,pg_temp AS $$ BEGIN
 IF TG_OP='DELETE' THEN RAISE EXCEPTION 'Company service history is retained' USING ERRCODE='23514'; END IF;
 IF TG_OP='UPDATE' AND (NEW.firm_id,NEW.company_id) IS DISTINCT FROM (OLD.firm_id,OLD.company_id) THEN
  RAISE EXCEPTION 'Company service identity is immutable' USING ERRCODE='23514'; END IF;
 IF NEW.status='active' AND EXISTS(SELECT 1 FROM accounting.companies WHERE id=NEW.company_id AND status<>'active') THEN
  RAISE EXCEPTION 'Inactive companies cannot receive active service' USING ERRCODE='23514'; END IF;
 RETURN NEW; END $$;
CREATE TRIGGER admin_client_guard BEFORE INSERT OR UPDATE OR DELETE ON accounting.firm_clients FOR EACH ROW EXECUTE FUNCTION accounting.admin_client_guard();

CREATE FUNCTION accounting.admin_membership_guard() RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER
 SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE target_firm TEXT; target_user TEXT; old_role TEXT; new_role TEXT; runtime BOOLEAN;
 BEGIN
 runtime := coalesce(current_setting('role',true),'')='accounting_app';
 IF TG_OP='DELETE' THEN target_firm:=OLD.firm_id; target_user:=OLD.user_id; old_role:=OLD.role;
 ELSE target_firm:=NEW.firm_id; target_user:=NEW.user_id; new_role:=NEW.role;
  IF TG_OP='UPDATE' THEN old_role:=OLD.role;
   IF (NEW.firm_id,NEW.user_id) IS DISTINCT FROM (OLD.firm_id,OLD.user_id) THEN RAISE EXCEPTION 'Membership identity is immutable' USING ERRCODE='23514'; END IF;
  ELSE SELECT role INTO old_role FROM accounting.memberships WHERE firm_id=target_firm AND user_id=target_user; END IF;
 END IF;
 IF old_role='owner' AND (TG_OP='DELETE' OR new_role<>'owner') THEN RAISE EXCEPTION 'Owner membership is protected' USING ERRCODE='23514'; END IF;
 IF runtime AND new_role='owner' AND old_role IS DISTINCT FROM 'owner' AND NOT accounting.first_owner(target_firm) THEN
  RAISE EXCEPTION 'Ownership requires a separate transfer workflow' USING ERRCODE='42501'; END IF;
 IF runtime AND target_user=accounting.actor_user() AND NOT (TG_OP='INSERT' AND old_role IS NULL AND new_role='owner' AND accounting.first_owner(target_firm)) THEN
  RAISE EXCEPTION 'Administrators cannot modify their own firm membership' USING ERRCODE='42501'; END IF;
 IF TG_OP='DELETE' THEN RETURN OLD; ELSE RETURN NEW; END IF;
 END $$;
CREATE TRIGGER admin_membership_guard BEFORE INSERT OR UPDATE OR DELETE ON accounting.memberships FOR EACH ROW EXECUTE FUNCTION accounting.admin_membership_guard();

CREATE FUNCTION accounting.admin_company_membership_guard() RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER
 SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE target_company TEXT; target_user TEXT; old_role TEXT; new_role TEXT; staff BOOLEAN;
 BEGIN
 IF TG_OP='DELETE' THEN target_company:=OLD.company_id; target_user:=OLD.user_id; old_role:=OLD.role;
 ELSE target_company:=NEW.company_id; target_user:=NEW.user_id; new_role:=NEW.role;
  IF TG_OP='UPDATE' THEN old_role:=OLD.role;
   IF (NEW.company_id,NEW.user_id) IS DISTINCT FROM (OLD.company_id,OLD.user_id) THEN RAISE EXCEPTION 'Company membership identity is immutable' USING ERRCODE='23514'; END IF;
  ELSE SELECT role INTO old_role FROM accounting.company_memberships WHERE company_id=target_company AND user_id=target_user; END IF;
 END IF;
 IF coalesce(current_setting('role',true),'')='accounting_app' THEN
  IF target_user=accounting.actor_user() AND (TG_OP='DELETE' OR NOT accounting.firm_manager(accounting.actor_firm())) THEN
   RAISE EXCEPTION 'Cannot alter your own company management identity' USING ERRCODE='42501'; END IF;
  IF NOT accounting.firm_manager(accounting.actor_firm()) AND (
    old_role='owner' OR new_role='owner' OR EXISTS(SELECT 1 FROM accounting.memberships WHERE firm_id=accounting.actor_firm() AND user_id=target_user)) THEN
   RAISE EXCEPTION 'Company administrator cannot manage owners or firm staff' USING ERRCODE='42501'; END IF;
 END IF;
 IF TG_OP='DELETE' THEN RETURN OLD; ELSE RETURN NEW; END IF;
 END $$;
CREATE TRIGGER admin_company_membership_guard BEFORE INSERT OR UPDATE OR DELETE ON accounting.company_memberships FOR EACH ROW EXECUTE FUNCTION accounting.admin_company_membership_guard();

CREATE FUNCTION accounting.admin_book_grant_blocked(target_firm TEXT,target_user TEXT) RETURNS BOOLEAN
 LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 SELECT NOT accounting.firm_manager(target_firm) AND
 (target_user=accounting.actor_user() OR EXISTS(SELECT 1 FROM accounting.memberships WHERE firm_id=target_firm AND user_id=target_user))
$$;
-- Keep the invoker here: ledger_create_period is an audited SECURITY DEFINER
-- entry point that validates the explicit manager and every current source
-- grant before inheriting selected access into a newly-created period. A raw
-- accounting_app INSERT/UPDATE cannot impersonate that execution principal.
CREATE FUNCTION accounting.admin_book_grant_guard() RETURNS TRIGGER LANGUAGE plpgsql
 SET search_path=pg_catalog,accounting,pg_temp AS $$ DECLARE target_user TEXT; target_firm TEXT; BEGIN
 IF TG_OP='DELETE' THEN target_user:=OLD.user_id;target_firm:=OLD.firm_id;
 ELSE target_user:=NEW.user_id;target_firm:=NEW.firm_id;
  IF TG_OP='UPDATE' AND (NEW.book_id,NEW.firm_id,NEW.user_id) IS DISTINCT FROM (OLD.book_id,OLD.firm_id,OLD.user_id) THEN RAISE EXCEPTION 'Book grant identity is immutable' USING ERRCODE='23514'; END IF;
 END IF;
 IF current_user='accounting_app' AND accounting.admin_book_grant_blocked(target_firm,target_user) THEN
  RAISE EXCEPTION 'Company administrators cannot change their own or firm staff book permissions' USING ERRCODE='42501'; END IF;
 IF TG_OP='DELETE' THEN RETURN OLD; ELSE RETURN NEW; END IF;
 END $$;
CREATE TRIGGER admin_book_grant_guard BEFORE INSERT OR UPDATE OR DELETE ON accounting.book_access FOR EACH ROW EXECUTE FUNCTION accounting.admin_book_grant_guard();
CREATE FUNCTION accounting.admin_company_member_cleanup() RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER
 SET search_path=pg_catalog,accounting,pg_temp AS $$ BEGIN
 IF TG_OP='DELETE' OR (NEW.role='employee' AND OLD.role<>'employee') THEN
  DELETE FROM accounting.migration_project_access a USING accounting.migration_projects p
  WHERE a.project_id=p.id AND a.user_id=OLD.user_id AND p.company_id=OLD.company_id
   AND NOT EXISTS(SELECT 1 FROM accounting.memberships m WHERE m.firm_id=p.firm_id AND m.user_id=OLD.user_id);
  DELETE FROM accounting.ledger_managers lm USING accounting.ledgers l
  WHERE lm.ledger_id=l.id AND lm.user_id=OLD.user_id AND l.company_id=OLD.company_id
   AND NOT EXISTS(SELECT 1 FROM accounting.memberships m WHERE m.firm_id=l.firm_id AND m.user_id=OLD.user_id);
  DELETE FROM accounting.book_access ba USING accounting.books b
  WHERE ba.book_id=b.id AND ba.firm_id=b.firm_id AND ba.user_id=OLD.user_id AND b.company_id=OLD.company_id
   AND NOT EXISTS(SELECT 1 FROM accounting.memberships m WHERE m.firm_id=b.firm_id AND m.user_id=OLD.user_id);
 END IF;
 RETURN coalesce(NEW,OLD); END $$;
CREATE TRIGGER admin_company_member_cleanup AFTER UPDATE OF role OR DELETE ON accounting.company_memberships FOR EACH ROW EXECUTE FUNCTION accounting.admin_company_member_cleanup();
CREATE FUNCTION accounting.admin_firm_member_cleanup() RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER
 SET search_path=pg_catalog,accounting,pg_temp AS $$ BEGIN
 DELETE FROM accounting.migration_project_access a USING accounting.migration_projects p
 WHERE a.project_id=p.id AND a.user_id=OLD.user_id AND p.firm_id=OLD.firm_id;
 DELETE FROM accounting.ledger_managers lm USING accounting.ledgers l
 WHERE lm.ledger_id=l.id AND lm.user_id=OLD.user_id AND l.firm_id=OLD.firm_id;
 DELETE FROM accounting.book_access WHERE firm_id=OLD.firm_id AND user_id=OLD.user_id;
 RETURN OLD; END $$;
CREATE TRIGGER admin_firm_member_cleanup AFTER DELETE ON accounting.memberships FOR EACH ROW EXECUTE FUNCTION accounting.admin_firm_member_cleanup();

-- Revocation removes dependent grants even if the company manager has no
-- historical-ledger access. Only nested DELETE after the scoped principal has
-- ceased to exist qualifies; normal project grant/change rules remain intact.
CREATE OR REPLACE FUNCTION accounting.migration_access_guard() RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE p accounting.migration_projects; BEGIN
 SELECT * INTO p FROM accounting.migration_projects WHERE id=CASE WHEN TG_OP='DELETE' THEN OLD.project_id ELSE NEW.project_id END;
 IF TG_OP='DELETE' AND pg_trigger_depth()>1 AND p.firm_id=accounting.actor_firm() AND accounting.actor_active()
 AND NOT EXISTS(SELECT 1 FROM accounting.memberships m WHERE m.firm_id=p.firm_id AND m.user_id=OLD.user_id)
 AND (accounting.firm_manager(p.firm_id) OR (accounting.admin_company_manager(p.company_id)
  AND NOT EXISTS(SELECT 1 FROM accounting.company_memberships cm WHERE cm.company_id=p.company_id AND cm.user_id=OLD.user_id AND cm.role IN ('owner','admin','finance')))) THEN
  RETURN OLD;
 END IF;
 IF NOT accounting.migration_manage(p.id) AND NOT (TG_OP='INSERT' AND accounting.migration_bootstrap(p.id,NEW.user_id,NEW.role)) THEN RAISE EXCEPTION 'Migration grant requires project manager' USING ERRCODE='42501'; END IF;
 IF TG_OP<>'DELETE' AND (NEW.granted_by<>accounting.actor_user() OR NOT accounting.migration_book_member(p.book_id,NEW.user_id,NEW.role<>'viewer')
 OR (NEW.role='manager' AND NOT EXISTS(SELECT 1 FROM accounting.ledger_managers WHERE ledger_id=p.ledger_id AND user_id=NEW.user_id))) THEN RAISE EXCEPTION 'Migration grant never creates book or ledger permissions' USING ERRCODE='42501'; END IF;
 IF TG_OP='UPDATE' AND (NEW.project_id,NEW.user_id) IS DISTINCT FROM (OLD.project_id,OLD.user_id) THEN RAISE EXCEPTION 'Migration grant identity is immutable'; END IF;
 RETURN CASE WHEN TG_OP='DELETE' THEN OLD ELSE NEW END; END $$;

CREATE FUNCTION accounting.admin_company_counts(target TEXT) RETURNS TABLE(member_count BIGINT,book_count BIGINT)
 LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 SELECT (SELECT count(*) FROM accounting.company_memberships WHERE company_id=target),
 (SELECT count(*) FROM accounting.books WHERE company_id=target AND firm_id=accounting.actor_firm())
 WHERE accounting.admin_company_manager(target)
$$;
CREATE FUNCTION accounting.admin_company_metadata(target TEXT) RETURNS JSONB LANGUAGE plpgsql STABLE SECURITY DEFINER
 SET search_path=pg_catalog,accounting,pg_temp AS $$ BEGIN
 IF NOT accounting.admin_company_manager(target) THEN RAISE EXCEPTION 'Organization management denied' USING ERRCODE='42501'; END IF;
 RETURN jsonb_build_object(
  'members',coalesce((SELECT jsonb_agg(x ORDER BY x.name,x.user_id) FROM (SELECT cm.user_id,u.name,u.email,u.active,cm.role,cm.department_id FROM accounting.company_memberships cm JOIN accounting.users u ON u.id=cm.user_id WHERE cm.company_id=target) x),'[]'::jsonb),
  'departments',coalesce((SELECT jsonb_agg(x ORDER BY x.name,x.id) FROM (SELECT id,name FROM accounting.departments WHERE company_id=target) x),'[]'::jsonb),
  'books',coalesce((SELECT jsonb_agg(x ORDER BY x.period DESC,x.name,x.id) FROM (SELECT id,name,period FROM accounting.books WHERE company_id=target AND firm_id=accounting.actor_firm()) x),'[]'::jsonb));
 END $$;
REVOKE ALL ON FUNCTION accounting.admin_company_manager(TEXT),accounting.admin_audit_scope_guard(),accounting.admin_company_guard(),accounting.admin_company_sync(),accounting.admin_client_guard(),accounting.admin_membership_guard(),accounting.admin_company_membership_guard(),accounting.admin_book_grant_blocked(TEXT,TEXT),accounting.admin_book_grant_guard(),accounting.admin_company_member_cleanup(),accounting.admin_firm_member_cleanup(),accounting.admin_company_counts(TEXT),accounting.admin_company_metadata(TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION accounting.admin_company_manager(TEXT),accounting.admin_book_grant_blocked(TEXT,TEXT),accounting.admin_company_counts(TEXT),accounting.admin_company_metadata(TEXT) TO accounting_app;
