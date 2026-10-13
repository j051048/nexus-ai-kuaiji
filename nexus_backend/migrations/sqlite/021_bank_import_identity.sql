CREATE TABLE bank_import_keys (
 source_id TEXT PRIMARY KEY,book_id TEXT NOT NULL,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,
 namespace TEXT NOT NULL CHECK(length(trim(namespace)) BETWEEN 1 AND 100),
 bank_account TEXT NOT NULL CHECK(length(trim(bank_account)) BETWEEN 1 AND 100),
 stable_transaction_id TEXT NOT NULL CHECK(length(trim(stable_transaction_id)) BETWEEN 1 AND 200),
 original_external_id TEXT NOT NULL,content_fingerprint TEXT NOT NULL,created_by TEXT NOT NULL REFERENCES users(id),created_at TEXT NOT NULL,
 UNIQUE(firm_id,company_id,namespace,bank_account,stable_transaction_id),
 FOREIGN KEY(source_id,book_id) REFERENCES sources(id,book_id),
 FOREIGN KEY(book_id,firm_id) REFERENCES books(id,firm_id)
);
CREATE TABLE bank_duplicate_decisions (
 source_id TEXT NOT NULL,other_source_id TEXT NOT NULL,book_id TEXT NOT NULL,firm_id TEXT NOT NULL,
 source_fingerprint TEXT NOT NULL,other_fingerprint TEXT NOT NULL,expected_draft_version INTEGER NOT NULL,
 reason TEXT NOT NULL CHECK(length(trim(reason)) BETWEEN 3 AND 1000),
 reviewed_by TEXT NOT NULL REFERENCES users(id),created_at TEXT NOT NULL,
 PRIMARY KEY(source_id,other_source_id),CHECK(source_id<other_source_id),
 FOREIGN KEY(source_id,book_id) REFERENCES sources(id,book_id),
 FOREIGN KEY(other_source_id,book_id) REFERENCES sources(id,book_id),
 FOREIGN KEY(book_id,firm_id) REFERENCES books(id,firm_id)
);
CREATE INDEX idx_bank_import_account ON bank_import_keys(book_id,namespace,bank_account,source_id);
CREATE INDEX idx_sources_duplicate_facts ON sources(book_id,source_kind,date,amount_cents,direction,counterparty);
CREATE TRIGGER bank_import_scope BEFORE INSERT ON bank_import_keys BEGIN
 SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM sources s JOIN books b ON b.id=s.book_id JOIN periods p ON p.book_id=b.id AND p.period=b.period WHERE s.id=NEW.source_id AND s.book_id=NEW.book_id
  AND s.source_kind='bank' AND s.external_id='bank:v1:'||NEW.source_id AND s.fingerprint=NEW.content_fingerprint
  AND b.firm_id=NEW.firm_id AND b.company_id=NEW.company_id AND p.status='open' AND substr(s.date,1,7)=b.period) THEN RAISE(ABORT,'Bank identity scope mismatch') END;
END;
CREATE TRIGGER bank_identity_no_update BEFORE UPDATE ON bank_import_keys BEGIN SELECT RAISE(ABORT,'Bank identity is immutable'); END;
CREATE TRIGGER bank_identity_no_delete BEFORE DELETE ON bank_import_keys BEGIN SELECT RAISE(ABORT,'Bank identity is immutable'); END;
CREATE TRIGGER bank_decision_no_update BEFORE UPDATE ON bank_duplicate_decisions BEGIN SELECT RAISE(ABORT,'Duplicate review is immutable'); END;
CREATE TRIGGER bank_decision_no_delete BEFORE DELETE ON bank_duplicate_decisions BEGIN SELECT RAISE(ABORT,'Duplicate review is immutable'); END;
