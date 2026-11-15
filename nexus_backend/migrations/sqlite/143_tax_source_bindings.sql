CREATE TABLE tax_source_bindings (
 id TEXT PRIMARY KEY,
 firm_id TEXT NOT NULL,
 company_id TEXT NOT NULL,
 book_id TEXT NOT NULL,
 period TEXT NOT NULL,
 source_file_id TEXT NOT NULL REFERENCES tax_source_files(id),
 source_system TEXT NOT NULL,
 external_id TEXT NOT NULL,
 source_fingerprint TEXT NOT NULL CHECK(length(source_fingerprint)=64),
 output_kind TEXT NOT NULL CHECK(output_kind IN('workpaper','preview','approval')),
 output_id TEXT NOT NULL,
 output_version INTEGER NOT NULL CHECK(output_version>0),
 frozen INTEGER NOT NULL DEFAULT 0 CHECK(frozen IN(0,1)),
 created_by TEXT NOT NULL,
 created_at TEXT NOT NULL,
 invalidated_at TEXT,
 invalidated_reason TEXT,
 UNIQUE(book_id,output_kind,output_id,output_version)
);
CREATE INDEX idx_tax_source_bindings_output ON tax_source_bindings(book_id,output_kind,output_id,output_version);
