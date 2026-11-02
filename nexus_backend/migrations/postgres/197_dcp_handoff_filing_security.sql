-- compatibility: incremental
-- Forward repair of 193/195. Runtime stays non-superuser/NOBYPASSRLS.
ALTER TABLE accounting.handoff_tasks ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.handoff_tasks FORCE ROW LEVEL SECURITY;
ALTER TABLE accounting.tax_filings ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.tax_filings FORCE ROW LEVEL SECURITY;
REVOKE ALL ON accounting.handoff_tasks,accounting.tax_filings FROM PUBLIC,accounting_app,accounting_worker;
GRANT SELECT,INSERT ON accounting.handoff_tasks,accounting.tax_filings TO accounting_app;
GRANT UPDATE(superseded_at,updated_at) ON accounting.handoff_tasks TO accounting_app;
DROP POLICY IF EXISTS dcp_handoff_read ON accounting.handoff_tasks;
CREATE POLICY dcp_handoff_read ON accounting.handoff_tasks FOR SELECT TO accounting_app
 USING(firm_id=accounting.actor_firm() AND accounting.book_allowed(book_id));
DROP POLICY IF EXISTS dcp_handoff_insert ON accounting.handoff_tasks;
CREATE POLICY dcp_handoff_insert ON accounting.handoff_tasks FOR INSERT TO accounting_app
 WITH CHECK(firm_id=accounting.actor_firm() AND accounting.firm_manager(firm_id)
 AND accounting.book_allowed(book_id) AND created_by=accounting.actor_user());
DROP POLICY IF EXISTS dcp_handoff_replace ON accounting.handoff_tasks;
CREATE POLICY dcp_handoff_replace ON accounting.handoff_tasks FOR UPDATE TO accounting_app
 USING(firm_id=accounting.actor_firm() AND accounting.firm_manager(firm_id) AND accounting.book_allowed(book_id))
 WITH CHECK(firm_id=accounting.actor_firm() AND accounting.firm_manager(firm_id) AND accounting.book_allowed(book_id));
DROP POLICY IF EXISTS dcp_filing_read ON accounting.tax_filings;
CREATE POLICY dcp_filing_read ON accounting.tax_filings FOR SELECT TO accounting_app
 USING(firm_id=accounting.actor_firm() AND accounting.book_allowed(book_id));
DROP POLICY IF EXISTS dcp_filing_insert ON accounting.tax_filings;
CREATE POLICY dcp_filing_insert ON accounting.tax_filings FOR INSERT TO accounting_app
 WITH CHECK(firm_id=accounting.actor_firm() AND accounting.book_review_allowed(book_id)
 AND created_by=accounting.actor_user());

