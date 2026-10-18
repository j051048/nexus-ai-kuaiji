-- compatibility: incremental
-- R02: persistent restore hold for PG/remote targets. This is an operational
-- flag, not tenant data; it is readable by the restricted runtime so startup
-- can fail closed without relying on a local file or optional env var.
CREATE TABLE IF NOT EXISTS accounting.restore_state(
 instance_id TEXT PRIMARY KEY, hold BOOLEAN NOT NULL DEFAULT false,
 restore_id TEXT NOT NULL DEFAULT '', reason TEXT NOT NULL DEFAULT '',
 updated_by TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL
);
REVOKE ALL ON accounting.restore_state FROM PUBLIC;
GRANT SELECT ON accounting.restore_state TO accounting_app,accounting_worker,accounting_system;
