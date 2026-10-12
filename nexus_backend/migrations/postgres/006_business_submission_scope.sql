-- Applicants can check that independent approvers exist without listing other
-- employees, company roles or private department grants.
CREATE FUNCTION accounting.business_submission_ready(target_firm TEXT,target_company TEXT,target_department TEXT,target_applicant TEXT) RETURNS BOOLEAN
 LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 SELECT target_applicant=accounting.actor_user() AND accounting.business_member(target_firm,target_company)
 AND EXISTS(SELECT 1 FROM accounting.company_memberships cm WHERE cm.company_id=target_company
  AND cm.user_id=target_applicant AND cm.department_id IS NOT DISTINCT FROM target_department)
 AND (target_department IS NULL OR EXISTS(
  SELECT 1 FROM accounting.department_approvers da
  JOIN accounting.company_memberships cm ON cm.company_id=da.company_id AND cm.user_id=da.user_id
  JOIN accounting.users u ON u.id=da.user_id AND u.active=1
  WHERE da.firm_id=target_firm AND da.company_id=target_company AND da.department_id=target_department AND da.user_id<>target_applicant))
 AND EXISTS(SELECT 1 FROM accounting.company_memberships cm JOIN accounting.users u ON u.id=cm.user_id AND u.active=1
  WHERE cm.company_id=target_company AND cm.role='finance' AND cm.user_id<>target_applicant)
$$;
CREATE FUNCTION accounting.business_applicant_department(target_firm TEXT,target_company TEXT,target_department TEXT) RETURNS BOOLEAN
 LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 SELECT accounting.business_member(target_firm,target_company) AND EXISTS(
  SELECT 1 FROM accounting.company_memberships cm WHERE cm.company_id=target_company AND cm.user_id=accounting.actor_user()
  AND cm.department_id IS NOT DISTINCT FROM target_department)
$$;
REVOKE ALL ON FUNCTION accounting.business_submission_ready(TEXT,TEXT,TEXT,TEXT),accounting.business_applicant_department(TEXT,TEXT,TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION accounting.business_submission_ready(TEXT,TEXT,TEXT,TEXT),accounting.business_applicant_department(TEXT,TEXT,TEXT) TO accounting_app;
DROP POLICY business_events_create ON accounting.business_events;
CREATE POLICY business_events_create ON accounting.business_events FOR INSERT TO accounting_app
 WITH CHECK(accounting.business_applicant_department(firm_id,company_id,department_id)
  AND applicant_id=accounting.actor_user() AND status='draft' AND version=1 AND submitted_version IS NULL);
