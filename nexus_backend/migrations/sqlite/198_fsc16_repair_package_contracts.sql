CREATE TABLE repair_package_contracts (
  id TEXT PRIMARY KEY,
  firm_id TEXT NOT NULL,
  company_id TEXT NOT NULL,
  book_id TEXT NOT NULL REFERENCES books(id),
  contract_no TEXT NOT NULL,
  version INTEGER NOT NULL CHECK (version > 0),
  status TEXT NOT NULL CHECK (status IN ('draft','confirmed','active','superseded','inactive')),
  coverage_scope TEXT NOT NULL,
  vehicle_external_ids_json TEXT NOT NULL,
  valid_from TEXT NOT NULL,
  valid_to TEXT NOT NULL,
  limit_cents INTEGER NOT NULL CHECK (limit_cents > 0),
  policy TEXT NOT NULL CHECK (policy IN ('contract_cumulative','monthly_reset')),
  logical_id TEXT NOT NULL,
  fingerprint TEXT NOT NULL,
  created_by TEXT NOT NULL,
  created_at TEXT NOT NULL,
  confirmed_by TEXT,
  confirmed_at TEXT,
  activated_at TEXT,
  superseded_at TEXT,
  reason TEXT NOT NULL,
  UNIQUE (book_id, contract_no, version)
);

CREATE INDEX idx_repair_package_contracts_book ON repair_package_contracts(book_id, contract_no, version);
CREATE INDEX idx_repair_package_contracts_logical ON repair_package_contracts(book_id, logical_id, version);
CREATE UNIQUE INDEX uq_repair_package_active
  ON repair_package_contracts(book_id, contract_no)
  WHERE status='active';

CREATE TABLE repair_package_usage (
  id TEXT PRIMARY KEY,
  contract_id TEXT NOT NULL REFERENCES repair_package_contracts(id),
  firm_id TEXT NOT NULL,
  company_id TEXT NOT NULL,
  book_id TEXT NOT NULL REFERENCES books(id),
  period TEXT NOT NULL,
  source_key TEXT NOT NULL,
  source_version INTEGER NOT NULL CHECK (source_version > 0),
  amount_cents INTEGER NOT NULL CHECK (amount_cents > 0),
  kind TEXT NOT NULL CHECK (kind IN ('consume','release')),
  release_of TEXT,
  reason TEXT,
  idempotency_key TEXT NOT NULL,
  fingerprint TEXT NOT NULL,
  created_by TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE (book_id, idempotency_key),
  CHECK ((kind='consume' AND release_of IS NULL) OR (kind='release' AND release_of IS NOT NULL))
);

CREATE INDEX idx_repair_package_usage_contract ON repair_package_usage(contract_id, kind);
CREATE INDEX idx_repair_package_usage_release ON repair_package_usage(release_of);
