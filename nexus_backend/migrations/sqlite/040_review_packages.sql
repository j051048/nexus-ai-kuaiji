CREATE TABLE review_package_requests (
 id TEXT PRIMARY KEY,firm_id TEXT NOT NULL,created_by TEXT NOT NULL,idempotency_key TEXT NOT NULL,
 parent_preview_id TEXT REFERENCES review_package_requests(id),
 request_json TEXT NOT NULL CHECK(json_valid(request_json) AND json_type(request_json)='object'),
 request_fingerprint TEXT NOT NULL CHECK(length(request_fingerprint)=64),created_at TEXT NOT NULL,
 UNIQUE(firm_id,created_by,idempotency_key),
 FOREIGN KEY(id,firm_id,created_by) REFERENCES review_previews(id,firm_id,created_by)
);
CREATE INDEX review_package_owner ON review_package_requests(firm_id,created_by,created_at DESC);
CREATE INDEX review_package_parent ON review_package_requests(parent_preview_id);
CREATE TRIGGER review_package_no_update BEFORE UPDATE ON review_package_requests BEGIN SELECT RAISE(ABORT,'Package evidence is immutable');END;
CREATE TRIGGER review_package_no_delete BEFORE DELETE ON review_package_requests BEGIN SELECT RAISE(ABORT,'Package evidence is immutable');END;
CREATE TRIGGER review_package_valid BEFORE INSERT ON review_package_requests BEGIN
 SELECT CASE WHEN json_type(NEW.request_json,'$.items') IS NOT 'array'
 OR json_extract(NEW.request_json,'$.parent_preview_id') IS NOT NEW.parent_preview_id
 OR NOT EXISTS(SELECT 1 FROM review_previews p WHERE p.id=NEW.id AND p.firm_id=NEW.firm_id AND p.created_by=NEW.created_by AND p.request_fingerprint=NEW.request_fingerprint)
 OR (NEW.parent_preview_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM review_package_requests p WHERE p.id=NEW.parent_preview_id AND p.firm_id=NEW.firm_id AND p.created_by=NEW.created_by))
 THEN RAISE(ABORT,'Invalid package reviewer or parent') END;
 SELECT CASE WHEN json_array_length(NEW.request_json,'$.items')<>(SELECT json_array_length(snapshot_json) FROM review_previews WHERE id=NEW.id)
 OR json_array_length(NEW.request_json,'$.items')<>(SELECT count(DISTINCT json_extract(value,'$.draft_id')) FROM json_each(NEW.request_json,'$.items'))
 OR EXISTS(SELECT 1 FROM json_each(NEW.request_json,'$.items') i WHERE NOT EXISTS(
  SELECT 1 FROM review_previews p,json_each(p.snapshot_json) s WHERE p.id=NEW.id
  AND json_extract(s.value,'$.draft_id')=json_extract(i.value,'$.draft_id') AND json_extract(s.value,'$.book_id')=json_extract(i.value,'$.book_id') AND json_extract(s.value,'$.expected_version')=json_extract(i.value,'$.expected_version')))
 OR (NEW.parent_preview_id IS NOT NULL AND EXISTS(SELECT 1 FROM json_each(NEW.request_json,'$.items') i WHERE NOT EXISTS(
  SELECT 1 FROM review_previews p,json_each(p.snapshot_json) s WHERE p.id=NEW.parent_preview_id
  AND json_extract(s.value,'$.draft_id')=json_extract(i.value,'$.draft_id') AND json_extract(s.value,'$.book_id')=json_extract(i.value,'$.book_id') AND json_extract(s.value,'$.expected_version')=json_extract(i.value,'$.expected_version'))))
 THEN RAISE(ABORT,'Package selection mismatch') END;
END;
