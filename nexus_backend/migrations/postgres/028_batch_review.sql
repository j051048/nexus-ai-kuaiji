CREATE TABLE accounting.review_previews(
 id TEXT PRIMARY KEY,firm_id TEXT NOT NULL REFERENCES accounting.firms(id),created_by TEXT NOT NULL REFERENCES accounting.users(id),
 request_fingerprint TEXT NOT NULL,snapshot_json TEXT NOT NULL CHECK(jsonb_typeof(snapshot_json::jsonb)='array' AND jsonb_array_length(snapshot_json::jsonb) BETWEEN 1 AND 100),
 created_at TEXT NOT NULL,expires_at TEXT NOT NULL,UNIQUE(id,firm_id,created_by)
);
CREATE INDEX idx_review_previews_owner ON accounting.review_previews(firm_id,created_by,created_at DESC);
CREATE TABLE accounting.review_confirmations(
 id TEXT PRIMARY KEY,preview_id TEXT NOT NULL UNIQUE,firm_id TEXT NOT NULL,created_by TEXT NOT NULL,
 idempotency_key TEXT NOT NULL,created_at TEXT NOT NULL,UNIQUE(firm_id,created_by,idempotency_key),
 FOREIGN KEY(preview_id,firm_id,created_by) REFERENCES accounting.review_previews(id,firm_id,created_by)
);
CREATE TABLE accounting.review_book_receipts(
 confirmation_id TEXT NOT NULL REFERENCES accounting.review_confirmations(id),book_id TEXT NOT NULL REFERENCES accounting.books(id),
 result_json TEXT NOT NULL CHECK(jsonb_typeof(result_json::jsonb)='object'),created_at TEXT NOT NULL,PRIMARY KEY(confirmation_id,book_id)
);
CREATE FUNCTION accounting.review_snapshot_allowed(payload TEXT) RETURNS BOOLEAN LANGUAGE sql STABLE SECURITY DEFINER
 SET search_path=pg_catalog,accounting,pg_temp AS $$
 SELECT accounting.actor_active() AND jsonb_typeof(payload::jsonb)='array' AND jsonb_array_length(payload::jsonb) BETWEEN 1 AND 100
 AND NOT EXISTS(SELECT 1 FROM jsonb_array_elements(payload::jsonb) item WHERE item->>'book_id' IS NULL OR NOT accounting.catalog_manage(item->>'book_id'))
$$;
CREATE FUNCTION accounting.review_preview_allowed(target TEXT) RETURNS BOOLEAN LANGUAGE sql STABLE SECURITY DEFINER
 SET search_path=pg_catalog,accounting,pg_temp AS $$
 SELECT EXISTS(SELECT 1 FROM accounting.review_previews p WHERE p.id=target AND p.firm_id=accounting.actor_firm()
 AND p.created_by=accounting.actor_user() AND accounting.review_snapshot_allowed(p.snapshot_json))
$$;
CREATE FUNCTION accounting.review_command_allowed(target TEXT,target_book TEXT DEFAULT NULL) RETURNS BOOLEAN LANGUAGE sql STABLE SECURITY DEFINER
 SET search_path=pg_catalog,accounting,pg_temp AS $$
 SELECT EXISTS(SELECT 1 FROM accounting.review_confirmations c JOIN accounting.review_previews p ON p.id=c.preview_id
 WHERE c.id=target AND c.firm_id=accounting.actor_firm() AND c.created_by=accounting.actor_user()
 AND accounting.review_preview_allowed(p.id) AND (target_book IS NULL OR EXISTS(
 SELECT 1 FROM jsonb_array_elements(p.snapshot_json::jsonb) item WHERE item->>'book_id'=target_book)))
$$;
CREATE FUNCTION accounting.review_immutable() RETURNS trigger LANGUAGE plpgsql
 SET search_path=pg_catalog,accounting,pg_temp AS $$ BEGIN RAISE EXCEPTION 'review evidence and receipts are immutable'; END $$;
