-- compatibility: incremental
CREATE TABLE IF NOT EXISTS accounting.ingestion_connections(
 id TEXT PRIMARY KEY,
 firm_id TEXT NOT NULL REFERENCES accounting.firms(id),
 company_id TEXT NOT NULL REFERENCES accounting.companies(id),
 kind TEXT NOT NULL CHECK(kind IN('csv','xlsx','email','sftp','webhook','bank','invoice','erp')),
 name TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'active' CHECK(status IN('active','paused','error')),
 config_json TEXT NOT NULL CHECK(jsonb_typeof(config_json::jsonb)='object'),
 token_hash TEXT NOT NULL UNIQUE,
 last_cursor TEXT NOT NULL DEFAULT '',
 created_by TEXT NOT NULL REFERENCES accounting.users(id),
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS accounting.ingestion_runs(
 id TEXT PRIMARY KEY,
 connection_id TEXT NOT NULL REFERENCES accounting.ingestion_connections(id) ON DELETE CASCADE,
 firm_id TEXT NOT NULL REFERENCES accounting.firms(id),
 company_id TEXT NOT NULL REFERENCES accounting.companies(id),
 status TEXT NOT NULL CHECK(status IN('received','processing','succeeded','failed')),
 attempt INTEGER NOT NULL DEFAULT 1 CHECK(attempt>0),
 cursor TEXT NOT NULL DEFAULT '',
 summary_json TEXT NOT NULL CHECK(jsonb_typeof(summary_json::jsonb)='object'),
 error_code TEXT NOT NULL DEFAULT '',
 error_detail TEXT NOT NULL DEFAULT '',
 created_at TEXT NOT NULL,
 finished_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_ingestion_connections_firm
 ON accounting.ingestion_connections(firm_id,created_at DESC,id);
CREATE INDEX IF NOT EXISTS idx_ingestion_runs_connection
 ON accounting.ingestion_runs(connection_id,created_at DESC,id);
ALTER TABLE accounting.ingestion_connections ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.ingestion_connections FORCE ROW LEVEL SECURITY;
ALTER TABLE accounting.ingestion_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.ingestion_runs FORCE ROW LEVEL SECURITY;
CREATE POLICY ingestion_connections_manager ON accounting.ingestion_connections FOR ALL TO accounting_app
 USING(firm_id=accounting.actor_firm() AND EXISTS(
  SELECT 1 FROM accounting.memberships m WHERE m.firm_id=ingestion_connections.firm_id
  AND m.user_id=accounting.actor_user() AND m.role IN('owner','admin')))
 WITH CHECK(firm_id=accounting.actor_firm() AND EXISTS(
  SELECT 1 FROM accounting.memberships m WHERE m.firm_id=ingestion_connections.firm_id
  AND m.user_id=accounting.actor_user() AND m.role IN('owner','admin')));
CREATE POLICY ingestion_runs_manager ON accounting.ingestion_runs FOR ALL TO accounting_app
 USING(firm_id=accounting.actor_firm() AND EXISTS(
  SELECT 1 FROM accounting.memberships m WHERE m.firm_id=ingestion_runs.firm_id
  AND m.user_id=accounting.actor_user() AND m.role IN('owner','admin')))
 WITH CHECK(firm_id=accounting.actor_firm() AND EXISTS(
  SELECT 1 FROM accounting.memberships m WHERE m.firm_id=ingestion_runs.firm_id
  AND m.user_id=accounting.actor_user() AND m.role IN('owner','admin')));
CREATE POLICY ingestion_connections_system ON accounting.ingestion_connections TO accounting_system USING(true) WITH CHECK(true);
CREATE POLICY ingestion_runs_system ON accounting.ingestion_runs TO accounting_system USING(true) WITH CHECK(true);
REVOKE ALL ON accounting.ingestion_connections,accounting.ingestion_runs FROM PUBLIC,accounting_app,accounting_worker;
GRANT SELECT,INSERT,UPDATE ON accounting.ingestion_connections,accounting.ingestion_runs TO accounting_app;
GRANT SELECT,INSERT,UPDATE ON accounting.ingestion_connections,accounting.ingestion_runs TO accounting_system;
CREATE TRIGGER ingestion_connections_write_gate BEFORE INSERT OR UPDATE OR DELETE ON accounting.ingestion_connections FOR EACH ROW EXECUTE FUNCTION accounting.company_write_table_guard();
CREATE TRIGGER ingestion_runs_write_gate BEFORE INSERT OR UPDATE OR DELETE ON accounting.ingestion_runs FOR EACH ROW EXECUTE FUNCTION accounting.company_write_table_guard();
