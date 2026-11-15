CREATE TABLE IF NOT EXISTS accounting.bridge_export_artifacts (
  id TEXT PRIMARY KEY,
  outbox_id TEXT NOT NULL REFERENCES accounting.bridge_outbox(id),
  ledger_id TEXT NOT NULL,
  book_id TEXT NOT NULL REFERENCES accounting.books(id),
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
  created_by TEXT NOT NULL REFERENCES accounting.users(id),
  created_at TIMESTAMPTZ NOT NULL,
  fingerprint TEXT NOT NULL,
  UNIQUE(outbox_id, content_sha256)
);
CREATE TABLE IF NOT EXISTS accounting.bridge_export_receipts (
  id TEXT PRIMARY KEY,
  artifact_id TEXT NOT NULL REFERENCES accounting.bridge_export_artifacts(id),
  book_id TEXT NOT NULL REFERENCES accounting.books(id),
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
  external_posting BOOLEAN NOT NULL DEFAULT FALSE CHECK(external_posting=FALSE),
  created_by TEXT NOT NULL REFERENCES accounting.users(id),
  created_at TIMESTAMPTZ NOT NULL,
  fingerprint TEXT NOT NULL,
  UNIQUE(artifact_id, receipt_code),
  UNIQUE(artifact_id, content_sha256)
);
CREATE TABLE IF NOT EXISTS accounting.bridge_export_receipt_rows (
  receipt_id TEXT NOT NULL REFERENCES accounting.bridge_export_receipts(id) ON DELETE CASCADE,
  row_number INTEGER NOT NULL,
  voucher_external_code TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('accepted','rejected','duplicate','unknown')),
  reason TEXT NOT NULL DEFAULT '',
  raw_hash TEXT NOT NULL,
  PRIMARY KEY(receipt_id, row_number)
);
CREATE INDEX IF NOT EXISTS idx_bridge_artifacts_outbox ON accounting.bridge_export_artifacts(outbox_id, created_at, id);
CREATE INDEX IF NOT EXISTS idx_bridge_receipts_artifact ON accounting.bridge_export_receipts(artifact_id, created_at, id);
ALTER TABLE accounting.bridge_export_artifacts ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.bridge_export_artifacts FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS bridge_export_artifacts_tenant ON accounting.bridge_export_artifacts;
CREATE POLICY bridge_export_artifacts_tenant ON accounting.bridge_export_artifacts FOR ALL TO accounting_app
  USING (firm_id=accounting.actor_firm() AND accounting.company_visible(company_id))
  WITH CHECK (firm_id=accounting.actor_firm() AND accounting.company_visible(company_id));
ALTER TABLE accounting.bridge_export_receipts ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.bridge_export_receipts FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS bridge_export_receipts_tenant ON accounting.bridge_export_receipts;
CREATE POLICY bridge_export_receipts_tenant ON accounting.bridge_export_receipts FOR ALL TO accounting_app
  USING (firm_id=accounting.actor_firm() AND accounting.company_visible(company_id))
  WITH CHECK (firm_id=accounting.actor_firm() AND accounting.company_visible(company_id));
ALTER TABLE accounting.bridge_export_receipt_rows ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.bridge_export_receipt_rows FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS bridge_export_receipt_rows_tenant ON accounting.bridge_export_receipt_rows;
CREATE POLICY bridge_export_receipt_rows_tenant ON accounting.bridge_export_receipt_rows FOR ALL TO accounting_app
  USING (EXISTS (SELECT 1 FROM accounting.bridge_export_receipts r WHERE r.id=receipt_id AND r.firm_id=accounting.actor_firm() AND accounting.company_visible(r.company_id)))
  WITH CHECK (EXISTS (SELECT 1 FROM accounting.bridge_export_receipts r WHERE r.id=receipt_id AND r.firm_id=accounting.actor_firm() AND accounting.company_visible(r.company_id)));
REVOKE ALL ON accounting.bridge_export_artifacts, accounting.bridge_export_receipts, accounting.bridge_export_receipt_rows FROM PUBLIC;
GRANT SELECT, INSERT ON accounting.bridge_export_artifacts, accounting.bridge_export_receipts, accounting.bridge_export_receipt_rows TO accounting_app;
