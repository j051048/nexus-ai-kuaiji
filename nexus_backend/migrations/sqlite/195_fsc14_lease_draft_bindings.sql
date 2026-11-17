CREATE TABLE lease_draft_bindings (
  draft_id TEXT PRIMARY KEY,
  firm_id TEXT NOT NULL,
  company_id TEXT NOT NULL,
  book_id TEXT NOT NULL,
  contract_id TEXT NOT NULL,
  contract_version INTEGER NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('revenue','refund')),
  created_at TEXT NOT NULL
);
CREATE INDEX idx_lease_draft_bindings_contract ON lease_draft_bindings(contract_id, contract_version);
