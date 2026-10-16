-- Large-file upload intents and invoice export-table batches. Both are
-- infrastructure/evidence records: they never create accounting facts.
CREATE TABLE upload_intents(
 id TEXT PRIMARY KEY,firm_id TEXT NOT NULL REFERENCES firms(id),user_id TEXT NOT NULL REFERENCES users(id),
 purpose TEXT NOT NULL CHECK(purpose IN('intake_attachment','event_attachment','case_attachment','material_attachment','invoice_import')),
 filename TEXT NOT NULL CHECK(length(filename) BETWEEN 1 AND 150),
 media_type TEXT NOT NULL CHECK(length(media_type) BETWEEN 1 AND 120),
 size_bytes INTEGER NOT NULL CHECK(size_bytes BETWEEN 1 AND 10485760),
 sha256 TEXT NOT NULL CHECK(length(sha256)=64),
 storage_key TEXT NOT NULL UNIQUE,
 state TEXT NOT NULL DEFAULT 'pending' CHECK(state IN('pending','stored','consuming','consumed','discarded')),
 created_at TEXT NOT NULL,expires_at TEXT NOT NULL,consumed_ref TEXT
);
CREATE INDEX idx_upload_intents_owner ON upload_intents(firm_id,user_id,state,created_at);
CREATE TABLE invoice_import_batches(
 id TEXT PRIMARY KEY,book_id TEXT NOT NULL,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,
 filename TEXT NOT NULL CHECK(length(filename) BETWEEN 1 AND 150),
 storage_key TEXT NOT NULL UNIQUE,sha256 TEXT NOT NULL CHECK(length(sha256)=64),
 size_bytes INTEGER NOT NULL CHECK(size_bytes BETWEEN 1 AND 10485760),
 row_count INTEGER NOT NULL CHECK(row_count BETWEEN 0 AND 5000),
 direction TEXT NOT NULL CHECK(direction IN('input','output','mixed')),
 rows_json TEXT NOT NULL,notes_json TEXT NOT NULL DEFAULT '[]',
 created_by TEXT NOT NULL REFERENCES users(id),created_at TEXT NOT NULL,
 FOREIGN KEY(book_id,firm_id,company_id) REFERENCES books(id,firm_id,company_id)
);
CREATE INDEX idx_invoice_batches_book ON invoice_import_batches(book_id,created_at,id);
