-- compatibility: incremental
CREATE TABLE IF NOT EXISTS accounting.onboarding_runs(
 id TEXT PRIMARY KEY,
 firm_id TEXT NOT NULL REFERENCES accounting.firms(id),
 company_id TEXT NOT NULL REFERENCES accounting.companies(id),
 period TEXT NOT NULL,
 source_company_id TEXT REFERENCES accounting.companies(id),
 status TEXT NOT NULL CHECK(status IN('draft','ready','blocked','completed')),
 steps_json TEXT NOT NULL,
 result_json TEXT NOT NULL DEFAULT '{}',
 created_by TEXT NOT NULL REFERENCES accounting.users(id),
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_onboarding_runs_firm
 ON accounting.onboarding_runs(firm_id,created_at DESC,id);
ALTER TABLE accounting.onboarding_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.onboarding_runs FORCE ROW LEVEL SECURITY;
CREATE POLICY onboarding_runs_manager ON accounting.onboarding_runs FOR ALL TO accounting_app
 USING(firm_id=accounting.actor_firm() AND EXISTS(
  SELECT 1 FROM accounting.memberships m WHERE m.firm_id=onboarding_runs.firm_id
  AND m.user_id=accounting.actor_user() AND m.role IN('owner','admin')))
 WITH CHECK(firm_id=accounting.actor_firm() AND EXISTS(
  SELECT 1 FROM accounting.memberships m WHERE m.firm_id=onboarding_runs.firm_id
  AND m.user_id=accounting.actor_user() AND m.role IN('owner','admin')));
REVOKE ALL ON accounting.onboarding_runs FROM PUBLIC,accounting_app,accounting_worker;
GRANT SELECT,INSERT,UPDATE ON accounting.onboarding_runs TO accounting_app;
