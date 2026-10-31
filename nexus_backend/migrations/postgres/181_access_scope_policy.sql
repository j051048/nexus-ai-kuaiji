-- compatibility: incremental
SET LOCAL search_path=accounting,pg_catalog;

CREATE TABLE staff_access_assignments(
 id TEXT PRIMARY KEY,firm_id TEXT NOT NULL REFERENCES firms(id),user_id TEXT NOT NULL REFERENCES users(id),
 template TEXT NOT NULL CHECK(template IN('assistant','accountant','reviewer','auditor')),
 company_id TEXT REFERENCES companies(id),valid_from TEXT,valid_to TEXT,
 state TEXT NOT NULL CHECK(state IN('active','revoked')),reason TEXT NOT NULL CHECK(length(trim(reason)) BETWEEN 3 AND 1000),
 created_by TEXT NOT NULL REFERENCES users(id),created_at TEXT NOT NULL,
 CHECK(valid_from IS NULL OR valid_to IS NULL OR valid_from<valid_to)
);
CREATE INDEX idx_staff_scope_user ON staff_access_assignments(firm_id,user_id,state);
CREATE TABLE delivery_contact_access(
 book_id TEXT NOT NULL REFERENCES books(id),user_id TEXT NOT NULL REFERENCES users(id),firm_id TEXT NOT NULL REFERENCES firms(id),company_id TEXT NOT NULL REFERENCES companies(id),
 valid_to TEXT,state TEXT NOT NULL CHECK(state IN('active','revoked')),reason TEXT NOT NULL CHECK(length(trim(reason)) BETWEEN 3 AND 1000),
 created_by TEXT NOT NULL REFERENCES users(id),created_at TEXT NOT NULL,PRIMARY KEY(book_id,user_id),
 FOREIGN KEY(book_id,firm_id,company_id) REFERENCES books(id,firm_id,company_id)
);
CREATE VIEW accounting.access_all_book_roles AS SELECT book_id,firm_id,user_id,CASE MAX(rank) WHEN 3 THEN 'reviewer' WHEN 2 THEN 'editor' ELSE 'viewer' END AS role FROM (
 SELECT a.book_id,a.firm_id,a.user_id,CASE a.role WHEN 'reviewer' THEN 3 WHEN 'editor' THEN 2 ELSE 1 END AS rank FROM book_access a
 UNION ALL
 SELECT b.id,b.firm_id,g.user_id,CASE g.template WHEN 'reviewer' THEN 3 WHEN 'auditor' THEN 1 ELSE 2 END
 FROM staff_access_assignments g JOIN books b ON b.firm_id=g.firm_id AND (g.company_id IS NULL OR g.company_id=b.company_id)
 JOIN companies c ON c.id=b.company_id AND c.status='active'
 JOIN firm_clients fc ON fc.firm_id=b.firm_id AND fc.company_id=b.company_id AND fc.status='active'
 JOIN users u ON u.id=g.user_id AND u.active=1
 JOIN memberships m ON m.user_id=g.user_id AND m.firm_id=g.firm_id
 WHERE g.state='active' AND (g.valid_from IS NULL OR g.valid_from::timestamptz<=statement_timestamp()) AND (g.valid_to IS NULL OR g.valid_to::timestamptz>statement_timestamp())
 AND (g.template='auditor' AND m.role IN('viewer','operator','reviewer') OR g.template IN('assistant','accountant') AND m.role IN('operator','reviewer') OR g.template='reviewer' AND m.role='reviewer')
) grants GROUP BY book_id,firm_id,user_id;
CREATE VIEW accounting.effective_book_access AS SELECT * FROM accounting.access_all_book_roles WHERE firm_id=accounting.actor_firm() AND accounting.actor_active() AND (user_id=accounting.actor_user() OR accounting.book_manager(book_id));
GRANT SELECT ON accounting.effective_book_access TO accounting_app;
REVOKE ALL ON accounting.access_all_book_roles FROM PUBLIC,accounting_app,accounting_worker,accounting_system;
CREATE OR REPLACE FUNCTION accounting.company_visible(target TEXT) RETURNS BOOLEAN LANGUAGE sql STABLE SECURITY DEFINER
 SET search_path=pg_catalog,accounting,pg_temp AS $$
 SELECT accounting.actor_active() AND EXISTS(SELECT 1 FROM accounting.firm_clients fc WHERE fc.company_id=target
 AND fc.firm_id=accounting.actor_firm() AND fc.status='active' AND (
  accounting.firm_manager(fc.firm_id)
  OR EXISTS(SELECT 1 FROM accounting.company_memberships cm WHERE cm.company_id=target AND cm.user_id=accounting.actor_user())
  OR (accounting.firm_role(fc.firm_id) IS NOT NULL AND EXISTS(SELECT 1 FROM accounting.books b JOIN accounting.access_all_book_roles ba ON ba.book_id=b.id AND ba.firm_id=b.firm_id WHERE b.company_id=target AND b.firm_id=fc.firm_id AND ba.user_id=accounting.actor_user()))))
