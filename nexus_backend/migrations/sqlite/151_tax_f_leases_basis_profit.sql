CREATE TABLE IF NOT EXISTS lease_contracts (
  id TEXT PRIMARY KEY, firm_id TEXT NOT NULL, company_id TEXT NOT NULL,
  book_id TEXT NOT NULL REFERENCES books(id),
  customer_name TEXT NOT NULL, customer_external_id TEXT NOT NULL,
  vehicle_external_id TEXT NOT NULL, start_period TEXT NOT NULL, end_period TEXT NOT NULL,
  monthly_cents INTEGER NOT NULL CHECK(monthly_cents>0),
  status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('draft','active','terminated','superseded')),
  version INTEGER NOT NULL DEFAULT 1 CHECK(version>0), approved_version INTEGER NOT NULL DEFAULT 0,
  source_fingerprint TEXT NOT NULL, created_by TEXT NOT NULL REFERENCES users(id), created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_lease_contracts_book ON lease_contracts(book_id,status);
CREATE TABLE IF NOT EXISTS lease_plan_rows (
  id TEXT PRIMARY KEY, contract_id TEXT NOT NULL REFERENCES lease_contracts(id) ON DELETE CASCADE,
  period TEXT NOT NULL, amount_cents INTEGER NOT NULL CHECK(amount_cents>0),
  kind TEXT NOT NULL CHECK(kind IN ('revenue','refund')),
  status TEXT NOT NULL DEFAULT 'planned' CHECK(status IN ('planned','recognized','cancelled')),
  advance_cents INTEGER NOT NULL DEFAULT 0 CHECK(advance_cents>=0),
  draft_id TEXT REFERENCES drafts(id), created_at TEXT NOT NULL,
  UNIQUE(contract_id,period,kind)
);
CREATE TABLE IF NOT EXISTS lease_confirmations (
  id TEXT PRIMARY KEY, contract_id TEXT NOT NULL REFERENCES lease_contracts(id) ON DELETE CASCADE,
  kind TEXT NOT NULL CHECK(kind IN ('business','receipt','invoice')),
  period TEXT NOT NULL, amount_cents INTEGER NOT NULL CHECK(amount_cents>0),
  reference TEXT NOT NULL, confirmed_by TEXT NOT NULL REFERENCES users(id), confirmed_at TEXT NOT NULL,
  UNIQUE(contract_id,kind,period,reference)
);
CREATE TABLE IF NOT EXISTS lease_events (
  id TEXT PRIMARY KEY, contract_id TEXT NOT NULL REFERENCES lease_contracts(id) ON DELETE CASCADE,
  kind TEXT NOT NULL CHECK(kind IN ('recognize','amend','refund','terminate')), period TEXT NOT NULL,
  amount_cents INTEGER NOT NULL DEFAULT 0, reason TEXT NOT NULL,
  created_by TEXT NOT NULL REFERENCES users(id), created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tax_basis_reviews (
  id TEXT PRIMARY KEY, firm_id TEXT NOT NULL, company_id TEXT NOT NULL,
  book_id TEXT NOT NULL REFERENCES books(id), period TEXT NOT NULL, version INTEGER NOT NULL CHECK(version>0),
  status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','reviewed','invalidated')),
  workpaper_id TEXT, policy_json TEXT NOT NULL, policy_fingerprint TEXT NOT NULL,
  base_cents INTEGER NOT NULL CHECK(base_cents>=0), adjustments_json TEXT NOT NULL,
  manual_evidence TEXT NOT NULL DEFAULT '', source_refs_json TEXT NOT NULL DEFAULT '[]',
  tax_cents INTEGER NOT NULL, source_fingerprint TEXT NOT NULL,
  created_by TEXT NOT NULL REFERENCES users(id), created_at TEXT NOT NULL,
  reviewed_by TEXT REFERENCES users(id), reviewed_at TEXT,
  UNIQUE(book_id,period,version)
);
CREATE TABLE IF NOT EXISTS report_line_mappings (
  id TEXT PRIMARY KEY, firm_id TEXT NOT NULL, template_id TEXT NOT NULL, template_version INTEGER NOT NULL,
  version INTEGER NOT NULL CHECK(version>0), line_code TEXT NOT NULL, line_name TEXT NOT NULL,
  line_kind TEXT NOT NULL CHECK(line_kind IN ('revenue','cost','tax','selling','admin','finance','nonoperating','income_tax')),
  account_prefix TEXT NOT NULL, sign INTEGER NOT NULL CHECK(sign IN (1,-1)),
  active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
  created_by TEXT NOT NULL REFERENCES users(id), created_at TEXT NOT NULL,
  UNIQUE(firm_id,template_id,template_version,version,line_code,account_prefix)
);
CREATE INDEX IF NOT EXISTS idx_report_line_mappings_active ON report_line_mappings(firm_id,template_id,template_version,active,version);
