-- Bounded import originals and immutable previews, receipts and scoped profiles.
CREATE TABLE bank_import_files(
 id TEXT PRIMARY KEY,book_id TEXT NOT NULL,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,
 storage_key TEXT NOT NULL UNIQUE,filename TEXT NOT NULL CHECK(length(filename) BETWEEN 1 AND 150),media_type TEXT NOT NULL,
 sha256 TEXT NOT NULL CHECK(length(sha256)=64),size_bytes INTEGER NOT NULL CHECK(size_bytes BETWEEN 1 AND 2097152),
 table_json TEXT NOT NULL CHECK(json_valid(table_json)),created_by TEXT NOT NULL REFERENCES users(id),created_at TEXT NOT NULL,
 UNIQUE(id,book_id,firm_id,company_id),FOREIGN KEY(book_id,firm_id,company_id) REFERENCES books(id,firm_id,company_id)
);
CREATE TABLE bank_import_previews(
 id TEXT PRIMARY KEY,book_id TEXT NOT NULL,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,file_id TEXT NOT NULL,
 payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),fingerprint TEXT NOT NULL CHECK(length(fingerprint)=64),created_by TEXT NOT NULL REFERENCES users(id),created_at TEXT NOT NULL,
 UNIQUE(id,book_id,firm_id,company_id),FOREIGN KEY(file_id,book_id,firm_id,company_id) REFERENCES bank_import_files(id,book_id,firm_id,company_id)
);
CREATE TABLE bank_import_confirmations(
 preview_id TEXT PRIMARY KEY,book_id TEXT NOT NULL,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,result_json TEXT NOT NULL CHECK(json_valid(result_json)),
 created_by TEXT NOT NULL REFERENCES users(id),created_at TEXT NOT NULL,
 FOREIGN KEY(preview_id,book_id,firm_id,company_id) REFERENCES bank_import_previews(id,book_id,firm_id,company_id)
);
CREATE TABLE bank_import_source_files(
 preview_id TEXT NOT NULL,file_id TEXT NOT NULL,book_id TEXT NOT NULL,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,source_id TEXT NOT NULL,row_number INTEGER NOT NULL CHECK(row_number BETWEEN 2 AND 1001),
 PRIMARY KEY(preview_id,row_number),FOREIGN KEY(preview_id,book_id,firm_id,company_id) REFERENCES bank_import_previews(id,book_id,firm_id,company_id),
 FOREIGN KEY(file_id,book_id,firm_id,company_id) REFERENCES bank_import_files(id,book_id,firm_id,company_id),FOREIGN KEY(source_id,book_id) REFERENCES sources(id,book_id)
);
CREATE TABLE bank_import_profiles(
 id TEXT PRIMARY KEY,book_id TEXT NOT NULL,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,namespace TEXT NOT NULL CHECK(length(namespace) BETWEEN 1 AND 100),
 bank_account TEXT NOT NULL CHECK(length(bank_account) BETWEEN 1 AND 100),version INTEGER NOT NULL CHECK(version>0),headers_json TEXT NOT NULL CHECK(json_valid(headers_json)),
 mapping_json TEXT NOT NULL CHECK(json_valid(mapping_json)),created_by TEXT NOT NULL REFERENCES users(id),created_at TEXT NOT NULL,
 UNIQUE(book_id,namespace,bank_account,version),FOREIGN KEY(book_id,firm_id,company_id) REFERENCES books(id,firm_id,company_id)
);
CREATE INDEX idx_import_files_book ON bank_import_files(book_id,created_by,created_at);
CREATE INDEX idx_import_source_evidence ON bank_import_source_files(book_id,source_id);
CREATE INDEX idx_import_profile_scope ON bank_import_profiles(book_id,namespace,bank_account,version);
CREATE TRIGGER bank_import_files_update BEFORE UPDATE ON bank_import_files BEGIN SELECT RAISE(ABORT,'Import originals immutable');END;
CREATE TRIGGER bank_import_files_delete BEFORE DELETE ON bank_import_files BEGIN SELECT RAISE(ABORT,'Import originals immutable');END;
CREATE TRIGGER bank_import_previews_update BEFORE UPDATE ON bank_import_previews BEGIN SELECT RAISE(ABORT,'Import previews immutable');END;
CREATE TRIGGER bank_import_previews_delete BEFORE DELETE ON bank_import_previews BEGIN SELECT RAISE(ABORT,'Import previews immutable');END;
CREATE TRIGGER bank_import_confirmations_update BEFORE UPDATE ON bank_import_confirmations BEGIN SELECT RAISE(ABORT,'Import receipts immutable');END;
CREATE TRIGGER bank_import_confirmations_delete BEFORE DELETE ON bank_import_confirmations BEGIN SELECT RAISE(ABORT,'Import receipts immutable');END;
CREATE TRIGGER bank_import_source_files_update BEFORE UPDATE ON bank_import_source_files BEGIN SELECT RAISE(ABORT,'Import lineage immutable');END;
CREATE TRIGGER bank_import_source_files_delete BEFORE DELETE ON bank_import_source_files BEGIN SELECT RAISE(ABORT,'Import lineage immutable');END;
CREATE TRIGGER bank_import_profiles_update BEFORE UPDATE ON bank_import_profiles BEGIN SELECT RAISE(ABORT,'Scoped mappings versioned');END;
CREATE TRIGGER bank_import_profiles_delete BEFORE DELETE ON bank_import_profiles BEGIN SELECT RAISE(ABORT,'Scoped mappings versioned');END;
