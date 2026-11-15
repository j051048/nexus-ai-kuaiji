CREATE TABLE oa_approval_snapshots(
 id TEXT PRIMARY KEY,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,book_id TEXT NOT NULL,
 oa_event_id TEXT NOT NULL,oa_version INTEGER NOT NULL CHECK(oa_version>0),period TEXT NOT NULL,
 title TEXT NOT NULL,approved_by TEXT NOT NULL,approved_at TEXT NOT NULL,
 lines_json TEXT NOT NULL,source_sha256 TEXT NOT NULL,fingerprint TEXT NOT NULL,
 created_by TEXT NOT NULL,created_at TEXT NOT NULL,
 UNIQUE(firm_id,company_id,book_id,oa_event_id,oa_version),
 FOREIGN KEY(book_id,firm_id,company_id) REFERENCES books(id,firm_id,company_id),
 FOREIGN KEY(created_by) REFERENCES users(id)
);
CREATE TRIGGER oa_snapshot_identity BEFORE UPDATE ON oa_approval_snapshots BEGIN SELECT RAISE(ABORT,'OA approval snapshot is immutable'); END;
CREATE TRIGGER oa_snapshot_delete BEFORE DELETE ON oa_approval_snapshots BEGIN SELECT RAISE(ABORT,'OA approval snapshot is immutable'); END;
