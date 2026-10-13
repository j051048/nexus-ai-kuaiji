-- Auxiliary open items, never mutable source facts or a formal subledger.
CREATE TABLE settlement_items(
 id TEXT PRIMARY KEY,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,book_id TEXT NOT NULL,source_id TEXT NOT NULL UNIQUE,event_id TEXT NOT NULL,
 fact_version INTEGER NOT NULL,source_fingerprint TEXT NOT NULL,counterparty_id TEXT NOT NULL,counterparty_kind TEXT NOT NULL DEFAULT 'counterparty' CHECK(counterparty_kind='counterparty'),counterparty_original TEXT NOT NULL,
 amount_cents INTEGER NOT NULL CHECK(amount_cents>0),direction TEXT NOT NULL CHECK(direction IN ('expense','income')),clearing_account TEXT NOT NULL CHECK(clearing_account IN ('2202','2241','1122','1221')),
 created_by TEXT NOT NULL REFERENCES users(id),created_at TEXT NOT NULL,idempotency_key TEXT NOT NULL,request_fingerprint TEXT NOT NULL,reason TEXT NOT NULL,
 UNIQUE(id,book_id,firm_id,company_id),UNIQUE(firm_id,created_by,idempotency_key),
 FOREIGN KEY(book_id,firm_id,company_id) REFERENCES books(id,firm_id,company_id),FOREIGN KEY(source_id,book_id) REFERENCES sources(id,book_id),
 FOREIGN KEY(event_id,firm_id,company_id) REFERENCES business_events(id,firm_id,company_id),
 FOREIGN KEY(counterparty_id,firm_id,company_id,counterparty_kind) REFERENCES company_master_entities(id,firm_id,company_id,kind)
);
CREATE TABLE settlement_previews(
 id TEXT PRIMARY KEY,book_id TEXT NOT NULL,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,created_by TEXT NOT NULL REFERENCES users(id),
 request_json TEXT NOT NULL,plan_json TEXT NOT NULL,created_at TEXT NOT NULL,expires_at TEXT NOT NULL,
 UNIQUE(id,book_id,firm_id,company_id),FOREIGN KEY(book_id,firm_id,company_id) REFERENCES books(id,firm_id,company_id)
);
CREATE TABLE settlement_commands(
 id TEXT PRIMARY KEY,book_id TEXT NOT NULL,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,preview_id TEXT NOT NULL UNIQUE,
 created_by TEXT NOT NULL REFERENCES users(id),idempotency_key TEXT NOT NULL,created_at TEXT NOT NULL,
 UNIQUE(id,book_id,firm_id,company_id),UNIQUE(firm_id,created_by,idempotency_key),
 FOREIGN KEY(preview_id,book_id,firm_id,company_id) REFERENCES settlement_previews(id,book_id,firm_id,company_id)
);
CREATE TABLE settlement_allocations(
 id TEXT PRIMARY KEY,command_id TEXT NOT NULL,book_id TEXT NOT NULL,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,item_id TEXT NOT NULL,
 bank_source_id TEXT NOT NULL,amount_cents INTEGER NOT NULL CHECK(amount_cents>0),clearing_account TEXT NOT NULL,
 created_by TEXT NOT NULL REFERENCES users(id),created_at TEXT NOT NULL,UNIQUE(id,book_id,firm_id,company_id),
 FOREIGN KEY(command_id,book_id,firm_id,company_id) REFERENCES settlement_commands(id,book_id,firm_id,company_id),
 FOREIGN KEY(item_id,book_id,firm_id,company_id) REFERENCES settlement_items(id,book_id,firm_id,company_id),FOREIGN KEY(bank_source_id,book_id) REFERENCES sources(id,book_id)
);
CREATE TABLE settlement_reversals(
 id TEXT PRIMARY KEY,allocation_id TEXT NOT NULL UNIQUE,book_id TEXT NOT NULL,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,
 created_by TEXT NOT NULL REFERENCES users(id),created_at TEXT NOT NULL,idempotency_key TEXT NOT NULL,reason TEXT NOT NULL,expected_fingerprint TEXT NOT NULL,
 UNIQUE(firm_id,created_by,idempotency_key),FOREIGN KEY(allocation_id,book_id,firm_id,company_id) REFERENCES settlement_allocations(id,book_id,firm_id,company_id)
);
CREATE INDEX idx_settlement_item_page ON settlement_items(book_id,created_at,id);
CREATE INDEX idx_settlement_bank_balance ON settlement_allocations(bank_source_id,item_id);
CREATE INDEX idx_settlement_item_balance ON settlement_allocations(item_id,bank_source_id);
CREATE TRIGGER settlement_revision_guard BEFORE INSERT ON fact_revision_lineage WHEN EXISTS(SELECT 1 FROM settlement_items i JOIN settlement_allocations a ON a.item_id=i.id WHERE i.source_id=NEW.old_source_id AND i.book_id=NEW.old_book_id AND NOT EXISTS(SELECT 1 FROM settlement_reversals r WHERE r.allocation_id=a.id)) BEGIN SELECT RAISE(ABORT,'Active allocations require reversal before fact replacement');END;

