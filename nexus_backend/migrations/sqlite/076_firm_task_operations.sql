-- compatibility: incremental
CREATE TABLE IF NOT EXISTS firm_task_assignments(
 firm_id TEXT NOT NULL,
 kind TEXT NOT NULL,
 ref TEXT NOT NULL,
 assignee_id TEXT,
 due_on TEXT,
 sla_state TEXT NOT NULL DEFAULT 'normal' CHECK(sla_state IN('normal','today','overdue','blocked')),
 version INTEGER NOT NULL DEFAULT 1 CHECK(version>0),
 note TEXT NOT NULL DEFAULT '',
 updated_by TEXT NOT NULL,
 updated_at TEXT NOT NULL,
 PRIMARY KEY(firm_id,kind,ref)
);
CREATE TABLE IF NOT EXISTS client_messages(
 id TEXT PRIMARY KEY,
 firm_id TEXT NOT NULL,
 company_id TEXT NOT NULL,
 recipient_user_id TEXT,
 kind TEXT NOT NULL CHECK(kind IN('reminder','return','system','approval')),
 subject TEXT NOT NULL,
 body TEXT NOT NULL,
 related_kind TEXT NOT NULL DEFAULT '',
 related_ref TEXT NOT NULL DEFAULT '',
 created_by TEXT NOT NULL,
 created_at TEXT NOT NULL,
 read_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_firm_task_assignments_assignee
 ON firm_task_assignments(firm_id,assignee_id,due_on);
CREATE INDEX IF NOT EXISTS idx_client_messages_company
 ON client_messages(firm_id,company_id,created_at DESC,id);
CREATE INDEX IF NOT EXISTS idx_client_messages_recipient
 ON client_messages(firm_id,recipient_user_id,created_at DESC,id);
