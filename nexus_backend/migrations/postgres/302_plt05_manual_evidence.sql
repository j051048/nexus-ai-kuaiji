CREATE TABLE IF NOT EXISTS accounting.bridge_return_evidence (
  import_id TEXT PRIMARY KEY REFERENCES accounting.bridge_return_imports(id),
  book_id TEXT NOT NULL,
  firm_id TEXT NOT NULL,
  company_id TEXT NOT NULL,
  filename TEXT NOT NULL,
  media_type TEXT NOT NULL,
  sha256 TEXT NOT NULL,
  size_bytes BIGINT NOT NULL CHECK(size_bytes > 0),
  content BYTEA NOT NULL,
  created_by TEXT NOT NULL REFERENCES accounting.users(id),
  created_at TIMESTAMPTZ NOT NULL
);
ALTER TABLE accounting.bridge_return_evidence ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.bridge_return_evidence FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS bridge_return_evidence_firm ON accounting.bridge_return_evidence;
CREATE POLICY bridge_return_evidence_firm ON accounting.bridge_return_evidence
  USING (firm_id = accounting.actor_firm())
  WITH CHECK (firm_id = accounting.actor_firm() AND accounting.company_visible(company_id));
GRANT SELECT, INSERT ON accounting.bridge_return_evidence TO accounting_app;
REVOKE UPDATE, DELETE ON accounting.bridge_return_evidence FROM accounting_app;
