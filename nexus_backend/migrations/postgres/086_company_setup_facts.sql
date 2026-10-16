-- Current company setup facts and effective fleet limits. No account secrets or readiness flags are stored.
CREATE FUNCTION accounting.admin_company_onboarding(target TEXT,month TEXT) RETURNS JSONB
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
      AND (fm.role='reviewer' OR (fm.role IS NULL AND cm.role='finance'))) THEN 1 ELSE 0 END AS can_review
   FROM month_books b JOIN accounting.users u ON u.active=1
   LEFT JOIN accounting.memberships fm ON fm.firm_id=b.firm_id AND fm.user_id=u.id
   LEFT JOIN accounting.company_memberships cm ON cm.company_id=b.company_id AND cm.user_id=u.id
   LEFT JOIN accounting.book_access ba ON ba.book_id=b.id AND ba.firm_id=b.firm_id AND ba.user_id=u.id
 ), submitters AS (
   SELECT p.* FROM people p CROSS JOIN client c
   WHERE c.mode='department' OR p.role IN ('owner','admin','finance')
 )
 SELECT c.mode,c.policy_version,
  (SELECT COUNT(*) FROM people) AS active_members,
  (SELECT COUNT(*) FROM submitters) AS submitters,
  (SELECT COUNT(*) FROM month_books) AS books,
  (SELECT COUNT(*) FROM month_books b WHERE EXISTS(SELECT 1 FROM capabilities x WHERE x.book_id=b.id AND x.can_edit=1)
     AND EXISTS(SELECT 1 FROM capabilities x WHERE x.book_id=b.id AND x.can_review=1)) AS staffed_books,
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
CREATE FUNCTION accounting.processing_capacity(target TEXT) RETURNS JSONB
 LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$ BEGIN
 IF NOT COALESCE((accounting.actor_active() AND accounting.admin_company_manager(target)
 AND accounting.company_visible(target) AND EXISTS(SELECT 1 FROM accounting.companies c
 JOIN accounting.firm_clients fc ON fc.company_id=c.id AND fc.firm_id=accounting.actor_firm()
 WHERE c.id=target AND c.status='active' AND fc.status='active')),false) THEN RAISE EXCEPTION 'Capacity inspection denied' USING ERRCODE='42501'; END IF;
 RETURN (SELECT jsonb_build_object('processing_global_limit',processing_global_limit,
 'provider_global_limit',provider_global_limit,'provider_default_limit',provider_default_limit,
 'company_model_concurrency',1) FROM accounting.processing_runtime_limits WHERE singleton=1);
 END $$;
REVOKE ALL ON FUNCTION accounting.admin_company_onboarding(TEXT,TEXT),accounting.processing_capacity(TEXT) FROM PUBLIC,accounting_worker,accounting_system;
GRANT EXECUTE ON FUNCTION accounting.admin_company_onboarding(TEXT,TEXT),accounting.processing_capacity(TEXT) TO accounting_app;
