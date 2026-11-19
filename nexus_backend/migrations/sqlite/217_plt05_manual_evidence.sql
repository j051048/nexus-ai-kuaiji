CREATE TABLE IF NOT EXISTS bridge_return_evidence (
  import_id TEXT PRIMARY KEY REFERENCES bridge_return_imports(id),
  book_id TEXT NOT NULL REFERENCES books(id),
  firm_id TEXT NOT NULL,
  company_id TEXT NOT NULL,
  filename TEXT NOT NULL,
  media_type TEXT NOT NULL,
  sha256 TEXT NOT NULL,
  size_bytes INTEGER NOT NULL CHECK(size_bytes > 0),
  content BLOB NOT NULL,
  created_by TEXT NOT NULL REFERENCES users(id),
  created_at TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS bridge_return_evidence_no_delete
BEFORE DELETE ON bridge_return_evidence
BEGIN SELECT RAISE(ABORT, '回执证据不能删除'); END;
