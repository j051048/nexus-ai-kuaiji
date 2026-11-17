DO $$
DECLARE r record;
BEGIN
  FOR r IN SELECT conname FROM pg_constraint
    WHERE conrelid='accounting.subject_aliases'::regclass AND contype='c'
      AND pg_get_constraintdef(oid) LIKE '%subject_type%'
  LOOP
    EXECUTE format('ALTER TABLE accounting.subject_aliases DROP CONSTRAINT %I', r.conname);
  END LOOP;
END $$;
ALTER TABLE accounting.subject_aliases ADD CONSTRAINT subject_aliases_subject_type_check
  CHECK(subject_type IN ('transport_subject','counterparty'));
