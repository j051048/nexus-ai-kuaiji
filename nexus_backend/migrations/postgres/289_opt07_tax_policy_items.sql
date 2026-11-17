-- OPT07-E: policy-driven tax item registry.
CREATE TABLE IF NOT EXISTS accounting.tax_policy_items (
  id TEXT PRIMARY KEY,
  profile_id TEXT NOT NULL REFERENCES accounting.tax_profiles(id),
  firm_id TEXT NOT NULL,
  company_id TEXT NOT NULL,
  kind TEXT NOT NULL CHECK(kind IN ('vat_output','surtax','culture_fee','agriculture_deduction','cit_prepayment','stamp_duty')),
  code TEXT NOT NULL,
  name TEXT NOT NULL,
  basis_kind TEXT NOT NULL,
  rate_bp INTEGER NOT NULL CHECK(rate_bp BETWEEN 0 AND 100000),
  effective_from TEXT NOT NULL,
  effective_to TEXT,
  conditions_json JSONB NOT NULL,
  authority TEXT NOT NULL,
  approved_by TEXT NOT NULL,
  created_by TEXT NOT NULL,
  created_at TEXT NOT NULL,
  fingerprint TEXT NOT NULL,
  UNIQUE(profile_id, kind, code, effective_from)
);
CREATE INDEX IF NOT EXISTS idx_tax_policy_items_scope ON accounting.tax_policy_items(company_id,effective_from,effective_to);
ALTER TABLE accounting.tax_policy_items ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.tax_policy_items FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tax_policy_items_company_visible ON accounting.tax_policy_items;
CREATE POLICY tax_policy_items_company_visible ON accounting.tax_policy_items
  USING (accounting.company_visible(company_id)) WITH CHECK (accounting.company_visible(company_id));
GRANT SELECT, INSERT ON accounting.tax_policy_items TO accounting_app;
