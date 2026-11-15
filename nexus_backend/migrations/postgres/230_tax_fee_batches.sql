CREATE TABLE IF NOT EXISTS accounting.tax_fee_batches (
  id TEXT PRIMARY KEY, firm_id TEXT NOT NULL, company_id TEXT NOT NULL, book_id TEXT NOT NULL REFERENCES accounting.books(id),
  period TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','reviewed','invalidated')),
  version INTEGER NOT NULL DEFAULT 1 CHECK(version>0), draft_id TEXT REFERENCES accounting.drafts(id),
  policy_json TEXT NOT NULL, policy_fingerprint TEXT NOT NULL, source_fingerprint TEXT NOT NULL,
  batch_fingerprint TEXT NOT NULL, total_cents BIGINT NOT NULL CHECK(total_cents>=0),
  row_count INTEGER NOT NULL, skipped_count INTEGER NOT NULL DEFAULT 0,
  created_by TEXT NOT NULL REFERENCES accounting.users(id), created_at TIMESTAMPTZ NOT NULL,
  reviewed_by TEXT REFERENCES accounting.users(id), reviewed_at TIMESTAMPTZ, UNIQUE(book_id,batch_fingerprint)
);
CREATE TABLE IF NOT EXISTS accounting.tax_fee_batch_rows (
  id TEXT PRIMARY KEY, batch_id TEXT NOT NULL REFERENCES accounting.tax_fee_batches(id) ON DELETE CASCADE,
  source_file_id TEXT NOT NULL, source_row_number INTEGER NOT NULL, external_id TEXT NOT NULL,
  driver_id TEXT, vehicle_id TEXT, gross_cents BIGINT NOT NULL, discount_cents BIGINT NOT NULL DEFAULT 0,
  adjustment_cents BIGINT NOT NULL DEFAULT 0, net_cents BIGINT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('adopted','zero','rejected')), reason TEXT NOT NULL DEFAULT '',
  line_ordinal INTEGER, UNIQUE(batch_id,source_file_id,source_row_number)
);
CREATE TABLE IF NOT EXISTS accounting.draft_line_sources (
  draft_id TEXT NOT NULL, book_id TEXT NOT NULL, ordinal INTEGER NOT NULL,
  source_file_id TEXT NOT NULL, source_row_number INTEGER NOT NULL, driver_id TEXT, vehicle_id TEXT,
  role TEXT NOT NULL CHECK(role IN ('receivable','revenue')), amount_cents BIGINT NOT NULL,
  PRIMARY KEY(draft_id,ordinal), FOREIGN KEY(draft_id,book_id) REFERENCES accounting.drafts(id,book_id)
);
ALTER TABLE accounting.tax_fee_batches ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.tax_fee_batches FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tax_fee_batches_tenant ON accounting.tax_fee_batches;
CREATE POLICY tax_fee_batches_tenant ON accounting.tax_fee_batches FOR ALL TO accounting_app
  USING (accounting.company_visible(company_id)) WITH CHECK (accounting.company_visible(company_id));
ALTER TABLE accounting.tax_fee_batch_rows ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.tax_fee_batch_rows FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tax_fee_batch_rows_tenant ON accounting.tax_fee_batch_rows;
CREATE POLICY tax_fee_batch_rows_tenant ON accounting.tax_fee_batch_rows FOR ALL TO accounting_app
  USING (EXISTS (SELECT 1 FROM accounting.tax_fee_batches b WHERE b.id=batch_id AND accounting.company_visible(b.company_id)))
  WITH CHECK (EXISTS (SELECT 1 FROM accounting.tax_fee_batches b WHERE b.id=batch_id AND accounting.company_visible(b.company_id)));
ALTER TABLE accounting.draft_line_sources ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.draft_line_sources FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS draft_line_sources_tenant ON accounting.draft_line_sources;
CREATE POLICY draft_line_sources_tenant ON accounting.draft_line_sources FOR ALL TO accounting_app
  USING (EXISTS (SELECT 1 FROM accounting.drafts d WHERE d.id=draft_id AND d.book_id=book_id AND accounting.company_visible((SELECT b.company_id FROM accounting.books b WHERE b.id=d.book_id))))
  WITH CHECK (EXISTS (SELECT 1 FROM accounting.drafts d WHERE d.id=draft_id AND d.book_id=book_id AND accounting.company_visible((SELECT b.company_id FROM accounting.books b WHERE b.id=d.book_id))));
REVOKE ALL ON accounting.tax_fee_batches, accounting.tax_fee_batch_rows, accounting.draft_line_sources FROM PUBLIC;
GRANT SELECT, INSERT, UPDATE ON accounting.tax_fee_batches TO accounting_app;
GRANT SELECT, INSERT ON accounting.tax_fee_batch_rows, accounting.draft_line_sources TO accounting_app;
