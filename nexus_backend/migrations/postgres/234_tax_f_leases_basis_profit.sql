CREATE TABLE IF NOT EXISTS accounting.lease_contracts (
  id TEXT PRIMARY KEY, firm_id TEXT NOT NULL, company_id TEXT NOT NULL,
  book_id TEXT NOT NULL REFERENCES accounting.books(id),
  customer_name TEXT NOT NULL, customer_external_id TEXT NOT NULL,
  vehicle_external_id TEXT NOT NULL, start_period TEXT NOT NULL, end_period TEXT NOT NULL,
  monthly_cents BIGINT NOT NULL CHECK(monthly_cents>0),
  status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('draft','active','terminated','superseded')),
  version INTEGER NOT NULL DEFAULT 1 CHECK(version>0), approved_version INTEGER NOT NULL DEFAULT 0,
  source_fingerprint TEXT NOT NULL, created_by TEXT NOT NULL REFERENCES accounting.users(id), created_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_lease_contracts_book ON accounting.lease_contracts(book_id,status);
CREATE TABLE IF NOT EXISTS accounting.lease_plan_rows (
  id TEXT PRIMARY KEY, contract_id TEXT NOT NULL REFERENCES accounting.lease_contracts(id) ON DELETE CASCADE,
  period TEXT NOT NULL, amount_cents BIGINT NOT NULL CHECK(amount_cents>0),
  kind TEXT NOT NULL CHECK(kind IN ('revenue','refund')),
  status TEXT NOT NULL DEFAULT 'planned' CHECK(status IN ('planned','recognized','cancelled')),
  advance_cents BIGINT NOT NULL DEFAULT 0 CHECK(advance_cents>=0),
  draft_id TEXT REFERENCES accounting.drafts(id), created_at TIMESTAMPTZ NOT NULL,
  UNIQUE(contract_id,period,kind)
);
CREATE TABLE IF NOT EXISTS accounting.lease_confirmations (
  id TEXT PRIMARY KEY, contract_id TEXT NOT NULL REFERENCES accounting.lease_contracts(id) ON DELETE CASCADE,
  kind TEXT NOT NULL CHECK(kind IN ('business','receipt','invoice')),
  period TEXT NOT NULL, amount_cents BIGINT NOT NULL CHECK(amount_cents>0),
  reference TEXT NOT NULL, confirmed_by TEXT NOT NULL REFERENCES accounting.users(id), confirmed_at TIMESTAMPTZ NOT NULL,
  UNIQUE(contract_id,kind,period,reference)
);
CREATE TABLE IF NOT EXISTS accounting.lease_events (
  id TEXT PRIMARY KEY, contract_id TEXT NOT NULL REFERENCES accounting.lease_contracts(id) ON DELETE CASCADE,
  kind TEXT NOT NULL CHECK(kind IN ('recognize','amend','refund','terminate')), period TEXT NOT NULL,
  amount_cents BIGINT NOT NULL DEFAULT 0, reason TEXT NOT NULL,
  created_by TEXT NOT NULL REFERENCES accounting.users(id), created_at TIMESTAMPTZ NOT NULL
);
CREATE TABLE IF NOT EXISTS accounting.tax_basis_reviews (
  id TEXT PRIMARY KEY, firm_id TEXT NOT NULL, company_id TEXT NOT NULL,
  book_id TEXT NOT NULL REFERENCES accounting.books(id), period TEXT NOT NULL, version INTEGER NOT NULL CHECK(version>0),
  status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','reviewed','invalidated')),
  workpaper_id TEXT, policy_json TEXT NOT NULL, policy_fingerprint TEXT NOT NULL,
  base_cents BIGINT NOT NULL CHECK(base_cents>=0), adjustments_json TEXT NOT NULL,
  manual_evidence TEXT NOT NULL DEFAULT '', source_refs_json TEXT NOT NULL DEFAULT '[]',
  tax_cents BIGINT NOT NULL, source_fingerprint TEXT NOT NULL,
  created_by TEXT NOT NULL REFERENCES accounting.users(id), created_at TIMESTAMPTZ NOT NULL,
  reviewed_by TEXT REFERENCES accounting.users(id), reviewed_at TIMESTAMPTZ,
  UNIQUE(book_id,period,version)
);
CREATE TABLE IF NOT EXISTS accounting.report_line_mappings (
  id TEXT PRIMARY KEY, firm_id TEXT NOT NULL, template_id TEXT NOT NULL, template_version INTEGER NOT NULL,
  version INTEGER NOT NULL CHECK(version>0), line_code TEXT NOT NULL, line_name TEXT NOT NULL,
  line_kind TEXT NOT NULL CHECK(line_kind IN ('revenue','cost','tax','selling','admin','finance','nonoperating','income_tax')),
  account_prefix TEXT NOT NULL, sign INTEGER NOT NULL CHECK(sign IN (1,-1)),
  active BOOLEAN NOT NULL DEFAULT TRUE,
  created_by TEXT NOT NULL REFERENCES accounting.users(id), created_at TIMESTAMPTZ NOT NULL,
  UNIQUE(firm_id,template_id,template_version,version,line_code,account_prefix)
);
CREATE INDEX IF NOT EXISTS idx_report_line_mappings_active ON accounting.report_line_mappings(firm_id,template_id,template_version,active,version);
ALTER TABLE accounting.lease_contracts ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.lease_contracts FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS lease_contracts_tenant ON accounting.lease_contracts;
CREATE POLICY lease_contracts_tenant ON accounting.lease_contracts FOR ALL TO accounting_app
  USING (accounting.company_visible(company_id)) WITH CHECK (accounting.company_visible(company_id));
