CREATE TABLE review_previews(
 id TEXT PRIMARY KEY,firm_id TEXT NOT NULL REFERENCES firms(id),created_by TEXT NOT NULL REFERENCES users(id),
 request_fingerprint TEXT NOT NULL,snapshot_json TEXT NOT NULL CHECK(json_valid(snapshot_json) AND json_type(snapshot_json)='array' AND json_array_length(snapshot_json) BETWEEN 1 AND 100),
 created_at TEXT NOT NULL,expires_at TEXT NOT NULL,UNIQUE(id,firm_id,created_by)
);
CREATE INDEX idx_review_previews_owner ON review_previews(firm_id,created_by,created_at DESC);
CREATE TABLE review_confirmations(
 id TEXT PRIMARY KEY,preview_id TEXT NOT NULL UNIQUE,firm_id TEXT NOT NULL,created_by TEXT NOT NULL,
 idempotency_key TEXT NOT NULL,created_at TEXT NOT NULL,
 UNIQUE(firm_id,created_by,idempotency_key),
 FOREIGN KEY(preview_id,firm_id,created_by) REFERENCES review_previews(id,firm_id,created_by)
);
CREATE TABLE review_book_receipts(
 confirmation_id TEXT NOT NULL REFERENCES review_confirmations(id),book_id TEXT NOT NULL REFERENCES books(id),
 result_json TEXT NOT NULL CHECK(json_valid(result_json) AND json_type(result_json)='object'),created_at TEXT NOT NULL,
 PRIMARY KEY(confirmation_id,book_id)
);
CREATE TRIGGER review_previews_no_update BEFORE UPDATE ON review_previews BEGIN SELECT RAISE(ABORT,'review preview is immutable'); END;
CREATE TRIGGER review_previews_no_delete BEFORE DELETE ON review_previews BEGIN SELECT RAISE(ABORT,'review preview is immutable'); END;
CREATE TRIGGER review_confirmations_no_update BEFORE UPDATE ON review_confirmations BEGIN SELECT RAISE(ABORT,'review command is immutable'); END;
CREATE TRIGGER review_confirmations_no_delete BEFORE DELETE ON review_confirmations BEGIN SELECT RAISE(ABORT,'review command is immutable'); END;
CREATE TRIGGER review_book_receipts_no_update BEFORE UPDATE ON review_book_receipts BEGIN SELECT RAISE(ABORT,'review receipt is immutable'); END;
CREATE TRIGGER review_book_receipts_no_delete BEFORE DELETE ON review_book_receipts BEGIN SELECT RAISE(ABORT,'review receipt is immutable'); END;
CREATE TRIGGER review_receipt_scope BEFORE INSERT ON review_book_receipts BEGIN
 SELECT CASE WHEN json_extract(NEW.result_json,'$.book_id') IS NOT NEW.book_id
 OR COALESCE(json_extract(NEW.result_json,'$.status'),'') NOT IN ('succeeded','failed')
 OR json_type(NEW.result_json,'$.approved_ids') IS NOT 'array'
 OR NOT EXISTS(SELECT 1 FROM review_confirmations c JOIN review_previews p ON p.id=c.preview_id JOIN json_each(p.snapshot_json) item WHERE c.id=NEW.confirmation_id AND json_extract(item.value,'$.book_id')=NEW.book_id)
 THEN RAISE(ABORT,'review receipt outside immutable preview') END;
 SELECT CASE WHEN json_extract(NEW.result_json,'$.status')='failed' AND json_array_length(NEW.result_json,'$.approved_ids')<>0
 THEN RAISE(ABORT,'failed review receipt cannot claim approvals') END;
 SELECT CASE WHEN json_extract(NEW.result_json,'$.status')='succeeded' AND (
  json_array_length(NEW.result_json,'$.approved_ids')<>(SELECT count(*) FROM review_confirmations c JOIN review_previews p ON p.id=c.preview_id JOIN json_each(p.snapshot_json) item WHERE c.id=NEW.confirmation_id AND json_extract(item.value,'$.book_id')=NEW.book_id)
  OR EXISTS(SELECT 1 FROM review_confirmations c JOIN review_previews p ON p.id=c.preview_id JOIN json_each(p.snapshot_json) item
    WHERE c.id=NEW.confirmation_id AND json_extract(item.value,'$.book_id')=NEW.book_id AND NOT EXISTS(
     SELECT 1 FROM drafts d JOIN json_each(NEW.result_json,'$.approved_ids') approved ON approved.value=d.id
     WHERE d.id=json_extract(item.value,'$.draft_id') AND d.book_id=NEW.book_id AND d.status='approved'
     AND d.approved_by=c.created_by AND d.version=json_extract(item.value,'$.expected_version')+1)))
 THEN RAISE(ABORT,'successful receipt requires the complete approved book set') END;
END;
