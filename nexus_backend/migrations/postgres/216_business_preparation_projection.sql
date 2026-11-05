-- compatibility: incremental
-- Employees may learn whether their business month is prepared, never book IDs,
-- balances, ledger permissions, or another company's setup. This is advisory only.
CREATE OR REPLACE FUNCTION accounting.business_preparation_status(target_company TEXT,target_period TEXT)
 RETURNS TEXT LANGUAGE plpgsql STABLE SECURITY DEFINER
 SET search_path=pg_catalog,accounting,pg_temp AS $$
DECLARE role_name TEXT; company_role_name TEXT;
BEGIN
 IF NOT accounting.actor_active() OR NOT accounting.company_visible(target_company)
 OR NOT EXISTS(SELECT 1 FROM accounting.companies c WHERE c.id=target_company AND c.status='active') THEN
  RAISE EXCEPTION 'Company unavailable' USING ERRCODE='42501';
 END IF;
 IF target_period IS NULL OR target_period !~ '^20[0-9]{2}-(0[1-9]|1[0-2])$' THEN
  RAISE EXCEPTION 'Invalid business period' USING ERRCODE='22023';
 END IF;
 IF NOT EXISTS(SELECT 1 FROM accounting.books b WHERE b.firm_id=accounting.actor_firm()
 AND b.company_id=target_company AND b.period=target_period) THEN RETURN 'period_missing'; END IF;
 IF NOT EXISTS(SELECT 1 FROM accounting.books b JOIN accounting.periods p ON p.book_id=b.id AND p.period=b.period
 WHERE b.firm_id=accounting.actor_firm() AND b.company_id=target_company AND b.period=target_period AND p.status='open')
 THEN RETURN 'period_locked'; END IF;
 role_name := accounting.firm_role(accounting.actor_firm());
 SELECT cm.role INTO company_role_name FROM accounting.company_memberships cm
 WHERE cm.company_id=target_company AND cm.user_id=accounting.actor_user();
 IF (role_name IS NOT NULL OR company_role_name IN('owner','admin','finance')) AND NOT EXISTS(
  SELECT 1 FROM accounting.books b JOIN accounting.periods p ON p.book_id=b.id AND p.period=b.period
  WHERE b.firm_id=accounting.actor_firm() AND b.company_id=target_company AND b.period=target_period
  AND p.status='open' AND accounting.book_allowed(b.id,true)) THEN RETURN 'assignment_required'; END IF;
 RETURN 'ready';
END $$;
REVOKE ALL ON FUNCTION accounting.business_preparation_status(TEXT,TEXT) FROM PUBLIC,accounting_worker;
GRANT EXECUTE ON FUNCTION accounting.business_preparation_status(TEXT,TEXT) TO accounting_app;
