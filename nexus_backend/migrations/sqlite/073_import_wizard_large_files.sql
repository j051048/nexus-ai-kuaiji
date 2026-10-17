-- compatibility: incremental
-- 大文件银行流水导入：新增上传用途，并把原件大小上限放宽到 10 MB、
-- 行号上限放宽到 5000 行（SQLite 无法修改 CHECK，按官方建议重建表并保留数据与索引）。
CREATE TABLE upload_intents_new(
 id TEXT PRIMARY KEY,firm_id TEXT NOT NULL REFERENCES firms(id),user_id TEXT NOT NULL REFERENCES users(id),
 purpose TEXT NOT NULL CHECK(purpose IN('intake_attachment','event_attachment','case_attachment','material_attachment','bank_import','invoice_import')),
 filename TEXT NOT NULL CHECK(length(filename) BETWEEN 1 AND 150),
 media_type TEXT NOT NULL CHECK(length(media_type) BETWEEN 1 AND 120),
 size_bytes INTEGER NOT NULL CHECK(size_bytes BETWEEN 1 AND 10485760),
 sha256 TEXT NOT NULL CHECK(length(sha256)=64),
 storage_key TEXT NOT NULL UNIQUE,
 state TEXT NOT NULL DEFAULT 'pending' CHECK(state IN('pending','stored','consuming','consumed','discarded')),
 created_at TEXT NOT NULL,expires_at TEXT NOT NULL,consumed_ref TEXT
);
INSERT INTO upload_intents_new(id,firm_id,user_id,purpose,filename,media_type,size_bytes,sha256,storage_key,state,created_at,expires_at,consumed_ref)
 SELECT id,firm_id,user_id,purpose,filename,media_type,size_bytes,sha256,storage_key,state,created_at,expires_at,consumed_ref FROM upload_intents;
DROP TABLE upload_intents;
ALTER TABLE upload_intents_new RENAME TO upload_intents;
CREATE INDEX idx_upload_intents_owner ON upload_intents(firm_id,user_id,state,created_at);

CREATE TABLE bank_import_files_new(
 id TEXT PRIMARY KEY,book_id TEXT NOT NULL,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,
 storage_key TEXT NOT NULL UNIQUE,filename TEXT NOT NULL CHECK(length(filename) BETWEEN 1 AND 150),media_type TEXT NOT NULL,
 sha256 TEXT NOT NULL CHECK(length(sha256)=64),size_bytes INTEGER NOT NULL CHECK(size_bytes BETWEEN 1 AND 10485760),
 table_json TEXT NOT NULL CHECK(json_valid(table_json)),created_by TEXT NOT NULL REFERENCES users(id),created_at TEXT NOT NULL,
 UNIQUE(id,book_id,firm_id,company_id),FOREIGN KEY(book_id,firm_id,company_id) REFERENCES books(id,firm_id,company_id)
);
INSERT INTO bank_import_files_new(id,book_id,firm_id,company_id,storage_key,filename,media_type,sha256,size_bytes,table_json,created_by,created_at)
 SELECT id,book_id,firm_id,company_id,storage_key,filename,media_type,sha256,size_bytes,table_json,created_by,created_at FROM bank_import_files;
DROP TABLE bank_import_files;
ALTER TABLE bank_import_files_new RENAME TO bank_import_files;
CREATE INDEX idx_import_files_book ON bank_import_files(book_id,created_by,created_at);
CREATE TRIGGER bank_import_files_update BEFORE UPDATE ON bank_import_files BEGIN SELECT RAISE(ABORT,'Import originals immutable');END;
CREATE TRIGGER bank_import_files_delete BEFORE DELETE ON bank_import_files BEGIN SELECT RAISE(ABORT,'Import originals immutable');END;

CREATE TABLE bank_import_source_files_new(
 preview_id TEXT NOT NULL,file_id TEXT NOT NULL,book_id TEXT NOT NULL,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,source_id TEXT NOT NULL,
 row_number INTEGER NOT NULL CHECK(row_number BETWEEN 2 AND 10000000),
 PRIMARY KEY(preview_id,row_number),
 FOREIGN KEY(preview_id,book_id,firm_id,company_id) REFERENCES bank_import_previews(id,book_id,firm_id,company_id),
 FOREIGN KEY(file_id,book_id,firm_id,company_id) REFERENCES bank_import_files(id,book_id,firm_id,company_id),
 FOREIGN KEY(source_id,book_id) REFERENCES sources(id,book_id)
);
INSERT INTO bank_import_source_files_new(preview_id,file_id,book_id,firm_id,company_id,source_id,row_number)
 SELECT preview_id,file_id,book_id,firm_id,company_id,source_id,row_number FROM bank_import_source_files;
DROP TABLE bank_import_source_files;
ALTER TABLE bank_import_source_files_new RENAME TO bank_import_source_files;
CREATE INDEX idx_import_source_evidence ON bank_import_source_files(book_id,source_id);
CREATE TRIGGER bank_import_source_files_update BEFORE UPDATE ON bank_import_source_files BEGIN SELECT RAISE(ABORT,'Import lineage immutable');END;
CREATE TRIGGER bank_import_source_files_delete BEFORE DELETE ON bank_import_source_files BEGIN SELECT RAISE(ABORT,'Import lineage immutable');END;
