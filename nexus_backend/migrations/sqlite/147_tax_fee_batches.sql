CREATE TABLE IF NOT EXISTS tax_fee_batches (
  id TEXT PRIMARY KEY, firm_id TEXT NOT NULL, company_id TEXT NOT NULL, book_id TEXT NOT NULL REFERENCES books(id),
  period TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','reviewed','invalidated')),
  version INTEGER NOT NULL DEFAULT 1 CHECK(version>0), draft_id TEXT REFERENCES drafts(id),
  policy_json TEXT NOT NULL, policy_fingerprint TEXT NOT NULL, source_fingerprint TEXT NOT NULL,
  batch_fingerprint TEXT NOT NULL, total_cents INTEGER NOT NULL CHECK(total_cents>=0),
  row_count INTEGER NOT NULL, skipped_count INTEGER NOT NULL DEFAULT 0,
  created_by TEXT NOT NULL REFERENCES users(id), created_at TEXT NOT NULL,
  reviewed_by TEXT REFERENCES users(id), reviewed_at TEXT, UNIQUE(book_id,batch_fingerprint)
);
CREATE TABLE IF NOT EXISTS tax_fee_batch_rows (
  id TEXT PRIMARY KEY, batch_id TEXT NOT NULL REFERENCES tax_fee_batches(id) ON DELETE CASCADE,
  source_file_id TEXT NOT NULL, source_row_number INTEGER NOT NULL, external_id TEXT NOT NULL,
  driver_id TEXT, vehicle_id TEXT, gross_cents INTEGER NOT NULL, discount_cents INTEGER NOT NULL DEFAULT 0,
  adjustment_cents INTEGER NOT NULL DEFAULT 0, net_cents INTEGER NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('adopted','zero','rejected')), reason TEXT NOT NULL DEFAULT '',
  line_ordinal INTEGER, UNIQUE(batch_id,source_file_id,source_row_number)
);
CREATE TABLE IF NOT EXISTS draft_line_sources (
  draft_id TEXT NOT NULL, book_id TEXT NOT NULL, ordinal INTEGER NOT NULL,
  source_file_id TEXT NOT NULL, source_row_number INTEGER NOT NULL, driver_id TEXT, vehicle_id TEXT,
  role TEXT NOT NULL CHECK(role IN ('receivable','revenue')), amount_cents INTEGER NOT NULL,
  PRIMARY KEY(draft_id,ordinal), FOREIGN KEY(draft_id,book_id) REFERENCES drafts(id,book_id)
);
