CREATE TABLE IF NOT EXISTS accounting.tax_cost_sources (
  id TEXT PRIMARY KEY, firm_id TEXT NOT NULL, company_id TEXT NOT NULL,
  book_id TEXT NOT NULL REFERENCES accounting.books(id), kind TEXT NOT NULL CHECK(kind IN ('repair','insurance','oa')),
  source_key TEXT NOT NULL, source_version INTEGER NOT NULL CHECK(source_version>0),
  period TEXT NOT NULL, subject_label TEXT NOT NULL DEFAULT '',
  original_ref TEXT NOT NULL, sha256 TEXT NOT NULL, amount_cents BIGINT NOT NULL CHECK(amount_cents>0),
  payload_json TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','superseded')),
  fingerprint TEXT NOT NULL, created_by TEXT NOT NULL REFERENCES accounting.users(id), created_at TIMESTAMPTZ NOT NULL,
  UNIQUE(book_id,kind,source_key,source_version)
);
CREATE INDEX IF NOT EXISTS idx_tax_cost_sources_active ON accounting.tax_cost_sources(book_id,kind,period,status);
CREATE TABLE IF NOT EXISTS accounting.tax_cost_batches (
  id TEXT PRIMARY KEY, firm_id TEXT NOT NULL, company_id TEXT NOT NULL,
  book_id TEXT NOT NULL REFERENCES accounting.books(id), kind TEXT NOT NULL CHECK(kind IN ('repair','insurance','oa')),
  period TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','reviewed','invalidated')),
  version INTEGER NOT NULL DEFAULT 1 CHECK(version>0), draft_id TEXT REFERENCES accounting.drafts(id),
  policy_json TEXT NOT NULL, policy_fingerprint TEXT NOT NULL, source_fingerprint TEXT NOT NULL,
  batch_fingerprint TEXT NOT NULL, amount_cents BIGINT NOT NULL CHECK(amount_cents>0),
  row_count INTEGER NOT NULL CHECK(row_count>0), created_by TEXT NOT NULL REFERENCES accounting.users(id),
  created_at TIMESTAMPTZ NOT NULL, reviewed_by TEXT REFERENCES accounting.users(id), reviewed_at TIMESTAMPTZ,
  UNIQUE(book_id,batch_fingerprint)
);
CREATE TABLE IF NOT EXISTS accounting.tax_cost_batch_rows (
  id TEXT PRIMARY KEY, batch_id TEXT NOT NULL REFERENCES accounting.tax_cost_batches(id) ON DELETE CASCADE,
  source_id TEXT NOT NULL REFERENCES accounting.tax_cost_sources(id), row_number INTEGER NOT NULL,
  amount_cents BIGINT NOT NULL CHECK(amount_cents>0), status TEXT NOT NULL DEFAULT 'adopted'
    CHECK(status IN ('adopted','rejected')),
  reason TEXT NOT NULL DEFAULT '', line_ordinal INTEGER,
  UNIQUE(batch_id,source_id)
);
ALTER TABLE accounting.tax_cost_sources ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.tax_cost_sources FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tax_cost_sources_tenant ON accounting.tax_cost_sources;
CREATE POLICY tax_cost_sources_tenant ON accounting.tax_cost_sources FOR ALL TO accounting_app
  USING (accounting.company_visible(company_id)) WITH CHECK (accounting.company_visible(company_id));
ALTER TABLE accounting.tax_cost_batches ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.tax_cost_batches FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tax_cost_batches_tenant ON accounting.tax_cost_batches;
CREATE POLICY tax_cost_batches_tenant ON accounting.tax_cost_batches FOR ALL TO accounting_app
  USING (accounting.company_visible(company_id)) WITH CHECK (accounting.company_visible(company_id));
ALTER TABLE accounting.tax_cost_batch_rows ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.tax_cost_batch_rows FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tax_cost_batch_rows_tenant ON accounting.tax_cost_batch_rows;
CREATE POLICY tax_cost_batch_rows_tenant ON accounting.tax_cost_batch_rows FOR ALL TO accounting_app
  USING (EXISTS (SELECT 1 FROM accounting.tax_cost_batches b WHERE b.id=batch_id AND accounting.company_visible(b.company_id)))
  WITH CHECK (EXISTS (SELECT 1 FROM accounting.tax_cost_batches b WHERE b.id=batch_id AND accounting.company_visible(b.company_id)));
REVOKE ALL ON accounting.tax_cost_sources, accounting.tax_cost_batches, accounting.tax_cost_batch_rows FROM PUBLIC;
GRANT SELECT, INSERT, UPDATE ON accounting.tax_cost_sources, accounting.tax_cost_batches TO accounting_app;
GRANT SELECT, INSERT ON accounting.tax_cost_batch_rows TO accounting_app;
