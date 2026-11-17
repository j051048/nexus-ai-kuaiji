CREATE TABLE IF NOT EXISTS accounting.fsc_evidence_policies (
  id TEXT PRIMARY KEY,
  firm_id TEXT NOT NULL,
  company_id TEXT NOT NULL,
  industry_code TEXT NOT NULL,
  version INTEGER NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('draft','confirmed','active','superseded','inactive')),
  requirements_json JSONB NOT NULL,
  exceptions_json JSONB NOT NULL,
  fingerprint TEXT NOT NULL,
  created_by TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL,
  confirmed_by TEXT,
  confirmed_at TIMESTAMPTZ,
  activated_by TEXT,
  activated_at TIMESTAMPTZ,
  superseded_at TIMESTAMPTZ,
  UNIQUE(company_id, industry_code, version)
);
CREATE INDEX IF NOT EXISTS idx_fsc_evidence_policies_scope
  ON accounting.fsc_evidence_policies(firm_id, company_id, industry_code, status);
CREATE TABLE IF NOT EXISTS accounting.fsc_evidence_policy_events (
  id TEXT PRIMARY KEY,
  policy_id TEXT NOT NULL,
  event TEXT NOT NULL,
  actor_id TEXT NOT NULL,
  at TIMESTAMPTZ NOT NULL,
  detail_json JSONB NOT NULL
);
ALTER TABLE accounting.fsc_evidence_policies ENABLE ROW LEVEL SECURITY;
CREATE POLICY fsc_evidence_policies_scope ON accounting.fsc_evidence_policies
  USING (accounting.company_visible(company_id));
GRANT SELECT,INSERT,UPDATE ON accounting.fsc_evidence_policies TO accounting_app;
GRANT SELECT,INSERT ON accounting.fsc_evidence_policy_events TO accounting_app;
CREATE UNIQUE INDEX IF NOT EXISTS fsc_evidence_policies_one_active ON accounting.fsc_evidence_policies(company_id, industry_code) WHERE status='active';
