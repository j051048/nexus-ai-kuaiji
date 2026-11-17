PRAGMA foreign_keys=OFF;
CREATE TABLE subject_aliases_new(
  id TEXT PRIMARY KEY,
  firm_id TEXT NOT NULL,
  company_id TEXT NOT NULL,
  subject_type TEXT NOT NULL CHECK(subject_type IN ('transport_subject','counterparty')),
  subject_id TEXT NOT NULL,
  alias TEXT NOT NULL,
  source TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'approved' CHECK(status IN ('approved','retired')),
  effective_from TEXT NOT NULL,
  effective_to TEXT,
  created_by TEXT NOT NULL,
  created_at TEXT NOT NULL,
  retired_by TEXT,
  retired_at TEXT,
  retire_reason TEXT,
  UNIQUE(firm_id,company_id,subject_type,alias,effective_from)
);
INSERT INTO subject_aliases_new SELECT * FROM subject_aliases;
DROP TABLE subject_aliases;
ALTER TABLE subject_aliases_new RENAME TO subject_aliases;
CREATE INDEX idx_subject_alias_lookup ON subject_aliases(firm_id,company_id,subject_type,alias,status);
PRAGMA foreign_keys=ON;
