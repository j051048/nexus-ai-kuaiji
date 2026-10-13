CREATE TABLE delivery_manifests(
 export_id TEXT PRIMARY KEY,book_id TEXT NOT NULL,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,period TEXT NOT NULL,
 origin TEXT NOT NULL CHECK(origin IN ('current_export','legacy_audit')),payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),
 created_by TEXT NOT NULL REFERENCES users(id),created_at TEXT NOT NULL,
 FOREIGN KEY(export_id,book_id) REFERENCES exports(id,book_id),FOREIGN KEY(book_id,firm_id,company_id) REFERENCES books(id,firm_id,company_id)
);
CREATE TABLE delivery_receipts(
 id TEXT PRIMARY KEY,export_id TEXT NOT NULL,book_id TEXT NOT NULL,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,
 body_json TEXT NOT NULL CHECK(json_valid(body_json)),request_fingerprint TEXT NOT NULL,idempotency_key TEXT NOT NULL,
 reason TEXT NOT NULL,import_format TEXT NOT NULL CHECK(import_format IN ('json','csv','structured')),raw_hash TEXT NOT NULL,
 created_by TEXT NOT NULL REFERENCES users(id),created_at TEXT NOT NULL,UNIQUE(id,book_id,firm_id,company_id),UNIQUE(firm_id,created_by,idempotency_key),
 FOREIGN KEY(export_id,book_id) REFERENCES exports(id,book_id),FOREIGN KEY(book_id,firm_id,company_id) REFERENCES books(id,firm_id,company_id)
);
CREATE TABLE delivery_receipt_items(
 receipt_id TEXT NOT NULL,book_id TEXT NOT NULL,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,ordinal INTEGER NOT NULL CHECK(ordinal BETWEEN 0 AND 999),
 claimed_draft_id TEXT NOT NULL,external_voucher_id TEXT,raw_json TEXT NOT NULL CHECK(json_valid(raw_json)),PRIMARY KEY(receipt_id,ordinal),
 FOREIGN KEY(receipt_id,book_id,firm_id,company_id) REFERENCES delivery_receipts(id,book_id,firm_id,company_id)
);
CREATE TABLE delivery_notes(
 id TEXT PRIMARY KEY,receipt_id TEXT NOT NULL,book_id TEXT NOT NULL,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,
 draft_id TEXT NOT NULL,issue_key TEXT NOT NULL,reason TEXT NOT NULL,request_fingerprint TEXT NOT NULL,idempotency_key TEXT NOT NULL,
 created_by TEXT NOT NULL REFERENCES users(id),created_at TEXT NOT NULL,UNIQUE(firm_id,created_by,idempotency_key),
 FOREIGN KEY(receipt_id,book_id,firm_id,company_id) REFERENCES delivery_receipts(id,book_id,firm_id,company_id)
);
CREATE UNIQUE INDEX idx_exports_delivery_scope ON exports(id,book_id);
CREATE INDEX idx_delivery_receipts_page ON delivery_receipts(book_id,export_id,created_at,id);
CREATE INDEX idx_delivery_items_draft ON delivery_receipt_items(book_id,claimed_draft_id,external_voucher_id);
CREATE INDEX idx_delivery_items_external ON delivery_receipt_items(book_id,external_voucher_id,claimed_draft_id);
CREATE INDEX idx_delivery_notes ON delivery_notes(book_id,receipt_id,draft_id);
CREATE TRIGGER delivery_manifest_no_update BEFORE UPDATE ON delivery_manifests BEGIN SELECT RAISE(ABORT,'delivery manifest is immutable'); END;
CREATE TRIGGER delivery_manifest_no_delete BEFORE DELETE ON delivery_manifests BEGIN SELECT RAISE(ABORT,'delivery manifest is immutable'); END;
CREATE TRIGGER delivery_receipt_no_update BEFORE UPDATE ON delivery_receipts BEGIN SELECT RAISE(ABORT,'delivery receipt is immutable'); END;
CREATE TRIGGER delivery_receipt_no_delete BEFORE DELETE ON delivery_receipts BEGIN SELECT RAISE(ABORT,'delivery receipt is immutable'); END;
CREATE TRIGGER delivery_item_no_update BEFORE UPDATE ON delivery_receipt_items BEGIN SELECT RAISE(ABORT,'delivery fact is immutable'); END;
CREATE TRIGGER delivery_item_no_delete BEFORE DELETE ON delivery_receipt_items BEGIN SELECT RAISE(ABORT,'delivery fact is immutable'); END;
CREATE TRIGGER delivery_note_no_update BEFORE UPDATE ON delivery_notes BEGIN SELECT RAISE(ABORT,'delivery note is immutable'); END;
CREATE TRIGGER delivery_note_no_delete BEFORE DELETE ON delivery_notes BEGIN SELECT RAISE(ABORT,'delivery note is immutable'); END;
CREATE TRIGGER delivery_item_facts BEFORE INSERT ON delivery_receipt_items BEGIN
 SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM delivery_receipts r WHERE r.id=NEW.receipt_id AND r.book_id=NEW.book_id
 AND json_extract(r.body_json,'$.items['||NEW.ordinal||'].draft_id')=NEW.claimed_draft_id
 AND json_extract(r.body_json,'$.items['||NEW.ordinal||'].external_voucher_id') IS NEW.external_voucher_id
 AND json(json_extract(r.body_json,'$.items['||NEW.ordinal||']'))=json(NEW.raw_json))
 THEN RAISE(ABORT,'delivery item must be an exact immutable receipt fact') END;
END;
