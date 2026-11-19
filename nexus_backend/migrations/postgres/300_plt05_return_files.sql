CREATE TABLE IF NOT EXISTS accounting.bridge_return_imports (
  id TEXT PRIMARY KEY,
  artifact_id TEXT NOT NULL REFERENCES accounting.bridge_export_artifacts(id),
  book_id TEXT NOT NULL REFERENCES accounting.books(id),
  firm_id TEXT NOT NULL,
  company_id TEXT NOT NULL,
  batch_code TEXT NOT NULL,
  content_sha256 TEXT NOT NULL,
  content BYTEA NOT NULL,
  result_status TEXT NOT NULL CHECK(result_status IN ('accepted','partial')),
  received BOOLEAN NOT NULL,
  posted BOOLEAN NOT NULL,
  row_count INTEGER NOT NULL CHECK(row_count>0),
  accepted_count INTEGER NOT NULL,
  rejected_count INTEGER NOT NULL,
  duplicate_count INTEGER NOT NULL,
  unknown_count INTEGER NOT NULL,
  missing_count INTEGER NOT NULL,
  extra_count INTEGER NOT NULL,
  mismatch_count INTEGER NOT NULL,
  discrepancy_count INTEGER NOT NULL,
  created_by TEXT NOT NULL REFERENCES accounting.users(id),
  created_at TIMESTAMPTZ NOT NULL,
  fingerprint TEXT NOT NULL,
  UNIQUE(artifact_id, batch_code),
  UNIQUE(artifact_id, content_sha256)
);
CREATE TABLE IF NOT EXISTS accounting.bridge_return_rows (
  import_id TEXT NOT NULL REFERENCES accounting.bridge_return_imports(id) ON DELETE CASCADE,
  voucher_code TEXT NOT NULL,
  external_code TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL,
  reason TEXT NOT NULL DEFAULT '',
  posted_date DATE,
  expected_cents BIGINT,
  actual_cents BIGINT,
  expected_accounts_json TEXT NOT NULL DEFAULT '[]',
  actual_accounts_json TEXT NOT NULL DEFAULT '[]',
  raw_hash TEXT NOT NULL,
  PRIMARY KEY(import_id, voucher_code)
);
CREATE INDEX IF NOT EXISTS idx_bridge_return_imports_artifact
  ON accounting.bridge_return_imports(artifact_id, created_at, id);
CREATE INDEX IF NOT EXISTS idx_bridge_return_imports_book
  ON accounting.bridge_return_imports(book_id, created_at, id);
ALTER TABLE accounting.bridge_return_imports ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.bridge_return_imports FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS bridge_return_imports_tenant ON accounting.bridge_return_imports;
CREATE POLICY bridge_return_imports_tenant ON accounting.bridge_return_imports FOR ALL TO accounting_app
  USING (firm_id=accounting.actor_firm() AND accounting.company_visible(company_id))
  WITH CHECK (firm_id=accounting.actor_firm() AND accounting.company_visible(company_id));
ALTER TABLE accounting.bridge_return_rows ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.bridge_return_rows FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS bridge_return_rows_tenant ON accounting.bridge_return_rows;
CREATE POLICY bridge_return_rows_tenant ON accounting.bridge_return_rows FOR ALL TO accounting_app
  USING (EXISTS (SELECT 1 FROM accounting.bridge_return_imports i WHERE i.id=import_id AND i.firm_id=accounting.actor_firm() AND accounting.company_visible(i.company_id)))
  WITH CHECK (EXISTS (SELECT 1 FROM accounting.bridge_return_imports i WHERE i.id=import_id AND i.firm_id=accounting.actor_firm() AND accounting.company_visible(i.company_id)));
REVOKE ALL ON accounting.bridge_return_imports, accounting.bridge_return_rows FROM PUBLIC;
GRANT SELECT, INSERT ON accounting.bridge_return_imports, accounting.bridge_return_rows TO accounting_app;
