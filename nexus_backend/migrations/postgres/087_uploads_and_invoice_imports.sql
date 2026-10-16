-- Large-file upload intents and invoice export-table batches. Both are
-- infrastructure/evidence records: they never create accounting facts, and the
-- intent row is owned by exactly one active actor of one firm.
CREATE TABLE accounting.upload_intents(
 id TEXT PRIMARY KEY,firm_id TEXT NOT NULL REFERENCES accounting.firms(id),user_id TEXT NOT NULL REFERENCES accounting.users(id),
 purpose TEXT NOT NULL CHECK(purpose IN('intake_attachment','event_attachment','case_attachment','material_attachment','invoice_import')),
 filename TEXT NOT NULL CHECK(length(filename) BETWEEN 1 AND 150),
 media_type TEXT NOT NULL CHECK(length(media_type) BETWEEN 1 AND 120),
 size_bytes INTEGER NOT NULL CHECK(size_bytes BETWEEN 1 AND 10485760),
 sha256 TEXT NOT NULL CHECK(sha256~'^[a-f0-9]{64}$'),
 storage_key TEXT NOT NULL UNIQUE,
 state TEXT NOT NULL DEFAULT 'pending' CHECK(state IN('pending','stored','consuming','consumed','discarded')),
 created_at TEXT NOT NULL,expires_at TEXT NOT NULL,consumed_ref TEXT
);
CREATE INDEX idx_upload_intents_owner ON accounting.upload_intents(firm_id,user_id,state,created_at);
CREATE TABLE accounting.invoice_import_batches(
 id TEXT PRIMARY KEY,book_id TEXT NOT NULL,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,
 filename TEXT NOT NULL CHECK(length(filename) BETWEEN 1 AND 150),
 storage_key TEXT NOT NULL UNIQUE,sha256 TEXT NOT NULL CHECK(sha256~'^[a-f0-9]{64}$'),
 size_bytes INTEGER NOT NULL CHECK(size_bytes BETWEEN 1 AND 10485760),
 row_count INTEGER NOT NULL CHECK(row_count BETWEEN 0 AND 5000),
 direction TEXT NOT NULL CHECK(direction IN('input','output','mixed')),
 rows_json TEXT NOT NULL,notes_json TEXT NOT NULL DEFAULT '[]',
 created_by TEXT NOT NULL REFERENCES accounting.users(id),created_at TEXT NOT NULL,
 FOREIGN KEY(book_id,firm_id,company_id) REFERENCES accounting.books(id,firm_id,company_id)
);
CREATE INDEX idx_invoice_batches_book ON accounting.invoice_import_batches(book_id,created_at,id);

-- An intent belongs to its uploader; identity columns never change and only the
-- reviewed state transitions are accepted. Rows are never deleted.
CREATE FUNCTION accounting.upload_intents_native_guard() RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
BEGIN
 IF TG_OP='INSERT' THEN
  IF NEW.firm_id<>accounting.actor_firm() OR NEW.user_id<>accounting.actor_user() OR NOT accounting.actor_active()
   OR NEW.state<>'pending' OR NEW.consumed_ref IS NOT NULL THEN RAISE EXCEPTION 'Own upload intent required' USING ERRCODE='42501';END IF;
  RETURN NEW;
 ELSIF TG_OP='UPDATE' THEN
  IF NEW.id<>OLD.id OR NEW.firm_id<>OLD.firm_id OR NEW.user_id<>OLD.user_id OR NEW.purpose<>OLD.purpose
   OR NEW.filename<>OLD.filename OR NEW.media_type<>OLD.media_type OR NEW.size_bytes<>OLD.size_bytes
   OR NEW.sha256<>OLD.sha256 OR NEW.storage_key<>OLD.storage_key OR NEW.expires_at<>OLD.expires_at
   OR NEW.user_id<>accounting.actor_user() OR NOT accounting.actor_active()
   THEN RAISE EXCEPTION 'Upload intent identity is immutable' USING ERRCODE='42501';END IF;
  IF NOT ((OLD.state='pending' AND NEW.state IN('stored','discarded'))
   OR (OLD.state='stored' AND NEW.state IN('consuming','discarded'))
   OR (OLD.state='consuming' AND NEW.state IN('consumed','stored','discarded'))
   OR (OLD.state IN('consumed','discarded') AND NEW.state=OLD.state))
   THEN RAISE EXCEPTION 'Upload intent transition is invalid' USING ERRCODE='23514';END IF;
  RETURN NEW;
 END IF;
 RAISE EXCEPTION 'Upload intents are never deleted' USING ERRCODE='42501';
END $$;
CREATE TRIGGER upload_intents_native BEFORE INSERT OR UPDATE OR DELETE ON accounting.upload_intents FOR EACH ROW EXECUTE FUNCTION accounting.upload_intents_native_guard();

DO $$ DECLARE tab TEXT;scope TEXT;BEGIN
 FOREACH tab IN ARRAY ARRAY['upload_intents','invoice_import_batches'] LOOP
  EXECUTE format('ALTER TABLE accounting.%I ENABLE ROW LEVEL SECURITY',tab);EXECUTE format('ALTER TABLE accounting.%I FORCE ROW LEVEL SECURITY',tab);
  EXECUTE format('REVOKE ALL ON accounting.%I FROM PUBLIC,accounting_app,accounting_worker',tab);
  EXECUTE format('CREATE TRIGGER batch2_company_write_gate BEFORE INSERT OR UPDATE OR DELETE ON accounting.%I FOR EACH ROW EXECUTE FUNCTION accounting.company_write_table_guard()',tab);
  EXECUTE format('CREATE TRIGGER batch2_statement_write_gate BEFORE INSERT OR UPDATE OR DELETE ON accounting.%I FOR EACH STATEMENT EXECUTE FUNCTION accounting.compatibility_statement_gate()',tab);
 END LOOP;
END $$;
REVOKE ALL ON accounting.upload_intents,accounting.invoice_import_batches FROM PUBLIC,accounting_app,accounting_worker;
GRANT SELECT,INSERT,UPDATE ON accounting.upload_intents TO accounting_app;
GRANT SELECT,INSERT ON accounting.invoice_import_batches TO accounting_app;
CREATE POLICY uploads_owner ON accounting.upload_intents FOR ALL TO accounting_app
 USING(firm_id=accounting.actor_firm() AND user_id=accounting.actor_user())
 WITH CHECK(firm_id=accounting.actor_firm() AND user_id=accounting.actor_user());
CREATE POLICY invoice_batches_read ON accounting.invoice_import_batches FOR SELECT TO accounting_app
 USING(firm_id=accounting.actor_firm() AND accounting.service_book_staff(book_id));
CREATE POLICY invoice_batches_insert ON accounting.invoice_import_batches FOR INSERT TO accounting_app
 WITH CHECK(firm_id=accounting.actor_firm() AND accounting.service_book_staff(book_id));
CREATE TRIGGER invoice_batches_immutable BEFORE UPDATE OR DELETE ON accounting.invoice_import_batches FOR EACH ROW EXECUTE FUNCTION accounting.ai_immutable_guard();
REVOKE ALL ON FUNCTION accounting.upload_intents_native_guard() FROM PUBLIC,accounting_app,accounting_worker;