CREATE TRIGGER settlement_item_truth BEFORE INSERT ON settlement_items WHEN NOT EXISTS(
 SELECT 1 FROM sources s JOIN drafts d ON d.source_id=s.id AND d.book_id=s.book_id JOIN business_mappings m ON m.source_id=s.id AND m.book_id=s.book_id JOIN business_events e ON e.id=m.event_id JOIN books b ON b.id=s.book_id JOIN periods p ON p.book_id=b.id AND p.period=b.period
 WHERE s.id=NEW.source_id AND s.book_id=NEW.book_id AND s.source_kind='business' AND d.status='draft' AND p.status='open' AND e.id=NEW.event_id AND e.fact_version=NEW.fact_version AND json_extract(e.payload,'$.payment_status')='unpaid'
 AND s.fingerprint=NEW.source_fingerprint AND s.amount_cents=NEW.amount_cents AND s.direction=NEW.direction AND s.counterparty=NEW.counterparty_original
 AND (SELECT SUM(CASE WHEN s.direction='expense' THEN credit_cents ELSE debit_cents END) FROM draft_lines WHERE draft_id=d.id AND account_code=NEW.clearing_account)=s.amount_cents
) BEGIN SELECT RAISE(ABORT,'Open item must capture canonical unpaid business facts');END;
CREATE TRIGGER settlement_allocation_truth BEFORE INSERT ON settlement_allocations WHEN NOT EXISTS(
 SELECT 1 FROM settlement_items i JOIN sources s ON s.id=NEW.bank_source_id AND s.book_id=i.book_id JOIN drafts d ON d.source_id=s.id AND d.book_id=s.book_id JOIN books b ON b.id=s.book_id JOIN periods p ON p.book_id=b.id AND p.period=b.period
 WHERE i.id=NEW.item_id AND i.book_id=NEW.book_id AND i.firm_id=NEW.firm_id AND i.company_id=NEW.company_id AND s.source_kind='bank' AND d.status='draft' AND p.status='open' AND s.direction=i.direction AND NEW.clearing_account=i.clearing_account
 AND NOT EXISTS(SELECT 1 FROM ai_bank_links WHERE bank_source_id=s.id OR event_id=i.event_id)
 AND NOT EXISTS(SELECT 1 FROM bank_source_dispositions WHERE source_id=s.id)
 AND (EXISTS(SELECT 1 FROM company_master_entities e WHERE e.id=i.counterparty_id AND e.active=1 AND e.name=s.counterparty) OR EXISTS(SELECT 1 FROM company_master_aliases a JOIN company_master_entities e ON e.id=a.entity_id WHERE a.entity_id=i.counterparty_id AND a.original=s.counterparty AND a.status='approved' AND e.active=1))
 AND NEW.amount_cents+COALESCE((SELECT SUM(a.amount_cents) FROM settlement_allocations a WHERE a.bank_source_id=s.id AND NOT EXISTS(SELECT 1 FROM settlement_reversals r WHERE r.allocation_id=a.id)),0)<=s.amount_cents
 AND NEW.amount_cents+COALESCE((SELECT SUM(a.amount_cents) FROM settlement_allocations a WHERE a.item_id=i.id AND NOT EXISTS(SELECT 1 FROM settlement_reversals r WHERE r.allocation_id=a.id)),0)<=i.amount_cents
) BEGIN SELECT RAISE(ABORT,'Allocation must preserve scoped party and both balances');END;
CREATE TRIGGER settlement_reverse_truth BEFORE INSERT ON settlement_reversals WHEN NOT EXISTS(
 SELECT 1 FROM settlement_allocations a JOIN drafts d ON d.source_id=a.bank_source_id AND d.book_id=a.book_id JOIN books b ON b.id=a.book_id JOIN periods p ON p.book_id=b.id AND p.period=b.period WHERE a.id=NEW.allocation_id AND a.book_id=NEW.book_id AND a.created_by<>NEW.created_by AND d.status='draft' AND p.status='open'
) BEGIN SELECT RAISE(ABORT,'Reversal requires an independent reviewer and unreviewed bank journal');END;
CREATE TRIGGER settlement_link_guard BEFORE INSERT ON ai_bank_links WHEN EXISTS(SELECT 1 FROM settlement_allocations a LEFT JOIN settlement_items i ON i.id=a.item_id WHERE (a.bank_source_id=NEW.bank_source_id OR i.event_id=NEW.event_id) AND NOT EXISTS(SELECT 1 FROM settlement_reversals r WHERE r.allocation_id=a.id)) BEGIN SELECT RAISE(ABORT,'Settlement and full matching are mutually exclusive');END;
CREATE TRIGGER settlement_disposition_guard BEFORE INSERT ON bank_source_dispositions WHEN EXISTS(SELECT 1 FROM settlement_allocations a WHERE a.bank_source_id=NEW.source_id AND NOT EXISTS(SELECT 1 FROM settlement_reversals r WHERE r.allocation_id=a.id)) BEGIN SELECT RAISE(ABORT,'Allocated bank source cannot be isolated');END;
CREATE TRIGGER settlement_draft_guard BEFORE UPDATE OF status ON drafts WHEN NEW.status IN ('approved','exported') AND EXISTS(SELECT 1 FROM settlement_allocations a WHERE a.bank_source_id=NEW.source_id AND NOT EXISTS(SELECT 1 FROM settlement_reversals r WHERE r.allocation_id=a.id)) AND (
 (SELECT SUM(a.amount_cents) FROM settlement_allocations a WHERE a.bank_source_id=NEW.source_id AND NOT EXISTS(SELECT 1 FROM settlement_reversals r WHERE r.allocation_id=a.id))<>(SELECT amount_cents FROM sources WHERE id=NEW.source_id)
 OR EXISTS(SELECT 1 FROM settlement_allocations a JOIN settlement_items i ON i.id=a.item_id JOIN drafts d ON d.source_id=i.source_id AND d.book_id=i.book_id WHERE a.bank_source_id=NEW.source_id AND NOT EXISTS(SELECT 1 FROM settlement_reversals r WHERE r.allocation_id=a.id) AND (d.status NOT IN ('approved','exported') OR NOT EXISTS(SELECT 1 FROM business_mappings m JOIN business_events e ON e.id=m.event_id WHERE m.source_id=i.source_id AND m.book_id=i.book_id AND e.fact_version=i.fact_version AND json_extract(e.payload,'$.payment_status')='unpaid')))
 OR EXISTS(SELECT 1 FROM draft_lines l WHERE l.draft_id=NEW.id AND l.account_code<>'1002' AND NOT EXISTS(SELECT 1 FROM settlement_allocations a WHERE a.bank_source_id=NEW.source_id AND a.clearing_account=l.account_code AND NOT EXISTS(SELECT 1 FROM settlement_reversals r WHERE r.allocation_id=a.id)))
 OR EXISTS(SELECT 1 FROM settlement_allocations a JOIN sources s ON s.id=a.bank_source_id WHERE a.bank_source_id=NEW.source_id AND NOT EXISTS(SELECT 1 FROM settlement_reversals r WHERE r.allocation_id=a.id) AND (
  COALESCE((SELECT SUM(CASE WHEN s.direction='expense' THEN l.debit_cents ELSE l.credit_cents END) FROM draft_lines l WHERE l.draft_id=NEW.id AND l.account_code=a.clearing_account),0)<>(SELECT SUM(a2.amount_cents) FROM settlement_allocations a2 WHERE a2.bank_source_id=NEW.source_id AND a2.clearing_account=a.clearing_account AND NOT EXISTS(SELECT 1 FROM settlement_reversals r WHERE r.allocation_id=a2.id))
  OR COALESCE((SELECT SUM(CASE WHEN s.direction='expense' THEN l.credit_cents ELSE l.debit_cents END) FROM draft_lines l WHERE l.draft_id=NEW.id AND l.account_code=a.clearing_account),0)<>0))
) BEGIN SELECT RAISE(ABORT,'Bank source must clear reviewed payable/receivable balances');END;
CREATE TRIGGER settlement_items_no_update BEFORE UPDATE ON settlement_items BEGIN SELECT RAISE(ABORT,'Settlement evidence is append only');END;

