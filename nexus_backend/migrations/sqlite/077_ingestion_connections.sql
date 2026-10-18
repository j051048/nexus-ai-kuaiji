-- compatibility: incremental
CREATE TABLE IF NOT EXISTS ingestion_connections(
 id TEXT PRIMARY KEY,
 firm_id TEXT NOT NULL,
 company_id TEXT NOT NULL,
 kind TEXT NOT NULL CHECK(kind IN('csv','xlsx','email','sftp','webhook','bank','invoice','erp')),
 name TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'active' CHECK(status IN('active','paused','error')),
 config_json TEXT NOT NULL,
 token_hash TEXT NOT NULL UNIQUE,
 last_cursor TEXT NOT NULL DEFAULT '',
 created_by TEXT NOT NULL,
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ingestion_runs(
 id TEXT PRIMARY KEY,
 connection_id TEXT NOT NULL REFERENCES ingestion_connections(id) ON DELETE CASCADE,
 firm_id TEXT NOT NULL,
 company_id TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN('received','processing','succeeded','failed')),
 attempt INTEGER NOT NULL DEFAULT 1 CHECK(attempt>0),
 cursor TEXT NOT NULL DEFAULT '',
 summary_json TEXT NOT NULL,
 error_code TEXT NOT NULL DEFAULT '',
 error_detail TEXT NOT NULL DEFAULT '',
 created_at TEXT NOT NULL,
 finished_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_ingestion_connections_firm
 ON ingestion_connections(firm_id,created_at DESC,id);
CREATE INDEX IF NOT EXISTS idx_ingestion_runs_connection
 ON ingestion_runs(connection_id,created_at DESC,id);
