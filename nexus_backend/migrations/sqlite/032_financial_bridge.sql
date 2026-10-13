-- Offline reference files and frozen intents; no live-send state is writable.
CREATE UNIQUE INDEX idx_bridge_manifest_scope ON delivery_manifests(export_id,book_id);
CREATE TABLE bridge_configurations(
 ledger_id TEXT NOT NULL,version INTEGER NOT NULL CHECK(version>0),book_id TEXT NOT NULL,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,
 payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),request_fingerprint TEXT NOT NULL,idempotency_key TEXT NOT NULL,created_by TEXT NOT NULL REFERENCES users(id),created_at TEXT NOT NULL,
 PRIMARY KEY(ledger_id,version),UNIQUE(firm_id,created_by,idempotency_key),
 FOREIGN KEY(ledger_id,firm_id,company_id) REFERENCES ledgers(id,firm_id,company_id),FOREIGN KEY(book_id,firm_id,company_id) REFERENCES books(id,firm_id,company_id)
);
CREATE TABLE bridge_import_previews(
 id TEXT PRIMARY KEY,ledger_id TEXT NOT NULL,configuration_version INTEGER NOT NULL,book_id TEXT NOT NULL,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,
 records_json TEXT NOT NULL CHECK(json_valid(records_json)),record_count INTEGER NOT NULL CHECK(record_count BETWEEN 1 AND 1000),fingerprint TEXT NOT NULL CHECK(length(fingerprint)=64),
 idempotency_key TEXT NOT NULL,reason TEXT NOT NULL,created_by TEXT NOT NULL REFERENCES users(id),created_at TEXT NOT NULL,state TEXT NOT NULL CHECK(state IN ('preview','confirmed')),
 UNIQUE(firm_id,created_by,idempotency_key),UNIQUE(id,book_id,firm_id,company_id),
 FOREIGN KEY(ledger_id,configuration_version) REFERENCES bridge_configurations(ledger_id,version),FOREIGN KEY(ledger_id,firm_id,company_id) REFERENCES ledgers(id,firm_id,company_id),FOREIGN KEY(book_id,firm_id,company_id) REFERENCES books(id,firm_id,company_id)
);
CREATE TABLE bridge_reference_records(
 id TEXT PRIMARY KEY,ledger_id TEXT NOT NULL,book_id TEXT NOT NULL,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,preview_id TEXT NOT NULL,
 kind TEXT NOT NULL CHECK(kind IN ('account','counterparty','project','bank_account','voucher','balance')),external_id TEXT NOT NULL,period TEXT NOT NULL,record_json TEXT NOT NULL CHECK(json_valid(record_json)),created_at TEXT NOT NULL,
 UNIQUE(ledger_id,kind,external_id,period),FOREIGN KEY(preview_id,book_id,firm_id,company_id) REFERENCES bridge_import_previews(id,book_id,firm_id,company_id),
 FOREIGN KEY(ledger_id,firm_id,company_id) REFERENCES ledgers(id,firm_id,company_id)
);
CREATE TABLE bridge_outbox(
 id TEXT PRIMARY KEY,ledger_id TEXT NOT NULL,configuration_version INTEGER NOT NULL,export_id TEXT NOT NULL UNIQUE,book_id TEXT NOT NULL,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,
 external_code TEXT NOT NULL UNIQUE,package_json TEXT NOT NULL CHECK(json_valid(package_json)),request_fingerprint TEXT NOT NULL,idempotency_key TEXT NOT NULL,reason TEXT NOT NULL,
 created_by TEXT NOT NULL REFERENCES users(id),created_at TEXT NOT NULL,state TEXT NOT NULL CHECK(state='awaiting_adapter'),
 UNIQUE(firm_id,created_by,idempotency_key),FOREIGN KEY(export_id,book_id) REFERENCES delivery_manifests(export_id,book_id),
 FOREIGN KEY(ledger_id,configuration_version) REFERENCES bridge_configurations(ledger_id,version),FOREIGN KEY(ledger_id,firm_id,company_id) REFERENCES ledgers(id,firm_id,company_id),FOREIGN KEY(book_id,firm_id,company_id) REFERENCES books(id,firm_id,company_id)
);
CREATE INDEX idx_bridge_previews_book ON bridge_import_previews(book_id,created_at,id);
CREATE INDEX idx_bridge_outbox_book ON bridge_outbox(book_id,created_at,id);
CREATE TRIGGER bridge_config_update BEFORE UPDATE ON bridge_configurations BEGIN SELECT RAISE(ABORT,'Frozen bridge configuration'); END;
CREATE TRIGGER bridge_config_delete BEFORE DELETE ON bridge_configurations BEGIN SELECT RAISE(ABORT,'Frozen bridge configuration'); END;
CREATE TRIGGER bridge_records_update BEFORE UPDATE ON bridge_reference_records BEGIN SELECT RAISE(ABORT,'Original reference is immutable'); END;
CREATE TRIGGER bridge_records_delete BEFORE DELETE ON bridge_reference_records BEGIN SELECT RAISE(ABORT,'Original reference is immutable'); END;
CREATE TRIGGER bridge_outbox_update BEFORE UPDATE ON bridge_outbox BEGIN SELECT RAISE(ABORT,'Frozen delivery intent'); END;
CREATE TRIGGER bridge_outbox_delete BEFORE DELETE ON bridge_outbox BEGIN SELECT RAISE(ABORT,'Frozen delivery intent'); END;
CREATE TRIGGER bridge_preview_delete BEFORE DELETE ON bridge_import_previews BEGIN SELECT RAISE(ABORT,'Frozen import preview'); END;
CREATE TRIGGER bridge_preview_update BEFORE UPDATE ON bridge_import_previews WHEN
 OLD.state<>'preview' OR NEW.state<>'confirmed' OR NEW.id<>OLD.id OR NEW.ledger_id<>OLD.ledger_id OR NEW.configuration_version<>OLD.configuration_version OR NEW.book_id<>OLD.book_id OR NEW.firm_id<>OLD.firm_id OR NEW.company_id<>OLD.company_id OR NEW.records_json<>OLD.records_json OR NEW.record_count<>OLD.record_count OR NEW.fingerprint<>OLD.fingerprint OR NEW.idempotency_key<>OLD.idempotency_key OR NEW.reason<>OLD.reason OR NEW.created_by<>OLD.created_by OR NEW.created_at<>OLD.created_at
 BEGIN SELECT RAISE(ABORT,'Only preview confirmation may change'); END;
