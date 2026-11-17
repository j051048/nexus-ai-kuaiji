-- OPT07-D: replace legacy hard-coded payable/receivable clearing codes with
-- account_roles validated values. Rebuild only settlement_items; allocation FKs
-- continue to reference the table name.
PRAGMA legacy_alter_table=ON;
CREATE TABLE settlement_items_new(
 id TEXT PRIMARY KEY,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,book_id TEXT NOT NULL,source_id TEXT NOT NULL UNIQUE,event_id TEXT NOT NULL,
 fact_version INTEGER NOT NULL,source_fingerprint TEXT NOT NULL,counterparty_id TEXT NOT NULL,counterparty_kind TEXT NOT NULL DEFAULT 'counterparty' CHECK(counterparty_kind='counterparty'),counterparty_original TEXT NOT NULL,
 amount_cents INTEGER NOT NULL CHECK(amount_cents>0),direction TEXT NOT NULL CHECK(direction IN ('expense','income')),clearing_account TEXT NOT NULL CHECK(length(trim(clearing_account))>0),
 created_by TEXT NOT NULL REFERENCES users(id),created_at TEXT NOT NULL,idempotency_key TEXT NOT NULL,request_fingerprint TEXT NOT NULL,reason TEXT NOT NULL,
 UNIQUE(id,book_id,firm_id,company_id),UNIQUE(firm_id,created_by,idempotency_key),
 FOREIGN KEY(book_id,firm_id,company_id) REFERENCES books(id,firm_id,company_id),FOREIGN KEY(source_id,book_id) REFERENCES sources(id,book_id),
 FOREIGN KEY(event_id,firm_id,company_id) REFERENCES business_events(id,firm_id,company_id),
 FOREIGN KEY(counterparty_id,firm_id,company_id,counterparty_kind) REFERENCES company_master_entities(id,firm_id,company_id,kind)
);
INSERT INTO settlement_items_new SELECT * FROM settlement_items;
DROP TABLE settlement_items;
ALTER TABLE settlement_items_new RENAME TO settlement_items;
CREATE INDEX idx_settlement_item_page ON settlement_items(book_id,created_at,id);
CREATE TRIGGER settlement_item_truth BEFORE INSERT ON settlement_items WHEN NOT EXISTS(
 SELECT 1 FROM sources s JOIN drafts d ON d.source_id=s.id AND d.book_id=s.book_id JOIN business_mappings m ON m.source_id=s.id AND m.book_id=s.book_id JOIN business_events e ON e.id=m.event_id JOIN books b ON b.id=s.book_id JOIN periods p ON p.book_id=b.id AND p.period=b.period
 WHERE s.id=NEW.source_id AND s.book_id=NEW.book_id AND s.source_kind='business' AND d.status='draft' AND p.status='open' AND e.id=NEW.event_id AND e.fact_version=NEW.fact_version AND json_extract(e.payload,'$.payment_status')='unpaid'
 AND s.fingerprint=NEW.source_fingerprint AND s.amount_cents=NEW.amount_cents AND s.direction=NEW.direction AND s.counterparty=NEW.counterparty_original
 AND (SELECT SUM(CASE WHEN s.direction='expense' THEN credit_cents ELSE debit_cents END) FROM draft_lines WHERE draft_id=d.id AND account_code=NEW.clearing_account)=s.amount_cents
) BEGIN SELECT RAISE(ABORT,'Open item must capture canonical unpaid business facts');END;

CREATE TRIGGER settlement_items_no_update BEFORE UPDATE ON settlement_items BEGIN SELECT RAISE(ABORT,'Settlement evidence is append only');END;

CREATE TRIGGER settlement_items_no_delete BEFORE DELETE ON settlement_items BEGIN SELECT RAISE(ABORT,'Settlement evidence is append only');END;

CREATE TRIGGER settlement_items_dirty AFTER INSERT ON settlement_items BEGIN INSERT INTO ops_company_dirty(firm_id,company_id,dirty_at) VALUES(NEW.firm_id,NEW.company_id,strftime('%Y-%m-%dT%H:%M:%f+00:00','now')) ON CONFLICT(firm_id,company_id) DO UPDATE SET revision=revision+1;END;
PRAGMA legacy_alter_table=OFF;
