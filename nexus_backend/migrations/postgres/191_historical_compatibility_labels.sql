-- compatibility: breaking
-- Repair classification only; historical SQL, checksum and applied_at stay frozen.
-- Conservative default for these originally unmarked files is breaking.
-- This repair is itself breaking: an old image must not ignore the stricter boundary.
SET LOCAL search_path=accounting,pg_catalog;

DO $repair$
DECLARE
 expected RECORD;
 recorded RECORD;
BEGIN
 -- Lock and validate every historical identity before changing any label.
 -- Ascending order also serializes concurrent direct replays without lock inversion.
 FOR expected IN
  SELECT * FROM (VALUES
   (87, '949a3933621d8686b24c05df01ee7b54ed927099e1accd1696666efdfc0936ba'),
   (138,'3896fcbe7ea8d26b96060597b5a7ebb638cdc2acfdefb822486845e133a5494f'),
   (148,'25fdd7d0130767833aba38a2984e32ce915a93f5643b3208299bae30758b4dbe'),
   (149,'b2c895897a8bd715052034754f5c8836f985919a1ebf99e35258e086bec5441a'),
   (150,'f77ea8509a8246a624ae83ebc068a6b6977a64e35f1d225bd73a231b5906f8c0')
  ) AS identities(version,checksum) ORDER BY version
 LOOP
  SELECT checksum,compatibility INTO recorded
   FROM accounting.schema_migrations WHERE version=expected.version FOR UPDATE;
  IF NOT FOUND THEN
   RAISE EXCEPTION 'Historical migration % is missing; compatibility repair refused',expected.version
    USING ERRCODE='23514';
  END IF;
  IF recorded.checksum IS DISTINCT FROM expected.checksum
   OR recorded.compatibility NOT IN('incremental','breaking') THEN
   RAISE EXCEPTION 'Historical migration % identity mismatch; compatibility repair refused',expected.version
    USING ERRCODE='23514';
  END IF;
 END LOOP;

 UPDATE accounting.schema_migrations SET compatibility='breaking'
  WHERE version IN(87,138,148,149,150) AND compatibility='incremental';
END
$repair$;
