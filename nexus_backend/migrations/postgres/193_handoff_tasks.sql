-- compatibility: incremental
SET LOCAL search_path=accounting,pg_catalog;
-- DCP05: a role ("who should handle it") and an assignment ("who is handling it")
-- are recorded separately so reassignment never rewrites history.
CREATE TABLE IF NOT EXISTS accounting.handoff_tasks(
 id TEXT PRIMARY KEY,
 firm_id TEXT NOT NULL REFERENCES accounting.firms(id),
 source_kind TEXT NOT NULL CHECK(source_kind IN('tax_workpaper','delivery_package','batch_item')),
 source_id TEXT NOT NULL,
 source_version INTEGER NOT NULL CHECK(source_version>0),
 source_fingerprint TEXT NOT NULL CHECK(length(source_fingerprint)=64),
 book_id TEXT NOT NULL REFERENCES accounting.books(id),
 role TEXT NOT NULL CHECK(role IN('reviewer','delivery','ops')),
 assignee_id TEXT REFERENCES accounting.users(id),
 due_on TEXT,
 next_action TEXT NOT NULL DEFAULT '',
 status TEXT NOT NULL CHECK(status IN('pending','assigned')) DEFAULT 'pending',
 reason TEXT NOT NULL DEFAULT '',
 created_by TEXT NOT NULL REFERENCES accounting.users(id),
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL,
 superseded_at TEXT,
 UNIQUE(source_kind,source_id,source_version,role,created_at)
);
CREATE INDEX IF NOT EXISTS idx_handoff_open
 ON accounting.handoff_tasks(firm_id,book_id,role,superseded_at);