$$;
CREATE OR REPLACE FUNCTION accounting.book_allowed(target TEXT,write_access BOOLEAN DEFAULT false) RETURNS BOOLEAN
 LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 SELECT accounting.actor_active() AND EXISTS(
  SELECT 1 FROM accounting.books b JOIN accounting.firm_clients fc ON fc.firm_id=b.firm_id AND fc.company_id=b.company_id
  WHERE b.id=target AND b.firm_id=accounting.actor_firm() AND fc.status='active' AND (
   accounting.firm_manager(b.firm_id) OR EXISTS(
    SELECT 1 FROM accounting.access_all_book_roles ba WHERE ba.book_id=b.id AND ba.firm_id=b.firm_id
    AND ba.user_id=accounting.actor_user() AND (
     (accounting.firm_role(b.firm_id) IS NOT NULL AND (
      NOT write_access OR (ba.role IN ('editor','reviewer') AND accounting.firm_role(b.firm_id) IN ('operator','reviewer'))))
     OR (accounting.firm_role(b.firm_id) IS NULL AND EXISTS(
      SELECT 1 FROM accounting.company_memberships cm WHERE cm.company_id=b.company_id
      AND cm.user_id=accounting.actor_user() AND cm.role IN ('owner','admin','finance')
      AND (NOT write_access OR ba.role IN ('editor','reviewer'))))
    )
   )
  )
 )
$$;
CREATE OR REPLACE FUNCTION accounting.company_manager_sees_member(target_firm TEXT,target_user TEXT)
 RETURNS BOOLEAN LANGUAGE sql STABLE SECURITY DEFINER
 SET search_path=pg_catalog,accounting,pg_temp AS $$
 SELECT target_firm=accounting.actor_firm() AND accounting.actor_active() AND EXISTS(
  SELECT 1 FROM accounting.firm_clients fc WHERE fc.firm_id=target_firm AND fc.status='active'
  AND accounting.company_manager(fc.company_id) AND (
   EXISTS(SELECT 1 FROM accounting.company_memberships cm WHERE cm.company_id=fc.company_id AND cm.user_id=target_user)
   OR EXISTS(SELECT 1 FROM accounting.books b JOIN accounting.access_all_book_roles ba ON ba.book_id=b.id AND ba.firm_id=b.firm_id
    WHERE b.firm_id=target_firm AND b.company_id=fc.company_id AND ba.user_id=target_user)
  )
 )
$$;
CREATE OR REPLACE FUNCTION accounting.ai_book_reviewer(target_book TEXT) RETURNS BOOLEAN
 LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 SELECT accounting.book_allowed(target_book,true) AND (accounting.firm_manager(accounting.actor_firm()) OR EXISTS(
  SELECT 1 FROM accounting.books b JOIN accounting.access_all_book_roles ba ON ba.book_id=b.id AND ba.firm_id=b.firm_id
  WHERE b.id=target_book AND ba.user_id=accounting.actor_user() AND ba.role='reviewer'
  AND (accounting.firm_role(b.firm_id)='reviewer' OR (accounting.firm_role(b.firm_id) IS NULL AND EXISTS(
   SELECT 1 FROM accounting.company_memberships cm WHERE cm.company_id=b.company_id AND cm.user_id=accounting.actor_user() AND cm.role='finance')))))
