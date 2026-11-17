CREATE TABLE accounting.lease_draft_bindings (
  draft_id TEXT PRIMARY KEY,
  firm_id TEXT NOT NULL,
  company_id TEXT NOT NULL,
  book_id TEXT NOT NULL,
  contract_id TEXT NOT NULL,
  contract_version INTEGER NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('revenue','refund')),
  created_at TEXT NOT NULL
);
CREATE INDEX idx_lease_draft_bindings_contract ON accounting.lease_draft_bindings(contract_id, contract_version);
ALTER TABLE accounting.lease_draft_bindings ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.lease_draft_bindings FORCE ROW LEVEL SECURITY;
CREATE POLICY lease_draft_bindings_company_visible ON accounting.lease_draft_bindings
  USING (accounting.company_visible(company_id)) WITH CHECK (accounting.company_visible(company_id));
GRANT SELECT, INSERT ON accounting.lease_draft_bindings TO accounting_app;