CREATE TRIGGER settlement_items_no_delete BEFORE DELETE ON settlement_items BEGIN SELECT RAISE(ABORT,'Settlement evidence is append only');END;

CREATE TRIGGER settlement_items_dirty AFTER INSERT ON settlement_items BEGIN INSERT INTO ops_company_dirty(firm_id,company_id,dirty_at) VALUES(NEW.firm_id,NEW.company_id,strftime('%Y-%m-%dT%H:%M:%f+00:00','now')) ON CONFLICT(firm_id,company_id) DO UPDATE SET revision=revision+1;END;

CREATE TRIGGER settlement_previews_no_update BEFORE UPDATE ON settlement_previews BEGIN SELECT RAISE(ABORT,'Settlement evidence is append only');END;

CREATE TRIGGER settlement_previews_no_delete BEFORE DELETE ON settlement_previews BEGIN SELECT RAISE(ABORT,'Settlement evidence is append only');END;

CREATE TRIGGER settlement_previews_dirty AFTER INSERT ON settlement_previews BEGIN INSERT INTO ops_company_dirty(firm_id,company_id,dirty_at) VALUES(NEW.firm_id,NEW.company_id,strftime('%Y-%m-%dT%H:%M:%f+00:00','now')) ON CONFLICT(firm_id,company_id) DO UPDATE SET revision=revision+1;END;