$$;
CREATE OR REPLACE FUNCTION accounting.catalog_manage(target TEXT) RETURNS BOOLEAN LANGUAGE sql STABLE SECURITY DEFINER
 SET search_path=pg_catalog,accounting,pg_temp AS $$
 SELECT accounting.book_allowed(target,true) AND EXISTS(
  SELECT 1 FROM accounting.books b WHERE b.id=target AND b.firm_id=accounting.actor_firm() AND (
   accounting.firm_manager(b.firm_id) OR EXISTS(
    SELECT 1 FROM accounting.access_all_book_roles ba WHERE ba.book_id=b.id AND ba.user_id=accounting.actor_user() AND ba.role='reviewer'
    AND (accounting.firm_role(b.firm_id)='reviewer' OR (accounting.firm_role(b.firm_id) IS NULL AND EXISTS(
     SELECT 1 FROM accounting.company_memberships cm WHERE cm.company_id=b.company_id AND cm.user_id=ba.user_id AND cm.role='finance')))
   )
  )
 )
$$;
CREATE OR REPLACE FUNCTION accounting.case_assignee_allowed(target_case TEXT,target_user TEXT) RETURNS BOOLEAN
 LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 WITH target AS(
  SELECT t.*,COALESCE(t.book_id,bm.book_id) AS current_book FROM accounting.case_tasks t
   LEFT JOIN accounting.business_mappings bm ON bm.event_id=t.event_id WHERE t.id=target_case
 ), candidate AS(
  SELECT t.event_id,t.kind,t.current_book,u.id AS user_id,
   (SELECT m.role FROM accounting.memberships m WHERE m.firm_id=t.firm_id AND m.user_id=u.id) AS firm_role,
   (SELECT cm.role FROM accounting.company_memberships cm WHERE cm.company_id=t.company_id AND cm.user_id=u.id) AS company_role,
   (SELECT a.role FROM accounting.access_all_book_roles a WHERE a.book_id=t.current_book AND a.firm_id=t.firm_id AND a.user_id=u.id) AS book_role,
   (SELECT e.applicant_id FROM accounting.business_events e WHERE e.id=t.event_id) AS applicant_id
  FROM target t JOIN accounting.users u ON u.id=target_user AND u.active=1
 )
 SELECT accounting.case_manage(target_case) AND EXISTS(SELECT 1 FROM candidate c WHERE
  (c.event_id IS NOT NULL AND c.company_role='finance' AND c.applicant_id<>c.user_id)
  OR (c.current_book IS NOT NULL AND (c.firm_role IN ('owner','admin') OR
   (c.book_role='reviewer' AND (c.firm_role='reviewer' OR (c.firm_role IS NULL AND c.company_role='finance')))))
  OR (c.event_id IS NULL AND c.kind='exception' AND c.company_role='finance' AND c.book_role IN ('editor','reviewer')
   AND (c.firm_role IS NULL OR c.firm_role IN ('owner','admin','operator','reviewer')))
 )
$$;
CREATE OR REPLACE FUNCTION accounting.ledger_member_current(source TEXT,target_user TEXT,target_role TEXT) RETURNS BOOLEAN LANGUAGE sql STABLE SECURITY DEFINER
 SET search_path=pg_catalog,accounting,pg_temp AS $$
 SELECT accounting.catalog_manage(source) AND EXISTS(SELECT 1 FROM accounting.books b JOIN accounting.access_all_book_roles a ON a.book_id=b.id AND a.firm_id=b.firm_id
 JOIN accounting.users u ON u.id=a.user_id AND u.active=1
 WHERE b.id=source AND b.firm_id=accounting.actor_firm() AND a.user_id=target_user AND a.role=target_role
 AND (EXISTS(SELECT 1 FROM accounting.memberships m WHERE m.user_id=u.id AND m.firm_id=b.firm_id)
 OR EXISTS(SELECT 1 FROM accounting.company_memberships c WHERE c.user_id=u.id AND c.company_id=b.company_id AND c.role IN ('owner','admin','finance'))))
$$;
CREATE OR REPLACE FUNCTION accounting.ledger_member_choices(source TEXT) RETURNS TABLE(user_id TEXT,role TEXT,name TEXT)
 LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 SELECT a.user_id,a.role,u.name FROM accounting.access_all_book_roles a JOIN accounting.users u ON u.id=a.user_id AND u.active=1
 WHERE a.book_id=source AND accounting.catalog_manage(source)
 AND EXISTS(SELECT 1 FROM accounting.ledger_periods p WHERE p.book_id=source AND accounting.ledger_manage(p.ledger_id))
 AND accounting.ledger_member_current(source,a.user_id,a.role) ORDER BY u.name,a.user_id