CREATE OR REPLACE FUNCTION accounting.dcp_handoff_guard() RETURNS TRIGGER
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
DECLARE b RECORD; w RECORD;
BEGIN
 SELECT * INTO b FROM accounting.books WHERE id=NEW.book_id;
 IF NOT FOUND OR b.firm_id IS DISTINCT FROM NEW.firm_id OR NEW.firm_id IS DISTINCT FROM accounting.actor_firm()
 OR NOT accounting.actor_active() OR NOT accounting.firm_manager(NEW.firm_id)
 OR NOT accounting.book_allowed(NEW.book_id) THEN
  RAISE EXCEPTION 'Current handoff management authority required' USING ERRCODE='42501';
 END IF;
 PERFORM accounting.lock_company_write(b.firm_id,b.company_id);
 IF TG_OP='UPDATE' THEN
  IF (to_jsonb(NEW)-ARRAY['superseded_at','updated_at']) IS DISTINCT FROM (to_jsonb(OLD)-ARRAY['superseded_at','updated_at'])
   OR OLD.superseded_at IS NOT NULL OR NEW.superseded_at IS NULL THEN
   RAISE EXCEPTION 'Handoff history cannot be rewritten' USING ERRCODE='23514';
  END IF;
  RETURN NEW;
 END IF;
 IF NEW.created_by IS DISTINCT FROM accounting.actor_user() THEN
  RAISE EXCEPTION 'Handoff creator must be current actor' USING ERRCODE='42501';
 END IF;
 -- Only the implemented domain adapter may create assignments.
 IF NEW.source_kind<>'tax_workpaper' OR NEW.role<>'reviewer' OR NEW.status<>'assigned'
 OR NEW.superseded_at IS NOT NULL OR length(trim(NEW.reason)) NOT BETWEEN 3 AND 1000 THEN
  RAISE EXCEPTION 'Unsupported handoff contract' USING ERRCODE='23514';
 END IF;
 SELECT * INTO w FROM accounting.tax_workpapers WHERE id=NEW.source_id;
 IF NOT FOUND OR w.book_id<>NEW.book_id OR w.firm_id<>NEW.firm_id OR w.status<>'draft'
 OR w.version<>NEW.source_version OR w.source_digest IS DISTINCT FROM NEW.source_fingerprint THEN
  RAISE EXCEPTION 'Handoff source identity is stale or foreign' USING ERRCODE='23514';
 END IF;
 IF NEW.assignee_id IS NULL OR NEW.assignee_id=w.created_by
 OR NOT EXISTS(SELECT 1 FROM accounting.rr_book_permissions p
  WHERE p.book_id=NEW.book_id AND p.user_id=NEW.assignee_id AND p.can_review) THEN
  RAISE EXCEPTION 'Independent current reviewer required' USING ERRCODE='42501';
 END IF;
 IF EXISTS(SELECT 1 FROM accounting.handoff_tasks t WHERE t.source_kind=NEW.source_kind
  AND t.source_id=NEW.source_id AND t.source_version=NEW.source_version AND t.role=NEW.role
  AND t.superseded_at IS NULL) THEN
  RAISE EXCEPTION 'Active assignment already exists' USING ERRCODE='23505';
 END IF;
 IF NEW.due_on IS NOT NULL THEN
  BEGIN
   IF NEW.due_on !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$' OR to_char(NEW.due_on::date,'YYYY-MM-DD')<>NEW.due_on THEN
    RAISE EXCEPTION 'Invalid due date' USING ERRCODE='23514';
   END IF;
  EXCEPTION WHEN invalid_datetime_format OR datetime_field_overflow THEN
   RAISE EXCEPTION 'Invalid due date' USING ERRCODE='23514';
  END;
 END IF;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION accounting.dcp_handoff_guard() FROM PUBLIC;
DROP TRIGGER IF EXISTS dcp_handoff_guard ON accounting.handoff_tasks;
CREATE TRIGGER dcp_handoff_guard BEFORE INSERT OR UPDATE ON accounting.handoff_tasks
 FOR EACH ROW EXECUTE FUNCTION accounting.dcp_handoff_guard();

CREATE OR REPLACE FUNCTION accounting.dcp_filing_guard() RETURNS TRIGGER
 LANGUAGE plpgsql SET search_path=pg_catalog,accounting,pg_temp AS $$
BEGIN
 PERFORM accounting.lock_company_write(NEW.firm_id,NEW.company_id);
 IF NEW.firm_id IS DISTINCT FROM accounting.actor_firm() OR NEW.created_by IS DISTINCT FROM accounting.actor_user()
 OR NOT accounting.actor_active() OR NOT accounting.book_review_allowed(NEW.book_id) THEN
  RAISE EXCEPTION 'Current filing reviewer and identity required' USING ERRCODE='42501';
 END IF;
 IF NOT EXISTS(SELECT 1 FROM accounting.books b WHERE b.id=NEW.book_id
  AND b.company_id=NEW.company_id AND b.firm_id=NEW.firm_id AND b.period=NEW.period) THEN
  RAISE EXCEPTION 'Filing scope does not match the book' USING ERRCODE='23514';
 END IF;
 BEGIN
  IF NEW.occurred_at !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$'
   OR to_char(NEW.occurred_at::date,'YYYY-MM-DD')<>NEW.occurred_at THEN
   RAISE EXCEPTION 'Invalid filing date' USING ERRCODE='23514';
  END IF;
 EXCEPTION WHEN invalid_datetime_format OR datetime_field_overflow THEN
  RAISE EXCEPTION 'Invalid filing date' USING ERRCODE='23514';
 END;
 IF EXISTS(SELECT 1 FROM accounting.tax_filings f WHERE f.book_id=NEW.book_id AND f.period=NEW.period
  AND f.kind=NEW.kind AND f.status=NEW.status AND f.external_ref=NEW.external_ref) THEN
  RAISE EXCEPTION 'Filing receipt already recorded' USING ERRCODE='23505';
 END IF;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION accounting.dcp_filing_guard() FROM PUBLIC;
DROP TRIGGER IF EXISTS dcp_filing_guard ON accounting.tax_filings;
CREATE TRIGGER dcp_filing_guard BEFORE INSERT ON accounting.tax_filings
 FOR EACH ROW EXECUTE FUNCTION accounting.dcp_filing_guard();
