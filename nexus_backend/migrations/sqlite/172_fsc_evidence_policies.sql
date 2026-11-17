CREATE TABLE IF NOT EXISTS fsc_evidence_policies (
  id TEXT PRIMARY KEY,
  firm_id TEXT NOT NULL,
  company_id TEXT NOT NULL,
  industry_code TEXT NOT NULL,
  version INTEGER NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('draft','confirmed','active','superseded','inactive')),
  requirements_json TEXT NOT NULL,
  exceptions_json TEXT NOT NULL,
  fingerprint TEXT NOT NULL,
  created_by TEXT NOT NULL,
  created_at TEXT NOT NULL,
  confirmed_by TEXT,
  confirmed_at TEXT,
  activated_by TEXT,
  activated_at TEXT,
  superseded_at TEXT,
  UNIQUE(company_id, industry_code, version)
);
CREATE INDEX IF NOT EXISTS idx_fsc_evidence_policies_scope
  ON fsc_evidence_policies(firm_id, company_id, industry_code, status);
CREATE TABLE IF NOT EXISTS fsc_evidence_policy_events (
  id TEXT PRIMARY KEY,
  policy_id TEXT NOT NULL,
  event TEXT NOT NULL,
  actor_id TEXT NOT NULL,
  at TEXT NOT NULL,
  detail_json TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS fsc_evidence_policy_immutable
BEFORE UPDATE ON fsc_evidence_policies
WHEN OLD.status <> 'draft' AND (
  NEW.requirements_json <> OLD.requirements_json OR NEW.exceptions_json <> OLD.exceptions_json
  OR NEW.industry_code <> OLD.industry_code OR NEW.version <> OLD.version
)
BEGIN SELECT RAISE(ABORT, 'confirmed evidence policy is immutable'); END;
CREATE UNIQUE INDEX IF NOT EXISTS fsc_evidence_policies_one_active ON fsc_evidence_policies(company_id, industry_code) WHERE status='active';
