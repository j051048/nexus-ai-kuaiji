CREATE TABLE subject_aliases(
  id TEXT PRIMARY KEY,
  firm_id TEXT NOT NULL,
  company_id TEXT NOT NULL,
  subject_type TEXT NOT NULL CHECK(subject_type IN ('transport_subject')),
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
CREATE INDEX idx_subject_alias_lookup ON subject_aliases(firm_id,company_id,subject_type,alias,status);
INSERT INTO subject_aliases(id,firm_id,company_id,subject_type,subject_id,alias,source,status,effective_from,created_by,created_at)
SELECT id,firm_id,company_id,'transport_subject',subject_id,alias,source,status,substr(created_at,1,10),created_by,created_at FROM transport_aliases;
