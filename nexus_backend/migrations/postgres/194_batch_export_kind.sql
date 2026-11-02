-- compatibility: incremental
SET LOCAL search_path=accounting,pg_catalog;
-- DCP06-B: the batch table carries one more kind; no parallel queue.
DO $$
DECLARE
  name text;
BEGIN
  SELECT conname INTO name
    FROM pg_constraint
   WHERE conrelid='accounting.batch_operations'::regclass
     AND contype='c'
     AND pg_get_constraintdef(oid) LIKE '%monthly_open%';
  IF name IS NOT NULL THEN
    EXECUTE format('ALTER TABLE accounting.batch_operations DROP CONSTRAINT %I', name);
  END IF;
END $$;
ALTER TABLE accounting.batch_operations
  ADD CONSTRAINT batch_operations_kind_check
  CHECK (kind IN ('monthly_open','review_export','package_draft'));