$$;
CREATE OR REPLACE FUNCTION accounting.migration_book_member(target TEXT,target_user TEXT,review_access BOOLEAN DEFAULT false) RETURNS BOOLEAN
 LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 SELECT EXISTS(SELECT 1 FROM accounting.books b JOIN accounting.firm_clients fc ON fc.firm_id=b.firm_id AND fc.company_id=b.company_id AND fc.status='active'
 JOIN accounting.users u ON u.id=target_user AND u.active=1
 LEFT JOIN accounting.memberships m ON m.firm_id=b.firm_id AND m.user_id=u.id
 LEFT JOIN accounting.company_memberships cm ON cm.company_id=b.company_id AND cm.user_id=u.id
 LEFT JOIN accounting.access_all_book_roles a ON a.book_id=b.id AND a.firm_id=b.firm_id AND a.user_id=u.id
 WHERE b.id=target AND (m.role IN ('owner','admin') OR (a.user_id IS NOT NULL AND
 ((m.role IS NOT NULL AND (NOT review_access OR (m.role='reviewer' AND a.role='reviewer')))
 OR (m.role IS NULL AND cm.role IN ('owner','admin','finance') AND NOT review_access)))))
$$;
CREATE OR REPLACE FUNCTION accounting.service_book_user(target TEXT,person TEXT,capability TEXT) RETURNS BOOLEAN LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 SELECT EXISTS(SELECT 1 FROM accounting.books b JOIN accounting.companies c ON c.id=b.company_id AND c.status='active'
 JOIN accounting.firm_clients fc ON fc.company_id=b.company_id AND fc.firm_id=b.firm_id AND fc.status='active'
 JOIN accounting.users u ON u.id=person AND u.active=1
 LEFT JOIN accounting.memberships m ON m.firm_id=b.firm_id AND m.user_id=person
 LEFT JOIN accounting.company_memberships cm ON cm.company_id=b.company_id AND cm.user_id=person
 LEFT JOIN accounting.access_all_book_roles ba ON ba.book_id=b.id AND ba.firm_id=b.firm_id AND ba.user_id=person
 WHERE b.id=target AND b.firm_id=accounting.actor_firm() AND CASE capability
 WHEN 'contact' THEN cm.user_id IS NOT NULL
 WHEN 'review' THEN m.role IN('owner','admin') OR ba.role='reviewer' AND (m.role='reviewer')
 WHEN 'edit' THEN m.role IN('owner','admin') OR ba.role IN('editor','reviewer') AND (m.role IN('operator','reviewer') OR m.role IS NULL AND cm.role IN('owner','admin','finance'))
 ELSE false END)
$$;
CREATE OR REPLACE FUNCTION accounting.service_plan_candidates(target TEXT) RETURNS TABLE(user_id TEXT,name TEXT,can_lead BOOLEAN,can_review BOOLEAN,can_contact BOOLEAN) LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 SELECT u.id,u.name,accounting.service_book_user(target,u.id,'edit'),accounting.service_book_user(target,u.id,'review'),accounting.service_book_user(target,u.id,'contact')
 FROM accounting.users u WHERE u.active=1 AND accounting.service_book_staff(target)
 AND u.id IN(SELECT m.user_id FROM accounting.memberships m WHERE m.firm_id=accounting.actor_firm()
 UNION SELECT cm.user_id FROM accounting.company_memberships cm JOIN accounting.books b ON b.company_id=cm.company_id WHERE b.id=target AND b.firm_id=accounting.actor_firm()
 UNION SELECT ba.user_id FROM accounting.access_all_book_roles ba WHERE ba.book_id=target AND ba.firm_id=accounting.actor_firm())
 AND (accounting.service_book_user(target,u.id,'edit') OR accounting.service_book_user(target,u.id,'contact')) ORDER BY u.name,u.id
