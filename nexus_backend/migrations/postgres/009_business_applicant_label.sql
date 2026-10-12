-- Approval lists need a human applicant label, without opening the users table
-- or exposing email addresses. The event's existing scope remains mandatory.
CREATE FUNCTION accounting.business_applicant_name(target_event TEXT,target_firm TEXT,target_company TEXT) RETURNS TEXT
 LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 SELECT u.name FROM accounting.business_events e JOIN accounting.users u ON u.id=e.applicant_id
 WHERE e.id=target_event AND e.firm_id=target_firm AND e.company_id=target_company
 AND accounting.business_event_visible(target_event,target_firm,target_company)
$$;
REVOKE ALL ON FUNCTION accounting.business_applicant_name(TEXT,TEXT,TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION accounting.business_applicant_name(TEXT,TEXT,TEXT) TO accounting_app;
