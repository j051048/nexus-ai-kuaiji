-- compatibility: incremental
-- R02: persistent restore hold for targets without a local marker directory.
CREATE TABLE IF NOT EXISTS restore_state(
 instance_id TEXT PRIMARY KEY, hold INTEGER NOT NULL DEFAULT 0,
 restore_id TEXT NOT NULL DEFAULT '', reason TEXT NOT NULL DEFAULT '',
 updated_by TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL
);