$$;
CREATE OR REPLACE FUNCTION accounting.admin_company_onboarding(target TEXT,month TEXT) RETURNS JSONB
 LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$ BEGIN
 IF month IS NULL OR month !~ '^20[0-9]{2}-(0[1-9]|1[0-2])$' THEN RAISE EXCEPTION 'Invalid setup month' USING ERRCODE='22023'; END IF;
 IF NOT COALESCE((accounting.actor_active() AND accounting.admin_company_manager(target)
 AND accounting.company_visible(target) AND EXISTS(SELECT 1 FROM accounting.companies c
 JOIN accounting.firm_clients fc ON fc.company_id=c.id AND fc.firm_id=accounting.actor_firm()
 WHERE c.id=target AND c.status='active' AND fc.status='active')),false) THEN RAISE EXCEPTION 'Company setup access denied' USING ERRCODE='42501'; END IF;
 RETURN (SELECT to_jsonb(facts) FROM (
WITH args AS (SELECT target AS company_id,accounting.actor_firm() AS firm_id,month AS period),
 client AS (
   SELECT c.id,COALESCE(p.mode,'department') AS mode,COALESCE(p.version,0) AS policy_version
   FROM accounting.companies c JOIN args a ON a.company_id=c.id
   LEFT JOIN accounting.business_submission_policies p ON p.company_id=c.id AND p.firm_id=a.firm_id
 ), people AS (
   SELECT cm.* FROM accounting.company_memberships cm JOIN accounting.users u ON u.id=cm.user_id AND u.active=1
   JOIN args a ON a.company_id=cm.company_id
 ), month_books AS (
   SELECT b.* FROM accounting.books b JOIN args a ON a.company_id=b.company_id AND a.firm_id=b.firm_id AND a.period=b.period
 ), capabilities AS (
   SELECT b.id AS book_id,u.id AS user_id,
    CASE WHEN fm.role IN ('owner','admin') OR (ba.role IN ('editor','reviewer')
      AND (fm.role IN ('operator','reviewer') OR (fm.role IS NULL AND cm.role IN ('owner','admin','finance')))) THEN 1 ELSE 0 END AS can_edit,
    CASE WHEN fm.role IN ('owner','admin') OR (ba.role='reviewer'
      AND (fm.role='reviewer')) THEN 1 ELSE 0 END AS can_review
   FROM month_books b JOIN accounting.users u ON u.active=1
   LEFT JOIN accounting.memberships fm ON fm.firm_id=b.firm_id AND fm.user_id=u.id
   LEFT JOIN accounting.company_memberships cm ON cm.company_id=b.company_id AND cm.user_id=u.id
   LEFT JOIN accounting.access_all_book_roles ba ON ba.book_id=b.id AND ba.firm_id=b.firm_id AND ba.user_id=u.id
 ), submitters AS (
   SELECT p.* FROM people p CROSS JOIN client c
   WHERE c.mode='department' OR p.role IN ('owner','admin','finance')
 )
 SELECT c.mode,c.policy_version,
  (SELECT COUNT(*) FROM people) AS active_members,
  (SELECT COUNT(*) FROM submitters) AS submitters,
  (SELECT COUNT(*) FROM month_books) AS books,
  (SELECT COUNT(*) FROM month_books b JOIN accounting.rr_book_policies rp ON rp.book_id=b.id WHERE EXISTS(SELECT 1 FROM capabilities x JOIN capabilities y ON y.book_id=x.book_id WHERE x.book_id=b.id AND x.can_edit=1 AND y.can_review=1 AND (rp.mode='single_operator' OR x.user_id<>y.user_id))) AS staffed_books,
  (SELECT COUNT(*) FROM month_books b JOIN accounting.rr_book_policies rp ON rp.book_id=b.id WHERE rp.mode='single_operator') AS single_operator_books,
  (SELECT COUNT(*) FROM submitters s WHERE
    (c.mode='direct' AND NOT EXISTS(SELECT 1 FROM capabilities x WHERE x.can_edit=1 AND x.user_id<>s.user_id))
    OR (c.mode='department' AND NOT EXISTS(
       SELECT 1 FROM accounting.departments d JOIN args a ON a.company_id=d.company_id
       WHERE (d.id=s.department_id OR (s.department_id IS NULL AND s.role<>'employee'))
        AND EXISTS(SELECT 1 FROM accounting.department_approvers da JOIN people ap ON ap.user_id=da.user_id
          WHERE da.firm_id=a.firm_id AND da.company_id=a.company_id AND da.department_id=d.id AND da.user_id<>s.user_id)
        AND EXISTS(SELECT 1 FROM people fp WHERE fp.role='finance' AND fp.user_id<>s.user_id)
    ))) AS blocked_submitters,
  (SELECT COUNT(*) FROM accounting.business_events e JOIN args a ON a.company_id=e.company_id AND a.firm_id=e.firm_id
    JOIN accounting.business_mappings bm ON bm.event_id=e.id AND bm.firm_id=e.firm_id AND bm.company_id=e.company_id
    JOIN month_books b ON b.id=bm.book_id
    JOIN accounting.drafts dr ON dr.id=bm.draft_id AND dr.book_id=b.id
    WHERE e.status='handed_off' AND dr.status='exported') AS verified_flow,
  COALESCE((SELECT enabled FROM accounting.ai_company_settings s JOIN args a ON a.firm_id=s.firm_id AND a.company_id=s.company_id),0) AS ai_enabled
 FROM client c
) facts);
 END $$;
