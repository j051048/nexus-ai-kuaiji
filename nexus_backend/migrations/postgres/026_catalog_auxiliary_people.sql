-- Accounting person facts outlive account authorization. This dictionary never
-- creates memberships or grants, and exposes no business originals.
CREATE TABLE accounting.catalog_auxiliary_people (
 book_id TEXT NOT NULL,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,
 employee_id TEXT NOT NULL REFERENCES accounting.users(id),display_name TEXT NOT NULL,
 recorded_by TEXT NOT NULL REFERENCES accounting.users(id),recorded_at TEXT NOT NULL,
 PRIMARY KEY(book_id,employee_id),FOREIGN KEY(book_id,firm_id,company_id) REFERENCES accounting.books(id,firm_id,company_id)
);
ALTER TABLE accounting.catalog_auxiliary_people ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.catalog_auxiliary_people FORCE ROW LEVEL SECURITY;
REVOKE ALL ON accounting.catalog_auxiliary_people FROM PUBLIC,accounting_app,accounting_worker;
GRANT SELECT ON accounting.catalog_auxiliary_people TO accounting_app;
CREATE POLICY catalog_people_read ON accounting.catalog_auxiliary_people FOR SELECT TO accounting_app USING(accounting.book_allowed(book_id));
CREATE TRIGGER catalog_people_immutable BEFORE UPDATE OR DELETE ON accounting.catalog_auxiliary_people FOR EACH ROW EXECUTE FUNCTION accounting.template_history_guard();

CREATE OR REPLACE FUNCTION accounting.catalog_aux_valid(target TEXT,dimension TEXT,value TEXT) RETURNS BOOLEAN LANGUAGE sql STABLE SECURITY DEFINER
 SET search_path=pg_catalog,accounting,pg_temp AS $$
 SELECT accounting.book_allowed(target) AND length(value) BETWEEN 1 AND 200 AND EXISTS(
  SELECT 1 FROM accounting.books b WHERE b.id=target AND b.firm_id=accounting.actor_firm() AND CASE dimension
   WHEN 'department' THEN EXISTS(SELECT 1 FROM accounting.departments d WHERE d.id=value AND d.company_id=b.company_id)
   WHEN 'employee' THEN (
    EXISTS(SELECT 1 FROM accounting.catalog_auxiliary_people cp WHERE cp.book_id=b.id AND cp.firm_id=b.firm_id AND cp.company_id=b.company_id AND cp.employee_id=value)
    OR EXISTS(SELECT 1 FROM accounting.company_memberships cm WHERE cm.user_id=value AND cm.company_id=b.company_id)
    OR EXISTS(SELECT 1 FROM accounting.business_mappings m JOIN accounting.business_events e ON e.id=m.event_id AND e.firm_id=m.firm_id AND e.company_id=m.company_id
      WHERE m.book_id=b.id AND m.firm_id=b.firm_id AND m.company_id=b.company_id AND e.applicant_id=value)
   )
   WHEN 'counterparty' THEN true WHEN 'project' THEN true ELSE false END
 )
$$;
CREATE FUNCTION accounting.record_catalog_employee(target TEXT,person TEXT) RETURNS BOOLEAN LANGUAGE plpgsql SECURITY DEFINER
 SET search_path=pg_catalog,accounting,pg_temp AS $$ BEGIN
 IF NOT accounting.book_allowed(target,true) OR NOT accounting.catalog_aux_valid(target,'employee',person) OR NOT EXISTS(
  SELECT 1 FROM accounting.books b JOIN accounting.periods p ON p.book_id=b.id AND p.period=b.period WHERE b.id=target AND p.status='open'
 ) THEN RETURN false; END IF;
 INSERT INTO accounting.catalog_auxiliary_people(book_id,firm_id,company_id,employee_id,display_name,recorded_by,recorded_at)
 SELECT b.id,b.firm_id,b.company_id,u.id,u.name,accounting.actor_user(),accounting.ocr_utc_now()
 FROM accounting.books b JOIN accounting.users u ON u.id=person WHERE b.id=target
 ON CONFLICT(book_id,employee_id) DO NOTHING;
 RETURN EXISTS(SELECT 1 FROM accounting.catalog_auxiliary_people WHERE book_id=target AND employee_id=person);
 END
$$;
REVOKE ALL ON FUNCTION accounting.record_catalog_employee(TEXT,TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION accounting.record_catalog_employee(TEXT,TEXT) TO accounting_app;
