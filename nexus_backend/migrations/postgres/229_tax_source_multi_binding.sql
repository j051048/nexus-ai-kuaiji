DO $$
DECLARE constraint_name text;
BEGIN
  FOR constraint_name IN
    SELECT conname FROM pg_constraint
    WHERE conrelid='accounting.tax_source_bindings'::regclass AND contype='u'
  LOOP
    EXECUTE format('ALTER TABLE accounting.tax_source_bindings DROP CONSTRAINT %I', constraint_name);
  END LOOP;
END $$;
ALTER TABLE accounting.tax_source_bindings
  ADD CONSTRAINT tax_source_bindings_output_source_unique
  UNIQUE(book_id,output_kind,output_id,output_version,source_file_id);

ALTER TABLE accounting.tax_source_contributions
  ADD COLUMN IF NOT EXISTS output_version INTEGER NOT NULL DEFAULT 1;
CREATE INDEX IF NOT EXISTS idx_tax_source_contributions_output_version
  ON accounting.tax_source_contributions(book_id,output_kind,output_id,output_version);
