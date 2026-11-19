CREATE TABLE IF NOT EXISTS bridge_return_imports (
  id TEXT PRIMARY KEY,
  artifact_id TEXT NOT NULL REFERENCES bridge_export_artifacts(id),
  book_id TEXT NOT NULL REFERENCES books(id),
  firm_id TEXT NOT NULL,
  company_id TEXT NOT NULL,
  batch_code TEXT NOT NULL,
  content_sha256 TEXT NOT NULL,
  content BLOB NOT NULL,
  result_status TEXT NOT NULL CHECK(result_status IN ('accepted','partial')),
  received INTEGER NOT NULL CHECK(received IN (0,1)),
  posted INTEGER NOT NULL CHECK(posted IN (0,1)),
  row_count INTEGER NOT NULL CHECK(row_count>0),
  accepted_count INTEGER NOT NULL,
  rejected_count INTEGER NOT NULL,
  duplicate_count INTEGER NOT NULL,
  unknown_count INTEGER NOT NULL,
  missing_count INTEGER NOT NULL,
  extra_count INTEGER NOT NULL,
  mismatch_count INTEGER NOT NULL,
  discrepancy_count INTEGER NOT NULL,
  created_by TEXT NOT NULL REFERENCES users(id),
  created_at TEXT NOT NULL,
  fingerprint TEXT NOT NULL,
  UNIQUE(artifact_id, batch_code),
  UNIQUE(artifact_id, content_sha256)
);
CREATE TABLE IF NOT EXISTS bridge_return_rows (
  import_id TEXT NOT NULL REFERENCES bridge_return_imports(id) ON DELETE CASCADE,
  voucher_code TEXT NOT NULL,
  external_code TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL,
  reason TEXT NOT NULL DEFAULT '',
  posted_date TEXT,
  expected_cents INTEGER,
  actual_cents INTEGER,
  expected_accounts_json TEXT NOT NULL DEFAULT '[]',
  actual_accounts_json TEXT NOT NULL DEFAULT '[]',
  raw_hash TEXT NOT NULL,
  PRIMARY KEY(import_id, voucher_code)
);
CREATE INDEX IF NOT EXISTS idx_bridge_return_imports_artifact
  ON bridge_return_imports(artifact_id, created_at, id);
CREATE INDEX IF NOT EXISTS idx_bridge_return_imports_book
  ON bridge_return_imports(book_id, created_at, id);
CREATE TRIGGER IF NOT EXISTS bridge_return_imports_identity_guard
BEFORE UPDATE ON bridge_return_imports
WHEN NEW.id<>OLD.id OR NEW.artifact_id<>OLD.artifact_id OR NEW.book_id<>OLD.book_id
  OR NEW.content_sha256<>OLD.content_sha256 OR NEW.batch_code<>OLD.batch_code
BEGIN SELECT RAISE(ABORT,'return import identity is immutable'); END;
CREATE TRIGGER IF NOT EXISTS bridge_return_rows_no_update
BEFORE UPDATE ON bridge_return_rows
BEGIN SELECT RAISE(ABORT,'return rows are append-only'); END;
CREATE TRIGGER IF NOT EXISTS bridge_return_rows_no_delete
BEFORE DELETE ON bridge_return_rows
BEGIN SELECT RAISE(ABORT,'return rows are append-only'); END;
CREATE TRIGGER IF NOT EXISTS bridge_return_imports_no_delete
BEFORE DELETE ON bridge_return_imports
BEGIN SELECT RAISE(ABORT,'return imports are append-only'); END;
