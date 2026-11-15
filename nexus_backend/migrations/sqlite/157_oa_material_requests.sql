CREATE TABLE oa_material_requests(
 id TEXT PRIMARY KEY,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,book_id TEXT NOT NULL,
 snapshot_id TEXT NOT NULL REFERENCES oa_approval_snapshots(id),title TEXT NOT NULL,reason TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN('open','closed')),version INTEGER NOT NULL DEFAULT 1,
 created_by TEXT NOT NULL,created_at TEXT NOT NULL,UNIQUE(book_id,snapshot_id)
);
