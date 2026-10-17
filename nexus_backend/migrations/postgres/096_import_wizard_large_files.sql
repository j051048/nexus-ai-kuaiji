-- compatibility: incremental
-- 大文件银行流水导入：新增上传用途，并把原件大小上限放宽到 10 MB、
-- 行号上限放宽到 5000 行（替换对应 CHECK，数据与其它约束不变）。
DO $$ DECLARE name TEXT;BEGIN
 FOR name IN SELECT c.conname FROM pg_constraint c
  JOIN pg_class t ON t.oid=c.conrelid JOIN pg_namespace n ON n.oid=t.relnamespace
  WHERE n.nspname='accounting' AND t.relname='upload_intents' AND c.contype='c'
  AND pg_get_constraintdef(c.oid) LIKE '%purpose%' LOOP
  EXECUTE format('ALTER TABLE accounting.upload_intents DROP CONSTRAINT %I',name);
 END LOOP;
END $$;
ALTER TABLE accounting.upload_intents ADD CONSTRAINT upload_intents_purpose_check
 CHECK(purpose IN('intake_attachment','event_attachment','case_attachment','material_attachment','bank_import','invoice_import'));

DO $$ DECLARE name TEXT;BEGIN
 FOR name IN SELECT c.conname FROM pg_constraint c
  JOIN pg_class t ON t.oid=c.conrelid JOIN pg_namespace n ON n.oid=t.relnamespace
  WHERE n.nspname='accounting' AND t.relname='bank_import_files' AND c.contype='c'
  AND pg_get_constraintdef(c.oid) LIKE '%size_bytes%' LOOP
  EXECUTE format('ALTER TABLE accounting.bank_import_files DROP CONSTRAINT %I',name);
 END LOOP;
END $$;
ALTER TABLE accounting.bank_import_files ADD CONSTRAINT bank_import_files_size_bytes_check
 CHECK(size_bytes BETWEEN 1 AND 10485760);

DO $$ DECLARE name TEXT;BEGIN
 FOR name IN SELECT c.conname FROM pg_constraint c
  JOIN pg_class t ON t.oid=c.conrelid JOIN pg_namespace n ON n.oid=t.relnamespace
  WHERE n.nspname='accounting' AND t.relname='bank_import_files' AND c.contype='c'
  AND pg_get_constraintdef(c.oid) LIKE '%table_json%' LOOP
  EXECUTE format('ALTER TABLE accounting.bank_import_files DROP CONSTRAINT %I',name);
 END LOOP;
END $$;
ALTER TABLE accounting.bank_import_files ADD CONSTRAINT bank_import_files_table_json_check
 CHECK(jsonb_typeof(table_json::jsonb->'headers')='array'
  AND jsonb_array_length(table_json::jsonb->'headers') BETWEEN 1 AND 64
  AND jsonb_typeof(table_json::jsonb->'rows')='array'
  AND jsonb_array_length(table_json::jsonb->'rows') BETWEEN 1 AND 5000);

DO $$ DECLARE name TEXT;BEGIN
 FOR name IN SELECT c.conname FROM pg_constraint c
  JOIN pg_class t ON t.oid=c.conrelid JOIN pg_namespace n ON n.oid=t.relnamespace
  WHERE n.nspname='accounting' AND t.relname='bank_import_previews' AND c.contype='c'
  AND pg_get_constraintdef(c.oid) LIKE '%payload_json%' LOOP
  EXECUTE format('ALTER TABLE accounting.bank_import_previews DROP CONSTRAINT %I',name);
 END LOOP;
END $$;
ALTER TABLE accounting.bank_import_previews ADD CONSTRAINT bank_import_previews_payload_json_check
 CHECK(jsonb_typeof(payload_json::jsonb->'rows')='array'
  AND jsonb_array_length(payload_json::jsonb->'rows') BETWEEN 1 AND 5000);

DO $$ DECLARE name TEXT;BEGIN
 FOR name IN SELECT c.conname FROM pg_constraint c
  JOIN pg_class t ON t.oid=c.conrelid JOIN pg_namespace n ON n.oid=t.relnamespace
  WHERE n.nspname='accounting' AND t.relname='bank_import_source_files' AND c.contype='c'
  AND pg_get_constraintdef(c.oid) LIKE '%row_number%' LOOP
  EXECUTE format('ALTER TABLE accounting.bank_import_source_files DROP CONSTRAINT %I',name);
 END LOOP;
END $$;
ALTER TABLE accounting.bank_import_source_files ADD CONSTRAINT bank_import_source_files_row_number_check
 CHECK(row_number BETWEEN 2 AND 10000000);