CREATE TRIGGER settlement_commands_no_update BEFORE UPDATE ON settlement_commands BEGIN SELECT RAISE(ABORT,'Settlement evidence is append only');END;

CREATE TRIGGER settlement_commands_no_delete BEFORE DELETE ON settlement_commands BEGIN SELECT RAISE(ABORT,'Settlement evidence is append only');END;

CREATE TRIGGER settlement_commands_dirty AFTER INSERT ON settlement_commands BEGIN INSERT INTO ops_company_dirty(firm_id,company_id,dirty_at) VALUES(NEW.firm_id,NEW.company_id,strftime('%Y-%m-%dT%H:%M:%f+00:00','now')) ON CONFLICT(firm_id,company_id) DO UPDATE SET revision=revision+1;END;

CREATE TRIGGER settlement_allocations_no_update BEFORE UPDATE ON settlement_allocations BEGIN SELECT RAISE(ABORT,'Settlement evidence is append only');END;

CREATE TRIGGER settlement_allocations_no_delete BEFORE DELETE ON settlement_allocations BEGIN SELECT RAISE(ABORT,'Settlement evidence is append only');END;

CREATE TRIGGER settlement_allocations_dirty AFTER INSERT ON settlement_allocations BEGIN INSERT INTO ops_company_dirty(firm_id,company_id,dirty_at) VALUES(NEW.firm_id,NEW.company_id,strftime('%Y-%m-%dT%H:%M:%f+00:00','now')) ON CONFLICT(firm_id,company_id) DO UPDATE SET revision=revision+1;END;

CREATE TRIGGER settlement_reversals_no_update BEFORE UPDATE ON settlement_reversals BEGIN SELECT RAISE(ABORT,'Settlement evidence is append only');END;

CREATE TRIGGER settlement_reversals_no_delete BEFORE DELETE ON settlement_reversals BEGIN SELECT RAISE(ABORT,'Settlement evidence is append only');END;

CREATE TRIGGER settlement_reversals_dirty AFTER INSERT ON settlement_reversals BEGIN INSERT INTO ops_company_dirty(firm_id,company_id,dirty_at) VALUES(NEW.firm_id,NEW.company_id,strftime('%Y-%m-%dT%H:%M:%f+00:00','now')) ON CONFLICT(firm_id,company_id) DO UPDATE SET revision=revision+1;END;