CREATE OR REPLACE FUNCTION accounting.book_review_allowed(target TEXT) RETURNS BOOLEAN
 LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 SELECT accounting.actor_active() AND EXISTS(
  SELECT 1 FROM accounting.books b
  JOIN accounting.firm_clients fc ON fc.firm_id=b.firm_id AND fc.company_id=b.company_id
  WHERE b.id=target AND b.firm_id=accounting.actor_firm() AND fc.status='active' AND (
   accounting.firm_manager(b.firm_id) OR EXISTS(
    SELECT 1 FROM accounting.access_all_book_roles ba
    WHERE ba.book_id=b.id AND ba.firm_id=b.firm_id AND ba.user_id=accounting.actor_user()
      AND ba.role='reviewer' AND (
       accounting.firm_role(b.firm_id) IN ('owner','admin','reviewer')
       OR (accounting.firm_role(b.firm_id) IS NULL AND EXISTS(
        SELECT 1 FROM accounting.company_memberships cm
        WHERE cm.company_id=b.company_id AND cm.user_id=accounting.actor_user()
          AND cm.role='finance'))
      )
   )
  )
 )
$$;
CREATE OR REPLACE FUNCTION accounting.monthly_delivery_contact_eligible(target_book TEXT,target_user TEXT)
 RETURNS BOOLEAN LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 SELECT accounting.book_allowed(target_book) AND EXISTS(
 SELECT 1 FROM accounting.books b JOIN accounting.companies c ON c.id=b.company_id AND c.status='active'
 JOIN accounting.firm_clients fc ON fc.firm_id=b.firm_id AND fc.company_id=b.company_id AND fc.status='active'
 JOIN accounting.service_plan_versions p ON p.book_id=b.id AND p.version=(SELECT MAX(version) FROM accounting.service_plan_versions WHERE book_id=b.id)
 JOIN accounting.users u ON u.id=p.contact_user_id AND u.active=1
 JOIN accounting.company_memberships cm ON cm.company_id=b.company_id AND cm.user_id=u.id
 LEFT JOIN accounting.memberships m ON m.firm_id=b.firm_id AND m.user_id=u.id
 LEFT JOIN accounting.access_all_book_roles ba ON ba.book_id=b.id AND ba.firm_id=b.firm_id AND ba.user_id=u.id
 WHERE b.id=target_book AND b.firm_id=accounting.actor_firm() AND u.id=target_user
 AND (m.role IN('owner','admin') OR (ba.user_id IS NOT NULL AND (m.role IS NOT NULL OR cm.role IN('owner','admin','finance'))))) $$;
CREATE OR REPLACE FUNCTION accounting.n03_review_eligible(target_book TEXT,target_user TEXT,target_firm TEXT)
 RETURNS BOOLEAN LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 SELECT EXISTS(SELECT 1 FROM accounting.books b JOIN accounting.companies c ON c.id=b.company_id AND c.status='active'
 JOIN accounting.firm_clients fc ON fc.firm_id=b.firm_id AND fc.company_id=b.company_id AND fc.status='active'
 JOIN accounting.users u ON u.id=target_user AND u.active=1
 LEFT JOIN accounting.memberships m ON m.firm_id=b.firm_id AND m.user_id=u.id
 LEFT JOIN accounting.access_all_book_roles ba ON ba.book_id=b.id AND ba.firm_id=b.firm_id AND ba.user_id=u.id
 WHERE b.id=target_book AND b.firm_id=target_firm AND
 (m.role IN('owner','admin') OR (ba.role='reviewer' AND m.role='reviewer'))) $$;
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
  (ba.user_id IS NOT NULL AND (m.role IS NOT NULL OR cm.role IN('owner','admin','finance'))))) $$;

