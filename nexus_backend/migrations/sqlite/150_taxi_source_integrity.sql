-- compatibility: incremental
ALTER TABLE tax_source_files ADD COLUMN import_contract_sha256 TEXT NOT NULL DEFAULT '';
ALTER TABLE tax_source_files ADD COLUMN original_content_base64 TEXT NOT NULL DEFAULT '';
ALTER TABLE tax_fee_batches ADD COLUMN source_snapshot_json TEXT NOT NULL DEFAULT '[]';