ALTER TABLE accounting.lease_plan_rows ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.lease_plan_rows FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS lease_plan_rows_tenant ON accounting.lease_plan_rows;
CREATE POLICY lease_plan_rows_tenant ON accounting.lease_plan_rows FOR ALL TO accounting_app
  USING (EXISTS (SELECT 1 FROM accounting.lease_contracts c WHERE c.id=contract_id AND accounting.company_visible(c.company_id)))
  WITH CHECK (EXISTS (SELECT 1 FROM accounting.lease_contracts c WHERE c.id=contract_id AND accounting.company_visible(c.company_id)));
ALTER TABLE accounting.lease_confirmations ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.lease_confirmations FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS lease_confirmations_tenant ON accounting.lease_confirmations;
CREATE POLICY lease_confirmations_tenant ON accounting.lease_confirmations FOR ALL TO accounting_app
  USING (EXISTS (SELECT 1 FROM accounting.lease_contracts c WHERE c.id=contract_id AND accounting.company_visible(c.company_id)))
  WITH CHECK (EXISTS (SELECT 1 FROM accounting.lease_contracts c WHERE c.id=contract_id AND accounting.company_visible(c.company_id)));
ALTER TABLE accounting.lease_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.lease_events FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS lease_events_tenant ON accounting.lease_events;
CREATE POLICY lease_events_tenant ON accounting.lease_events FOR ALL TO accounting_app
  USING (EXISTS (SELECT 1 FROM accounting.lease_contracts c WHERE c.id=contract_id AND accounting.company_visible(c.company_id)))
  WITH CHECK (EXISTS (SELECT 1 FROM accounting.lease_contracts c WHERE c.id=contract_id AND accounting.company_visible(c.company_id)));
ALTER TABLE accounting.tax_basis_reviews ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.tax_basis_reviews FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tax_basis_reviews_tenant ON accounting.tax_basis_reviews;
CREATE POLICY tax_basis_reviews_tenant ON accounting.tax_basis_reviews FOR ALL TO accounting_app
  USING (accounting.company_visible(company_id)) WITH CHECK (accounting.company_visible(company_id));
ALTER TABLE accounting.report_line_mappings ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.report_line_mappings FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS report_line_mappings_tenant ON accounting.report_line_mappings;
CREATE POLICY report_line_mappings_tenant ON accounting.report_line_mappings FOR ALL TO accounting_app
  USING (firm_id=accounting.actor_firm()) WITH CHECK (firm_id=accounting.actor_firm());
REVOKE ALL ON accounting.lease_contracts, accounting.lease_plan_rows, accounting.lease_confirmations, accounting.lease_events, accounting.tax_basis_reviews, accounting.report_line_mappings FROM PUBLIC;
GRANT SELECT, INSERT, UPDATE ON accounting.lease_contracts, accounting.lease_plan_rows, accounting.lease_confirmations, accounting.lease_events, accounting.tax_basis_reviews, accounting.report_line_mappings TO accounting_app;