CREATE OR REPLACE FUNCTION accounting.book_review_allowed(target TEXT) RETURNS BOOLEAN
 LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 SELECT accounting.book_allowed(target,true) AND (accounting.firm_manager(accounting.actor_firm()) OR
 (accounting.firm_role(accounting.actor_firm())='reviewer' AND EXISTS(SELECT 1 FROM accounting.access_all_book_roles a WHERE a.book_id=target AND a.firm_id=accounting.actor_firm() AND a.user_id=accounting.actor_user() AND a.role='reviewer'))) $$;
CREATE OR REPLACE FUNCTION accounting.catalog_manage(target TEXT) RETURNS BOOLEAN LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$ SELECT accounting.book_review_allowed(target) $$;
CREATE OR REPLACE FUNCTION accounting.ai_book_reviewer(target_book TEXT) RETURNS BOOLEAN LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$ SELECT accounting.book_review_allowed(target_book) $$;
CREATE OR REPLACE VIEW accounting.rr_book_permissions AS SELECT b.id AS book_id,u.id AS user_id,
 (m.role IN('owner','admin') OR ba.role IN('editor','reviewer') AND (m.role IN('operator','reviewer') OR m.role IS NULL AND cm.role IN('owner','admin','finance'))) AS can_edit,
 (m.role IN('owner','admin') OR ba.role='reviewer' AND m.role='reviewer') AS can_review
 FROM accounting.books b JOIN accounting.companies c ON c.id=b.company_id AND c.status='active'
 JOIN accounting.firm_clients fc ON fc.firm_id=b.firm_id AND fc.company_id=b.company_id AND fc.status='active'
 JOIN accounting.users u ON u.active=1 LEFT JOIN accounting.memberships m ON m.firm_id=b.firm_id AND m.user_id=u.id
 LEFT JOIN accounting.company_memberships cm ON cm.company_id=b.company_id AND cm.user_id=u.id
 LEFT JOIN accounting.access_all_book_roles ba ON ba.book_id=b.id AND ba.firm_id=b.firm_id AND ba.user_id=u.id;
CREATE OR REPLACE FUNCTION accounting.agency_book_grant_guard() RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$ BEGIN
 IF NEW.role='reviewer' AND NOT EXISTS(SELECT 1 FROM accounting.memberships WHERE firm_id=NEW.firm_id AND user_id=NEW.user_id AND role IN('owner','admin','reviewer')) THEN RAISE EXCEPTION 'Formal reviewer must be authorized firm staff' USING ERRCODE='42501'; END IF; RETURN NEW; END $$;
DROP TRIGGER IF EXISTS agency_book_grant ON accounting.book_access;
CREATE TRIGGER agency_book_grant BEFORE INSERT OR UPDATE ON accounting.book_access FOR EACH ROW EXECUTE FUNCTION accounting.agency_book_grant_guard();
CREATE OR REPLACE FUNCTION accounting.access_scope_guard() RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$ BEGIN
 IF NOT accounting.actor_active() OR NEW.firm_id IS DISTINCT FROM accounting.actor_firm() OR (TG_OP='INSERT' AND NEW.created_by IS DISTINCT FROM accounting.actor_user()) THEN RAISE EXCEPTION 'Access grant identity mismatch' USING ERRCODE='42501'; END IF;
 IF TG_TABLE_NAME='staff_access_assignments' THEN
  IF NOT accounting.firm_manager(NEW.firm_id) OR NOT EXISTS(SELECT 1 FROM accounting.memberships m JOIN accounting.users u ON u.id=m.user_id AND u.active=1 WHERE m.firm_id=NEW.firm_id AND m.user_id=NEW.user_id AND (NEW.template='auditor' AND m.role IN('viewer','operator','reviewer') OR NEW.template IN('assistant','accountant') AND m.role IN('operator','reviewer') OR NEW.template='reviewer' AND m.role='reviewer'))
   OR (NEW.company_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM accounting.firm_clients fc JOIN accounting.companies c ON c.id=fc.company_id AND c.status='active' WHERE fc.firm_id=NEW.firm_id AND fc.company_id=NEW.company_id AND fc.status='active')) THEN RAISE EXCEPTION 'Unauthorized template or scope' USING ERRCODE='42501'; END IF;
  IF NEW.valid_from IS NOT NULL THEN PERFORM NEW.valid_from::timestamptz; END IF;
 ELSE
  IF NOT accounting.book_review_allowed(NEW.book_id) OR NOT EXISTS(SELECT 1 FROM accounting.company_memberships cm JOIN accounting.users u ON u.id=cm.user_id AND u.active=1 WHERE cm.company_id=NEW.company_id AND cm.user_id=NEW.user_id) THEN RAISE EXCEPTION 'Unauthorized package recipient' USING ERRCODE='42501'; END IF;
 END IF;
 IF NEW.valid_to IS NOT NULL THEN PERFORM NEW.valid_to::timestamptz; END IF;
 IF TG_OP='UPDATE' AND TG_TABLE_NAME='staff_access_assignments' AND (to_jsonb(NEW)-'state') IS DISTINCT FROM (to_jsonb(OLD)-'state') THEN RAISE EXCEPTION 'Grant is immutable; revoke and issue a new grant' USING ERRCODE='42501'; END IF;
 RETURN NEW; END $$;
