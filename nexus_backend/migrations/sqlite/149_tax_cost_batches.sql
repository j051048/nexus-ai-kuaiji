CREATE TABLE IF NOT EXISTS tax_cost_sources (
  id TEXT PRIMARY KEY, firm_id TEXT NOT NULL, company_id TEXT NOT NULL,
  book_id TEXT NOT NULL REFERENCES books(id), kind TEXT NOT NULL CHECK(kind IN ('repair','insurance','oa')),
  source_key TEXT NOT NULL, source_version INTEGER NOT NULL CHECK(source_version>0),
  period TEXT NOT NULL, subject_label TEXT NOT NULL DEFAULT '',
  original_ref TEXT NOT NULL, sha256 TEXT NOT NULL, amount_cents INTEGER NOT NULL CHECK(amount_cents>0),
  payload_json TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','superseded')),
  fingerprint TEXT NOT NULL, created_by TEXT NOT NULL REFERENCES users(id), created_at TEXT NOT NULL,
  UNIQUE(book_id,kind,source_key,source_version)
);
CREATE INDEX IF NOT EXISTS idx_tax_cost_sources_active ON tax_cost_sources(book_id,kind,period,status);
CREATE TABLE IF NOT EXISTS tax_cost_batches (
  id TEXT PRIMARY KEY, firm_id TEXT NOT NULL, company_id TEXT NOT NULL,
  book_id TEXT NOT NULL REFERENCES books(id), kind TEXT NOT NULL CHECK(kind IN ('repair','insurance','oa')),
  period TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','reviewed','invalidated')),
  version INTEGER NOT NULL DEFAULT 1 CHECK(version>0), draft_id TEXT REFERENCES drafts(id),
  policy_json TEXT NOT NULL, policy_fingerprint TEXT NOT NULL, source_fingerprint TEXT NOT NULL,
  batch_fingerprint TEXT NOT NULL, amount_cents INTEGER NOT NULL CHECK(amount_cents>0),
  row_count INTEGER NOT NULL CHECK(row_count>0), created_by TEXT NOT NULL REFERENCES users(id),
  created_at TEXT NOT NULL, reviewed_by TEXT REFERENCES users(id), reviewed_at TEXT,
  UNIQUE(book_id,batch_fingerprint)
);
CREATE TABLE IF NOT EXISTS tax_cost_batch_rows (
  id TEXT PRIMARY KEY, batch_id TEXT NOT NULL REFERENCES tax_cost_batches(id) ON DELETE CASCADE,
  source_id TEXT NOT NULL REFERENCES tax_cost_sources(id), row_number INTEGER NOT NULL,
  amount_cents INTEGER NOT NULL CHECK(amount_cents>0), status TEXT NOT NULL DEFAULT 'adopted'
    CHECK(status IN ('adopted','rejected')),
  reason TEXT NOT NULL DEFAULT '', line_ordinal INTEGER,
  UNIQUE(batch_id,source_id)
);
