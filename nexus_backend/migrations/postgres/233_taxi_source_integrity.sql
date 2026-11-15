-- compatibility: incremental
-- Preserve the original bytes and the interpretation contract separately from review state.
ALTER TABLE accounting.tax_source_files ADD COLUMN IF NOT EXISTS import_contract_sha256 TEXT NOT NULL DEFAULT '';
ALTER TABLE accounting.tax_source_files ADD COLUMN IF NOT EXISTS original_content_base64 TEXT NOT NULL DEFAULT '';
ALTER TABLE accounting.tax_fee_batches ADD COLUMN IF NOT EXISTS source_snapshot_json TEXT NOT NULL DEFAULT '[]';
