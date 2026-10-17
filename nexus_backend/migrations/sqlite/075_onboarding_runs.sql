-- compatibility: incremental
CREATE TABLE IF NOT EXISTS onboarding_runs(
 id TEXT PRIMARY KEY,
 firm_id TEXT NOT NULL,
 company_id TEXT NOT NULL,
 period TEXT NOT NULL,
 source_company_id TEXT,
 status TEXT NOT NULL CHECK(status IN('draft','ready','blocked','completed')),
 steps_json TEXT NOT NULL,
 result_json TEXT NOT NULL DEFAULT '{}',
 created_by TEXT NOT NULL,
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_onboarding_runs_firm
 ON onboarding_runs(firm_id,created_at DESC,id);
