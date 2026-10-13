CREATE TABLE accounting.review_package_requests (
 id TEXT PRIMARY KEY,firm_id TEXT NOT NULL,created_by TEXT NOT NULL,idempotency_key TEXT NOT NULL,
 parent_preview_id TEXT REFERENCES accounting.review_package_requests(id),
 request_json TEXT NOT NULL CHECK(jsonb_typeof(request_json::jsonb)='object'),
 request_fingerprint TEXT NOT NULL CHECK(length(request_fingerprint)=64),created_at TEXT NOT NULL,
 UNIQUE(firm_id,created_by,idempotency_key),
 FOREIGN KEY(id,firm_id,created_by) REFERENCES accounting.review_previews(id,firm_id,created_by)
);
CREATE INDEX review_package_owner ON accounting.review_package_requests(firm_id,created_by,created_at DESC);
CREATE INDEX review_package_parent ON accounting.review_package_requests(parent_preview_id);
CREATE FUNCTION accounting.review_package_valid() RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER
 SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE target_snapshot JSONB; parent_snapshot JSONB; doc JSONB; item JSONB; BEGIN
 IF NEW.firm_id IS DISTINCT FROM accounting.actor_firm() OR NEW.created_by IS DISTINCT FROM accounting.actor_user()
 OR NOT accounting.review_preview_allowed(NEW.id) THEN RAISE EXCEPTION 'Package outside current reviewer' USING ERRCODE='42501';END IF;
 SELECT snapshot_json::jsonb INTO target_snapshot FROM accounting.review_previews WHERE id=NEW.id AND request_fingerprint=NEW.request_fingerprint;
 doc:=NEW.request_json::jsonb;
 IF target_snapshot IS NULL OR jsonb_typeof(doc->'items') IS DISTINCT FROM 'array'
 OR doc->>'parent_preview_id' IS DISTINCT FROM NEW.parent_preview_id
 OR NEW.request_fingerprint IS DISTINCT FROM encode(sha256(convert_to(NEW.request_json,'UTF8')),'hex')
 THEN RAISE EXCEPTION 'Invalid immutable package request' USING ERRCODE='23514';END IF;
 IF jsonb_array_length(doc->'items')<>jsonb_array_length(target_snapshot)
 OR jsonb_array_length(doc->'items')<>(SELECT count(DISTINCT value->>'draft_id') FROM jsonb_array_elements(doc->'items'))
 THEN RAISE EXCEPTION 'Package selection is not distinct' USING ERRCODE='23514';END IF;
 IF NEW.parent_preview_id IS NOT NULL THEN
  IF NOT accounting.review_preview_allowed(NEW.parent_preview_id) THEN RAISE EXCEPTION 'Parent package outside current reviewer' USING ERRCODE='42501';END IF;
  SELECT snapshot_json::jsonb INTO parent_snapshot FROM accounting.review_previews WHERE id=NEW.parent_preview_id;
 END IF;
 FOR item IN SELECT value FROM jsonb_array_elements(doc->'items') LOOP
  IF NOT EXISTS(SELECT 1 FROM jsonb_array_elements(target_snapshot) s WHERE s->>'draft_id'=item->>'draft_id' AND s->>'book_id'=item->>'book_id' AND s->'expected_version'=item->'expected_version')
  OR (NEW.parent_preview_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM jsonb_array_elements(parent_snapshot) s WHERE s->>'draft_id'=item->>'draft_id' AND s->>'book_id'=item->>'book_id' AND s->'expected_version'=item->'expected_version'))
  THEN RAISE EXCEPTION 'Package cannot change the frozen selection' USING ERRCODE='23514';END IF;
 END LOOP;
 RETURN NEW;END $$;
REVOKE ALL ON FUNCTION accounting.review_package_valid() FROM PUBLIC,accounting_app,accounting_worker;
CREATE TRIGGER review_package_valid BEFORE INSERT ON accounting.review_package_requests FOR EACH ROW EXECUTE FUNCTION accounting.review_package_valid();
CREATE TRIGGER review_package_immutable BEFORE UPDATE OR DELETE ON accounting.review_package_requests FOR EACH ROW EXECUTE FUNCTION accounting.review_immutable();
ALTER TABLE accounting.review_package_requests ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.review_package_requests FORCE ROW LEVEL SECURITY;
REVOKE ALL ON accounting.review_package_requests FROM PUBLIC,accounting_worker;
GRANT SELECT,INSERT ON accounting.review_package_requests TO accounting_app;
CREATE POLICY review_package_read ON accounting.review_package_requests FOR SELECT TO accounting_app USING(firm_id=accounting.actor_firm() AND created_by=accounting.actor_user() AND accounting.review_preview_allowed(id));
CREATE POLICY review_package_create ON accounting.review_package_requests FOR INSERT TO accounting_app WITH CHECK(firm_id=accounting.actor_firm() AND created_by=accounting.actor_user() AND accounting.review_preview_allowed(id));
