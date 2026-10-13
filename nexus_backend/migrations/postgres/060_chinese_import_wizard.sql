CREATE TABLE accounting.bank_import_files(
 id TEXT PRIMARY KEY,book_id TEXT NOT NULL,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,
 storage_key TEXT NOT NULL UNIQUE CHECK(storage_key~'^[0-9a-f]{2}/[0-9a-f]{32}$'),filename TEXT NOT NULL CHECK(length(filename) BETWEEN 1 AND 150),media_type TEXT NOT NULL CHECK(media_type IN ('text/csv','application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')),
 sha256 TEXT NOT NULL CHECK(sha256~'^[0-9a-f]{64}$'),size_bytes INTEGER NOT NULL CHECK(size_bytes BETWEEN 1 AND 2097152),
 table_json TEXT NOT NULL CHECK(jsonb_typeof(table_json::jsonb->'headers')='array' AND jsonb_array_length(table_json::jsonb->'headers') BETWEEN 1 AND 64 AND jsonb_typeof(table_json::jsonb->'rows')='array' AND jsonb_array_length(table_json::jsonb->'rows') BETWEEN 1 AND 1000),
 created_by TEXT NOT NULL REFERENCES accounting.users(id),created_at TEXT NOT NULL,
 UNIQUE(id,book_id,firm_id,company_id),FOREIGN KEY(book_id,firm_id,company_id) REFERENCES accounting.books(id,firm_id,company_id)
);
CREATE TABLE accounting.bank_import_previews(
 id TEXT PRIMARY KEY,book_id TEXT NOT NULL,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,file_id TEXT NOT NULL,
 payload_json TEXT NOT NULL CHECK(jsonb_typeof(payload_json::jsonb->'rows')='array' AND jsonb_array_length(payload_json::jsonb->'rows') BETWEEN 1 AND 1000),fingerprint TEXT NOT NULL CHECK(fingerprint~'^[0-9a-f]{64}$'),
 created_by TEXT NOT NULL REFERENCES accounting.users(id),created_at TEXT NOT NULL,
 UNIQUE(id,book_id,firm_id,company_id),FOREIGN KEY(file_id,book_id,firm_id,company_id) REFERENCES accounting.bank_import_files(id,book_id,firm_id,company_id)
);
CREATE TABLE accounting.bank_import_confirmations(
 preview_id TEXT PRIMARY KEY,book_id TEXT NOT NULL,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,result_json TEXT NOT NULL,
 created_by TEXT NOT NULL REFERENCES accounting.users(id),created_at TEXT NOT NULL,
 FOREIGN KEY(preview_id,book_id,firm_id,company_id) REFERENCES accounting.bank_import_previews(id,book_id,firm_id,company_id)
);
CREATE TABLE accounting.bank_import_source_files(
 preview_id TEXT NOT NULL,file_id TEXT NOT NULL,book_id TEXT NOT NULL,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,source_id TEXT NOT NULL,row_number INTEGER NOT NULL CHECK(row_number BETWEEN 2 AND 1001),
 PRIMARY KEY(preview_id,row_number),FOREIGN KEY(preview_id,book_id,firm_id,company_id) REFERENCES accounting.bank_import_previews(id,book_id,firm_id,company_id),
 FOREIGN KEY(file_id,book_id,firm_id,company_id) REFERENCES accounting.bank_import_files(id,book_id,firm_id,company_id),FOREIGN KEY(source_id,book_id) REFERENCES accounting.sources(id,book_id)
);
CREATE TABLE accounting.bank_import_profiles(
 id TEXT PRIMARY KEY,book_id TEXT NOT NULL,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,namespace TEXT NOT NULL CHECK(length(btrim(namespace)) BETWEEN 1 AND 100),
 bank_account TEXT NOT NULL CHECK(length(btrim(bank_account)) BETWEEN 1 AND 100),version INTEGER NOT NULL CHECK(version>0),headers_json TEXT NOT NULL CHECK(jsonb_typeof(headers_json::jsonb)='array' AND jsonb_array_length(headers_json::jsonb) BETWEEN 1 AND 64),
 mapping_json TEXT NOT NULL CHECK(jsonb_typeof(mapping_json::jsonb->'columns')='object'),created_by TEXT NOT NULL REFERENCES accounting.users(id),created_at TEXT NOT NULL,
 UNIQUE(book_id,namespace,bank_account,version),FOREIGN KEY(book_id,firm_id,company_id) REFERENCES accounting.books(id,firm_id,company_id)
);
CREATE INDEX idx_import_files_book ON accounting.bank_import_files(book_id,created_by,created_at);
CREATE INDEX idx_import_source_evidence ON accounting.bank_import_source_files(book_id,source_id);
CREATE INDEX idx_import_profile_scope ON accounting.bank_import_profiles(book_id,namespace,bank_account,version);

