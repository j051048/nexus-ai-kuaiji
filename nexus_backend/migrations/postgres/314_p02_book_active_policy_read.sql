-- compatibility: incremental
-- P02/PLT02: allow authorized book readers (without ledger_managers elevation)
-- to read only the active accounting policy bound to their authorized book,
-- keeping direct accounting_policies table RLS restricted to ledger managers.
SET LOCAL search_path=accounting,pg_catalog;

CREATE OR REPLACE FUNCTION accounting.book_active_policy(target_book TEXT)
RETURNS TABLE(
  id TEXT,
  firm_id TEXT,
  company_id TEXT,
  ledger_id TEXT,
  standard_kind TEXT,
  taxpayer_kind TEXT,
  formal_book_system TEXT,
  formal_book_system_name TEXT,
  version INTEGER,
  status TEXT,
  effective_from TEXT,
  effective_to TEXT,
  is_synthetic BOOLEAN,
  approved_by TEXT,
  approved_at TIMESTAMPTZ,
  mapping_fingerprint TEXT,
  fee_variance_reasons_json JSONB
)
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path=pg_catalog,accounting,pg_temp AS $$
  SELECT p.id, p.firm_id, p.company_id, p.ledger_id, p.standard_kind,
         p.taxpayer_kind, p.formal_book_system, p.formal_book_system_name,
         p.version, p.status, p.effective_from, p.effective_to,
         p.is_synthetic, p.approved_by, p.approved_at,
         p.mapping_fingerprint, p.fee_variance_reasons_json
  FROM accounting.books b
  JOIN accounting.ledger_periods lp
    ON lp.book_id = b.id AND lp.firm_id = b.firm_id AND lp.company_id = b.company_id
  JOIN accounting.accounting_policies p
    ON p.ledger_id = lp.ledger_id AND p.firm_id = b.firm_id AND p.company_id = b.company_id
  WHERE b.id = target_book
    AND accounting.actor_active()
    AND b.firm_id = accounting.actor_firm()
    AND accounting.book_allowed(b.id)
    AND p.status = 'active'
  ORDER BY p.version DESC
  LIMIT 1
$$;

REVOKE ALL ON FUNCTION accounting.book_active_policy(TEXT)
  FROM PUBLIC, accounting_worker, accounting_system, accounting_projection;
GRANT EXECUTE ON FUNCTION accounting.book_active_policy(TEXT) TO accounting_app;