CREATE FUNCTION accounting.review_receipt_guard() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
 SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE preview RECORD; item JSONB; selected_count INTEGER; BEGIN
 IF NEW.result_json::jsonb->>'book_id' IS DISTINCT FROM NEW.book_id OR COALESCE(NEW.result_json::jsonb->>'status','') NOT IN ('succeeded','failed')
 OR jsonb_typeof(NEW.result_json::jsonb->'approved_ids') IS DISTINCT FROM 'array'
 OR NOT accounting.review_command_allowed(NEW.confirmation_id,NEW.book_id) THEN
 RAISE EXCEPTION 'review receipt outside current authorized preview' USING ERRCODE='42501'; END IF;
 SELECT p.*,c.created_by AS reviewer INTO preview FROM accounting.review_confirmations c JOIN accounting.review_previews p ON p.id=c.preview_id WHERE c.id=NEW.confirmation_id;
 IF NEW.result_json::jsonb->>'status'='failed' THEN
  IF jsonb_array_length(NEW.result_json::jsonb->'approved_ids')<>0 THEN RAISE EXCEPTION 'failed receipt cannot claim approvals'; END IF;
 ELSE
  selected_count:=0;
  FOR item IN SELECT value FROM jsonb_array_elements(preview.snapshot_json::jsonb) WHERE value->>'book_id'=NEW.book_id LOOP
   selected_count:=selected_count+1;
   IF NOT (NEW.result_json::jsonb->'approved_ids' @> jsonb_build_array(item->>'draft_id')) OR NOT EXISTS(
    SELECT 1 FROM accounting.drafts d WHERE d.id=item->>'draft_id' AND d.book_id=NEW.book_id AND d.status='approved'
    AND d.approved_by=preview.reviewer AND d.version=(item->>'expected_version')::integer+1)
   THEN RAISE EXCEPTION 'receipt requires the complete approved book set'; END IF;
  END LOOP;
  IF selected_count<>jsonb_array_length(NEW.result_json::jsonb->'approved_ids') THEN RAISE EXCEPTION 'receipt approval set mismatch'; END IF;
 END IF;
 RETURN NEW; END $$;
REVOKE ALL ON FUNCTION accounting.review_snapshot_allowed(TEXT),accounting.review_preview_allowed(TEXT),accounting.review_command_allowed(TEXT,TEXT),accounting.review_immutable(),accounting.review_receipt_guard() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION accounting.review_snapshot_allowed(TEXT),accounting.review_preview_allowed(TEXT),accounting.review_command_allowed(TEXT,TEXT) TO accounting_app;
CREATE TRIGGER review_preview_immutable BEFORE UPDATE OR DELETE ON accounting.review_previews FOR EACH ROW EXECUTE FUNCTION accounting.review_immutable();
CREATE TRIGGER review_confirmation_immutable BEFORE UPDATE OR DELETE ON accounting.review_confirmations FOR EACH ROW EXECUTE FUNCTION accounting.review_immutable();
CREATE TRIGGER review_receipt_immutable BEFORE UPDATE OR DELETE ON accounting.review_book_receipts FOR EACH ROW EXECUTE FUNCTION accounting.review_immutable();
CREATE TRIGGER review_receipt_valid BEFORE INSERT ON accounting.review_book_receipts FOR EACH ROW EXECUTE FUNCTION accounting.review_receipt_guard();
ALTER TABLE accounting.review_previews ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.review_previews FORCE ROW LEVEL SECURITY;
ALTER TABLE accounting.review_confirmations ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.review_confirmations FORCE ROW LEVEL SECURITY;
ALTER TABLE accounting.review_book_receipts ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.review_book_receipts FORCE ROW LEVEL SECURITY;
REVOKE ALL ON accounting.review_previews,accounting.review_confirmations,accounting.review_book_receipts FROM PUBLIC;
GRANT SELECT,INSERT ON accounting.review_previews,accounting.review_confirmations,accounting.review_book_receipts TO accounting_app;
CREATE POLICY review_preview_read ON accounting.review_previews FOR SELECT TO accounting_app USING(firm_id=accounting.actor_firm() AND created_by=accounting.actor_user() AND accounting.review_snapshot_allowed(snapshot_json));
CREATE POLICY review_preview_create ON accounting.review_previews FOR INSERT TO accounting_app WITH CHECK(firm_id=accounting.actor_firm() AND created_by=accounting.actor_user() AND accounting.review_snapshot_allowed(snapshot_json));
CREATE POLICY review_confirmation_read ON accounting.review_confirmations FOR SELECT TO accounting_app USING(accounting.review_preview_allowed(preview_id));
CREATE POLICY review_confirmation_create ON accounting.review_confirmations FOR INSERT TO accounting_app WITH CHECK(firm_id=accounting.actor_firm() AND created_by=accounting.actor_user() AND accounting.review_preview_allowed(preview_id));
CREATE POLICY review_receipt_read ON accounting.review_book_receipts FOR SELECT TO accounting_app USING(accounting.review_command_allowed(confirmation_id,book_id));
CREATE POLICY review_receipt_create ON accounting.review_book_receipts FOR INSERT TO accounting_app WITH CHECK(accounting.review_command_allowed(confirmation_id,book_id));