CREATE FUNCTION accounting.import_wizard_guard() RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE v_preview accounting.bank_import_previews;v_file accounting.bank_import_files;v_row JSONB;v_request JSONB;v_source accounting.sources;v_key accounting.bank_import_keys;
 BEGIN
 IF TG_OP<>'INSERT' THEN RAISE EXCEPTION 'Statement originals, previews, lineage and receipts are immutable' USING ERRCODE='23514';END IF;
 IF NEW.firm_id<>accounting.actor_firm() OR NOT accounting.book_allowed(NEW.book_id,true) OR NOT EXISTS(SELECT 1 FROM accounting.books b JOIN accounting.periods p ON p.book_id=b.id AND p.period=b.period WHERE b.id=NEW.book_id AND b.firm_id=NEW.firm_id AND b.company_id=NEW.company_id AND p.status='open') THEN RAISE EXCEPTION 'Statement import scope denied' USING ERRCODE='42501';END IF;
 IF TG_TABLE_NAME<>'bank_import_source_files' THEN
  IF NEW.created_by<>accounting.actor_user() THEN RAISE EXCEPTION 'Statement import author mismatch' USING ERRCODE='42501';END IF;
 END IF;
 IF TG_TABLE_NAME='bank_import_previews' THEN
  SELECT * INTO v_file FROM accounting.bank_import_files WHERE id=NEW.file_id AND book_id=NEW.book_id;
  IF NOT FOUND OR NEW.payload_json::jsonb->'request'->>'file_id' IS DISTINCT FROM NEW.file_id OR jsonb_array_length(NEW.payload_json::jsonb->'rows')<>jsonb_array_length(v_file.table_json::jsonb->'rows') THEN RAISE EXCEPTION 'Statement preview must retain every original row' USING ERRCODE='23514';END IF;
 ELSIF TG_TABLE_NAME='bank_import_source_files' THEN
  SELECT * INTO v_preview FROM accounting.bank_import_previews WHERE id=NEW.preview_id AND book_id=NEW.book_id;
  v_request:=v_preview.payload_json::jsonb->'request';
  SELECT value INTO v_row FROM jsonb_array_elements(v_preview.payload_json::jsonb->'rows') WHERE (value->>'line')::integer=NEW.row_number;
  SELECT * INTO v_source FROM accounting.sources WHERE id=NEW.source_id AND book_id=NEW.book_id;
  SELECT * INTO v_key FROM accounting.bank_import_keys WHERE source_id=NEW.source_id AND book_id=NEW.book_id;
  IF v_preview.file_id IS DISTINCT FROM NEW.file_id OR v_row IS NULL OR jsonb_array_length(v_row->'errors')<>0 OR v_row->'source'->>'stable_transaction_id' IS DISTINCT FROM v_key.stable_transaction_id OR v_request->>'namespace' IS DISTINCT FROM v_key.namespace OR v_request->>'bank_account' IS DISTINCT FROM v_key.bank_account OR v_row->'source'->>'date' IS DISTINCT FROM v_source.date OR v_row->'source'->>'description' IS DISTINCT FROM v_source.description OR v_row->'source'->>'counterparty' IS DISTINCT FROM v_source.counterparty OR accounting.domain_money_cents(v_row->'source'->>'amount') IS DISTINCT FROM v_source.amount_cents OR v_row->'source'->>'direction' IS DISTINCT FROM v_source.direction OR v_row->'source'->>'evidence_ref' IS DISTINCT FROM v_source.evidence_ref THEN RAISE EXCEPTION 'Statement lineage must match frozen canonical facts' USING ERRCODE='23514';END IF;
 ELSIF TG_TABLE_NAME='bank_import_confirmations' THEN
  SELECT * INTO v_preview FROM accounting.bank_import_previews WHERE id=NEW.preview_id AND book_id=NEW.book_id;
  IF NEW.result_json::jsonb->>'preview_id' IS DISTINCT FROM NEW.preview_id OR NEW.result_json::jsonb->>'book_id' IS DISTINCT FROM NEW.book_id OR (SELECT count(*) FROM accounting.bank_import_source_files WHERE preview_id=NEW.preview_id AND book_id=NEW.book_id)<>jsonb_array_length(v_preview.payload_json::jsonb->'rows') THEN RAISE EXCEPTION 'Statement receipt needs complete source lineage' USING ERRCODE='23514';END IF;
 END IF;
 RETURN NEW;END $$;
REVOKE ALL ON FUNCTION accounting.import_wizard_guard() FROM PUBLIC,accounting_app,accounting_worker;
DO $$ DECLARE v_name TEXT;BEGIN
 FOREACH v_name IN ARRAY ARRAY['bank_import_files','bank_import_previews','bank_import_confirmations','bank_import_source_files','bank_import_profiles'] LOOP
  EXECUTE format('ALTER TABLE accounting.%I ENABLE ROW LEVEL SECURITY',v_name);
  EXECUTE format('ALTER TABLE accounting.%I FORCE ROW LEVEL SECURITY',v_name);
  EXECUTE format('REVOKE ALL ON accounting.%I FROM PUBLIC,accounting_worker',v_name);
  EXECUTE format('GRANT SELECT,INSERT ON accounting.%I TO accounting_app',v_name);
  EXECUTE format('CREATE POLICY statement_read ON accounting.%I FOR SELECT TO accounting_app USING(firm_id=accounting.actor_firm() AND accounting.book_allowed(book_id))',v_name);
  EXECUTE format('CREATE POLICY statement_write ON accounting.%I FOR INSERT TO accounting_app WITH CHECK(firm_id=accounting.actor_firm() AND accounting.book_allowed(book_id,true))',v_name);
  EXECUTE format('CREATE TRIGGER statement_immutable BEFORE INSERT OR UPDATE OR DELETE ON accounting.%I FOR EACH ROW EXECUTE FUNCTION accounting.import_wizard_guard()',v_name);
 END LOOP;
END $$;