ALTER TABLE accounting.staff_access_assignments ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.staff_access_assignments FORCE ROW LEVEL SECURITY;
GRANT SELECT,INSERT,UPDATE ON accounting.staff_access_assignments TO accounting_app;
DROP POLICY IF EXISTS access_read ON accounting.staff_access_assignments;
CREATE POLICY access_read ON accounting.staff_access_assignments FOR SELECT TO accounting_app USING(firm_id=accounting.actor_firm() AND accounting.actor_active() AND (user_id=accounting.actor_user() OR accounting.firm_manager(firm_id)));
DROP POLICY IF EXISTS access_write ON accounting.staff_access_assignments;
CREATE POLICY access_write ON accounting.staff_access_assignments FOR ALL TO accounting_app USING(firm_id=accounting.actor_firm() AND accounting.firm_manager(firm_id)) WITH CHECK(firm_id=accounting.actor_firm() AND accounting.firm_manager(firm_id));
DROP TRIGGER IF EXISTS access_scope_guard ON accounting.staff_access_assignments;
CREATE TRIGGER access_scope_guard BEFORE INSERT OR UPDATE ON accounting.staff_access_assignments FOR EACH ROW EXECUTE FUNCTION accounting.access_scope_guard();
DROP TRIGGER IF EXISTS access_statement_gate ON accounting.staff_access_assignments;
CREATE TRIGGER access_statement_gate BEFORE INSERT OR UPDATE OR DELETE ON accounting.staff_access_assignments FOR EACH STATEMENT EXECUTE FUNCTION accounting.compatibility_statement_gate();
ALTER TABLE accounting.delivery_contact_access ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.delivery_contact_access FORCE ROW LEVEL SECURITY;
GRANT SELECT,INSERT,UPDATE ON accounting.delivery_contact_access TO accounting_app;
DROP POLICY IF EXISTS access_read ON accounting.delivery_contact_access;
CREATE POLICY access_read ON accounting.delivery_contact_access FOR SELECT TO accounting_app USING(firm_id=accounting.actor_firm() AND accounting.actor_active() AND (user_id=accounting.actor_user() OR accounting.firm_manager(firm_id) OR accounting.book_review_allowed(book_id)));
DROP POLICY IF EXISTS access_write ON accounting.delivery_contact_access;
CREATE POLICY access_write ON accounting.delivery_contact_access FOR ALL TO accounting_app USING(firm_id=accounting.actor_firm() AND accounting.book_review_allowed(book_id)) WITH CHECK(firm_id=accounting.actor_firm() AND accounting.book_review_allowed(book_id));
DROP TRIGGER IF EXISTS access_scope_guard ON accounting.delivery_contact_access;
CREATE TRIGGER access_scope_guard BEFORE INSERT OR UPDATE ON accounting.delivery_contact_access FOR EACH ROW EXECUTE FUNCTION accounting.access_scope_guard();
DROP TRIGGER IF EXISTS access_statement_gate ON accounting.delivery_contact_access;
CREATE TRIGGER access_statement_gate BEFORE INSERT OR UPDATE OR DELETE ON accounting.delivery_contact_access FOR EACH STATEMENT EXECUTE FUNCTION accounting.compatibility_statement_gate();
REVOKE ALL ON FUNCTION accounting.agency_book_grant_guard(),accounting.access_scope_guard() FROM PUBLIC,accounting_app,accounting_worker,accounting_system;
