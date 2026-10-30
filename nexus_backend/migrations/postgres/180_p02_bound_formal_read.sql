-- compatibility: incremental
-- A book reader needs its frozen bound opening, not ledger administration or
-- every historical opening. All existing write policies remain unchanged.
CREATE OR REPLACE FUNCTION accounting.formal_bound_opening_visible(target_version TEXT)
 RETURNS BOOLEAN LANGUAGE sql STABLE SECURITY DEFINER
 SET search_path=pg_catalog,accounting,pg_temp AS $$
 SELECT accounting.actor_active() AND EXISTS(
  SELECT 1 FROM accounting.opening_bindings ob
  JOIN accounting.books b ON b.id=ob.book_id AND b.firm_id=ob.firm_id
   AND b.company_id=ob.company_id AND b.period=ob.period
  JOIN accounting.ledger_periods lp ON lp.book_id=b.id AND lp.ledger_id=ob.ledger_id
   AND lp.firm_id=b.firm_id AND lp.company_id=b.company_id AND lp.period=b.period
  JOIN accounting.opening_package_versions v ON v.id=ob.package_version_id
   AND v.status='frozen' AND v.config_fingerprint=ob.snapshot_fingerprint
  JOIN accounting.opening_packages p ON p.id=v.package_id AND p.ledger_id=ob.ledger_id
   AND p.firm_id=b.firm_id AND p.company_id=b.company_id AND p.target_period=b.period
  WHERE ob.package_version_id=target_version AND ob.status='active'
   AND b.firm_id=accounting.actor_firm() AND accounting.book_allowed(b.id)
 )
$$;

CREATE OR REPLACE FUNCTION accounting.formal_account_version_visible(
 target_account TEXT,target_version INTEGER)
 RETURNS BOOLEAN LANGUAGE sql STABLE SECURITY DEFINER
 SET search_path=pg_catalog,accounting,pg_temp AS $$
 SELECT accounting.actor_active() AND EXISTS(
  SELECT 1 FROM accounting.ledger_accounts a
  JOIN accounting.ledger_account_versions av ON av.account_id=a.id AND av.ledger_id=a.ledger_id
   AND av.firm_id=a.firm_id AND av.company_id=a.company_id
  JOIN accounting.ledger_periods lp ON lp.ledger_id=a.ledger_id AND lp.firm_id=a.firm_id
   AND lp.company_id=a.company_id
  JOIN accounting.books b ON b.id=lp.book_id AND b.firm_id=lp.firm_id
   AND b.company_id=lp.company_id AND b.period=lp.period
  JOIN accounting.opening_bindings ob ON ob.book_id=b.id AND ob.ledger_id=a.ledger_id
   AND ob.firm_id=b.firm_id AND ob.company_id=b.company_id AND ob.period=b.period
   AND ob.status='active'
  WHERE a.id=target_account AND (target_version IS NULL OR av.version=target_version)
   AND a.firm_id=accounting.actor_firm() AND accounting.book_allowed(b.id)
   AND accounting.formal_bound_opening_visible(ob.package_version_id)
   AND (
    (av.effective_from<=b.period AND (av.effective_to IS NULL OR av.effective_to>=b.period)
     AND av.version=(SELECT MAX(current.version) FROM accounting.ledger_account_versions current
      WHERE current.account_id=a.id AND current.ledger_id=a.ledger_id
       AND current.firm_id=a.firm_id AND current.company_id=a.company_id
       AND current.effective_from<=b.period
       AND (current.effective_to IS NULL OR current.effective_to>=b.period)))
    OR EXISTS(SELECT 1 FROM accounting.opening_lines ol
     WHERE ol.package_version_id=ob.package_version_id AND ol.account_id=a.id
      AND ol.account_version=av.version)
    OR EXISTS(SELECT 1 FROM accounting.voucher_lines vl JOIN accounting.vouchers v ON v.id=vl.voucher_id
     WHERE v.book_id=b.id AND v.firm_id=b.firm_id AND v.company_id=b.company_id
      AND v.ledger_id=a.ledger_id AND v.period=b.period
      AND vl.account_id=a.id AND vl.account_version=av.version)
   )
 )
$$;
REVOKE ALL ON FUNCTION accounting.formal_bound_opening_visible(TEXT),
 accounting.formal_account_version_visible(TEXT,INTEGER)
 FROM PUBLIC,accounting_system,accounting_worker,accounting_projection;
GRANT EXECUTE ON FUNCTION accounting.formal_bound_opening_visible(TEXT),
 accounting.formal_account_version_visible(TEXT,INTEGER) TO accounting_app;

DROP POLICY IF EXISTS p02_bound_opening_read ON accounting.opening_package_versions;
CREATE POLICY p02_bound_opening_read ON accounting.opening_package_versions
 FOR SELECT TO accounting_app USING(accounting.formal_bound_opening_visible(id));
DROP POLICY IF EXISTS p02_bound_opening_lines_read ON accounting.opening_lines;
CREATE POLICY p02_bound_opening_lines_read ON accounting.opening_lines
 FOR SELECT TO accounting_app USING(accounting.formal_bound_opening_visible(package_version_id));
DROP POLICY IF EXISTS p02_formal_accounts_read ON accounting.ledger_accounts;
CREATE POLICY p02_formal_accounts_read ON accounting.ledger_accounts
 FOR SELECT TO accounting_app USING(accounting.formal_account_version_visible(id,NULL));
DROP POLICY IF EXISTS p02_formal_account_versions_read ON accounting.ledger_account_versions;
CREATE POLICY p02_formal_account_versions_read ON accounting.ledger_account_versions
 FOR SELECT TO accounting_app USING(accounting.formal_account_version_visible(account_id,version));
DROP POLICY IF EXISTS p02_formal_mappings_read ON accounting.ledger_account_mappings;
CREATE POLICY p02_formal_mappings_read ON accounting.ledger_account_mappings
 FOR SELECT TO accounting_app USING(
  firm_id=accounting.actor_firm() AND accounting.book_allowed(book_id)
  AND accounting.formal_account_version_visible(account_id,account_version)
  AND EXISTS(SELECT 1 FROM accounting.opening_bindings ob
   WHERE ob.book_id=ledger_account_mappings.book_id AND ob.ledger_id=ledger_account_mappings.ledger_id
    AND ob.firm_id=ledger_account_mappings.firm_id AND ob.company_id=ledger_account_mappings.company_id
    AND ob.status='active' AND accounting.formal_bound_opening_visible(ob.package_version_id)));
