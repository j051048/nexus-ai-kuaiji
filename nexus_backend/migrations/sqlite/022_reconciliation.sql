CREATE TABLE reconciliation_receipts(
 id TEXT PRIMARY KEY,firm_id TEXT NOT NULL,created_by TEXT NOT NULL,idempotency_key TEXT NOT NULL,
 request_fingerprint TEXT NOT NULL,request_json TEXT NOT NULL CHECK(json_valid(request_json) AND json_type(request_json)='object'),
 reason TEXT NOT NULL CHECK(length(trim(reason))>0),created_at TEXT NOT NULL,
 UNIQUE(firm_id,created_by,idempotency_key),FOREIGN KEY(firm_id) REFERENCES firms(id),FOREIGN KEY(created_by) REFERENCES users(id)
);
CREATE INDEX idx_sources_reconciliation_candidate ON sources(book_id,source_kind,direction,counterparty,date,amount_cents,id);
CREATE INDEX idx_reconciliation_receipts_owner ON reconciliation_receipts(firm_id,created_by,created_at,id);
CREATE TRIGGER reconciliation_receipt_update BEFORE UPDATE ON reconciliation_receipts BEGIN SELECT RAISE(ABORT,'reconciliation receipt is immutable'); END;
CREATE TRIGGER reconciliation_receipt_delete BEFORE DELETE ON reconciliation_receipts BEGIN SELECT RAISE(ABORT,'reconciliation receipt is immutable'); END;
CREATE TRIGGER reconciliation_receipt_truth BEFORE INSERT ON reconciliation_receipts
 WHEN json_array_length(NEW.request_json,'$.items') NOT BETWEEN 1 AND 20 OR EXISTS(
  SELECT 1 FROM json_each(NEW.request_json,'$.items') item
  WHERE NOT EXISTS(SELECT 1 FROM business_mappings m JOIN business_events e ON e.id=m.event_id
   JOIN books b ON b.id=m.book_id JOIN periods p ON p.book_id=b.id AND p.period=b.period
   JOIN sources bs ON bs.id=m.source_id AND bs.book_id=m.book_id JOIN drafts bd ON bd.id=m.draft_id AND bd.book_id=m.book_id
   WHERE m.book_id=json_extract(item.value,'$.book_id') AND m.event_id=json_extract(item.value,'$.event_id') AND m.firm_id=NEW.firm_id
    AND e.status='handed_off' AND json_extract(e.payload,'$.payment_status')='paid' AND p.status='open' AND bd.status='draft'
    AND e.version=json_extract(item.value,'$.event_version') AND e.fact_version=json_extract(item.value,'$.fact_version')
    AND bd.version=json_extract(item.value,'$.business_draft_version') AND bs.fingerprint=json_extract(item.value,'$.business_source_fingerprint')
    AND bs.amount_cents=(SELECT COALESCE(SUM(s.amount_cents),0) FROM json_each(item.value,'$.banks') bank JOIN sources s ON s.id=json_extract(bank.value,'$.source_id') AND s.book_id=m.book_id)
    AND NOT EXISTS(SELECT 1 FROM json_each(item.value,'$.banks') bank WHERE NOT EXISTS(
     SELECT 1 FROM sources s JOIN drafts d ON d.source_id=s.id AND d.book_id=s.book_id JOIN ai_bank_links l ON l.bank_source_id=s.id AND l.book_id=s.book_id
     WHERE s.id=json_extract(bank.value,'$.source_id') AND s.book_id=m.book_id AND l.event_id=m.event_id AND s.source_kind='bank' AND d.status='draft'
      AND s.fingerprint=json_extract(bank.value,'$.source_fingerprint') AND d.version=json_extract(bank.value,'$.draft_version')
      AND s.direction=bs.direction AND s.counterparty=bs.counterparty AND abs(julianday(s.date)-julianday(bs.date))<=7))
  )
 )
 BEGIN SELECT RAISE(ABORT,'reconciliation receipt requires canonical current matching links'); END;
