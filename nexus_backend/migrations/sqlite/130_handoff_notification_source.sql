-- compatibility: incremental
-- NEXT04: notification investigations are staff handoffs, not delivery sends.
PRAGMA defer_foreign_keys=ON;
CREATE TABLE handoff_tasks_n04(
 id TEXT PRIMARY KEY,
 firm_id TEXT NOT NULL REFERENCES firms(id),
 source_kind TEXT NOT NULL CHECK(source_kind IN('tax_workpaper','delivery_package','batch_item','notification_investigation')),
 source_id TEXT NOT NULL,
 source_version INTEGER NOT NULL CHECK(source_version>0),
 source_fingerprint TEXT NOT NULL CHECK(length(source_fingerprint)=64),
 book_id TEXT NOT NULL REFERENCES books(id),
 role TEXT NOT NULL CHECK(role IN('reviewer','delivery','ops')),
 assignee_id TEXT REFERENCES users(id),
 due_on TEXT,
 next_action TEXT NOT NULL DEFAULT '',
 status TEXT NOT NULL CHECK(status IN('pending','assigned')) DEFAULT 'pending',
 reason TEXT NOT NULL DEFAULT '',
 created_by TEXT NOT NULL REFERENCES users(id),
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL,
 superseded_at TEXT,
 UNIQUE(source_kind,source_id,source_version,role,created_at)
);
INSERT INTO handoff_tasks_n04(
 id,firm_id,source_kind,source_id,source_version,source_fingerprint,book_id,role,
 assignee_id,due_on,next_action,status,reason,created_by,created_at,updated_at,superseded_at)
SELECT id,firm_id,source_kind,source_id,source_version,source_fingerprint,book_id,role,
 assignee_id,due_on,next_action,status,reason,created_by,created_at,updated_at,superseded_at
FROM handoff_tasks;
DROP TABLE handoff_tasks;
ALTER TABLE handoff_tasks_n04 RENAME TO handoff_tasks;
CREATE INDEX IF NOT EXISTS idx_handoff_open
 ON handoff_tasks(firm_id,book_id,role,superseded_at);
