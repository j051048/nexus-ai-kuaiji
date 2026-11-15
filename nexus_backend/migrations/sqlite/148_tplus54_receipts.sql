CREATE TABLE IF NOT EXISTS bridge_export_artifacts (
  id TEXT PRIMARY KEY,
  outbox_id TEXT NOT NULL REFERENCES bridge_outbox(id),
  ledger_id TEXT NOT NULL,
  book_id TEXT NOT NULL REFERENCES books(id),
  firm_id TEXT NOT NULL,
  company_id TEXT NOT NULL,
  profile TEXT NOT NULL,
  filename TEXT NOT NULL,
  content_sha256 TEXT NOT NULL,
  manifest_fingerprint TEXT NOT NULL,
  package_fingerprint TEXT NOT NULL,
  voucher_codes_json TEXT NOT NULL,
  row_count INTEGER NOT NULL CHECK(row_count>0),
  voucher_count INTEGER NOT NULL CHECK(voucher_count>0),
  created_by TEXT NOT NULL REFERENCES users(id),
  created_at TEXT NOT NULL,
  fingerprint TEXT NOT NULL,
  UNIQUE(outbox_id, content_sha256)
);
CREATE TABLE IF NOT EXISTS bridge_export_receipts (
  id TEXT PRIMARY KEY,
  artifact_id TEXT NOT NULL REFERENCES bridge_export_artifacts(id),
  book_id TEXT NOT NULL REFERENCES books(id),
  firm_id TEXT NOT NULL,
  company_id TEXT NOT NULL,
  receipt_code TEXT NOT NULL,
  content_sha256 TEXT NOT NULL,
  result_status TEXT NOT NULL CHECK(result_status IN ('accepted','partial','rejected','unknown')),
  row_count INTEGER NOT NULL,
  accepted_count INTEGER NOT NULL,
  rejected_count INTEGER NOT NULL,
  duplicate_count INTEGER NOT NULL,
  unknown_count INTEGER NOT NULL,
  external_posting INTEGER NOT NULL DEFAULT 0 CHECK(external_posting=0),
  created_by TEXT NOT NULL REFERENCES users(id),
  created_at TEXT NOT NULL,
  fingerprint TEXT NOT NULL,
  UNIQUE(artifact_id, receipt_code),
  UNIQUE(artifact_id, content_sha256)
);
CREATE TABLE IF NOT EXISTS bridge_export_receipt_rows (
  receipt_id TEXT NOT NULL REFERENCES bridge_export_receipts(id) ON DELETE CASCADE,
  row_number INTEGER NOT NULL,
  voucher_external_code TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('accepted','rejected','duplicate','unknown')),
  reason TEXT NOT NULL DEFAULT '',
  raw_hash TEXT NOT NULL,
  PRIMARY KEY(receipt_id, row_number)
);
CREATE INDEX IF NOT EXISTS idx_bridge_artifacts_outbox ON bridge_export_artifacts(outbox_id, created_at, id);
CREATE INDEX IF NOT EXISTS idx_bridge_receipts_artifact ON bridge_export_receipts(artifact_id, created_at, id);
