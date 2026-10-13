CREATE TABLE bank_source_dispositions (
 id TEXT PRIMARY KEY,firm_id TEXT NOT NULL,book_id TEXT NOT NULL,company_id TEXT NOT NULL,
 source_id TEXT NOT NULL UNIQUE,canonical_source_id TEXT NOT NULL,
 source_fingerprint TEXT NOT NULL CHECK(length(source_fingerprint)=64),canonical_fingerprint TEXT NOT NULL CHECK(length(canonical_fingerprint)=64),
 expected_version INTEGER NOT NULL CHECK(expected_version>=1),reason TEXT NOT NULL CHECK(length(trim(reason)) BETWEEN 3 AND 1000),
 reviewed_by TEXT NOT NULL REFERENCES users(id),idempotency_key TEXT NOT NULL CHECK(length(idempotency_key) BETWEEN 8 AND 120),
 request_fingerprint TEXT NOT NULL CHECK(length(request_fingerprint)=64),created_at TEXT NOT NULL,
 CHECK(source_id<>canonical_source_id),UNIQUE(firm_id,reviewed_by,idempotency_key),
 FOREIGN KEY(source_id,book_id) REFERENCES sources(id,book_id),
 FOREIGN KEY(canonical_source_id,book_id) REFERENCES sources(id,book_id),FOREIGN KEY(book_id,firm_id) REFERENCES books(id,firm_id)
);
CREATE INDEX idx_bank_dispositions_canonical ON bank_source_dispositions(book_id,canonical_source_id);
CREATE TRIGGER bank_disposition_immutable_update BEFORE UPDATE ON bank_source_dispositions
 BEGIN SELECT RAISE(ABORT,'bank disposition is immutable'); END;
CREATE TRIGGER bank_disposition_immutable_delete BEFORE DELETE ON bank_source_dispositions
 BEGIN SELECT RAISE(ABORT,'bank disposition is immutable'); END;
CREATE TRIGGER bank_disposition_truth BEFORE INSERT ON bank_source_dispositions
 WHEN NOT EXISTS(
  SELECT 1 FROM sources s JOIN sources c ON c.id=NEW.canonical_source_id AND c.book_id=s.book_id
   JOIN books b ON b.id=s.book_id JOIN periods p ON p.book_id=b.id AND p.period=b.period
   JOIN drafts d ON d.source_id=s.id AND d.book_id=s.book_id
   LEFT JOIN bank_import_keys k ON k.source_id=s.id AND k.book_id=s.book_id
   LEFT JOIN bank_import_keys kc ON kc.source_id=c.id AND kc.book_id=c.book_id
   WHERE s.id=NEW.source_id AND s.book_id=NEW.book_id AND b.firm_id=NEW.firm_id AND b.company_id=NEW.company_id
    AND p.status='open' AND s.source_kind='bank' AND c.source_kind='bank'
    AND s.fingerprint=NEW.source_fingerprint AND c.fingerprint=NEW.canonical_fingerprint
    AND d.status='draft' AND d.exported_at IS NULL AND d.version=NEW.expected_version+1
    AND s.date=c.date AND s.amount_cents=c.amount_cents AND s.direction=c.direction AND s.counterparty=c.counterparty
    AND (k.source_id IS NOT NULL OR kc.source_id IS NOT NULL) AND (k.source_id IS NULL OR kc.source_id IS NULL OR k.bank_account=kc.bank_account)
    AND NOT EXISTS(SELECT 1 FROM ai_bank_links WHERE bank_source_id=s.id)
    AND NOT EXISTS(SELECT 1 FROM bank_source_dispositions x WHERE x.source_id IN(s.id,c.id) OR x.canonical_source_id=s.id)
    AND NOT EXISTS(SELECT 1 FROM bank_duplicate_decisions x WHERE x.source_id=min(s.id,c.id) AND x.other_source_id=max(s.id,c.id))
 ) BEGIN SELECT RAISE(ABORT,'same transaction requires unreviewed unlinked duplicate and current canonical facts'); END;
CREATE TRIGGER bank_disposition_draft_update BEFORE UPDATE ON drafts
 WHEN EXISTS(SELECT 1 FROM bank_source_dispositions WHERE source_id=OLD.source_id AND book_id=OLD.book_id)
 BEGIN SELECT RAISE(ABORT,'isolated duplicate draft is frozen'); END;
CREATE TRIGGER bank_disposition_draft_delete BEFORE DELETE ON drafts
 WHEN EXISTS(SELECT 1 FROM bank_source_dispositions WHERE source_id=OLD.source_id AND book_id=OLD.book_id)
 BEGIN SELECT RAISE(ABORT,'isolated duplicate draft is frozen'); END;
CREATE TRIGGER bank_disposition_draft_create BEFORE INSERT ON drafts
 WHEN EXISTS(SELECT 1 FROM bank_source_dispositions WHERE source_id=NEW.source_id AND book_id=NEW.book_id)
 BEGIN SELECT RAISE(ABORT,'isolated duplicate cannot generate another draft'); END;
CREATE TRIGGER bank_disposition_lines_insert BEFORE INSERT ON draft_lines
 WHEN EXISTS(SELECT 1 FROM drafts d JOIN bank_source_dispositions x ON x.source_id=d.source_id AND x.book_id=d.book_id WHERE d.id=NEW.draft_id AND d.book_id=NEW.book_id)
 BEGIN SELECT RAISE(ABORT,'isolated duplicate lines are frozen'); END;
CREATE TRIGGER bank_disposition_lines_update BEFORE UPDATE ON draft_lines
 WHEN EXISTS(SELECT 1 FROM drafts d JOIN bank_source_dispositions x ON x.source_id=d.source_id AND x.book_id=d.book_id WHERE d.id=OLD.draft_id AND d.book_id=OLD.book_id)
 OR EXISTS(SELECT 1 FROM drafts d JOIN bank_source_dispositions x ON x.source_id=d.source_id AND x.book_id=d.book_id WHERE d.id=NEW.draft_id AND d.book_id=NEW.book_id)
 BEGIN SELECT RAISE(ABORT,'isolated duplicate lines are frozen'); END;
CREATE TRIGGER bank_disposition_lines_delete BEFORE DELETE ON draft_lines
 WHEN EXISTS(SELECT 1 FROM drafts d JOIN bank_source_dispositions x ON x.source_id=d.source_id AND x.book_id=d.book_id WHERE d.id=OLD.draft_id AND d.book_id=OLD.book_id)
 BEGIN SELECT RAISE(ABORT,'isolated duplicate lines are frozen'); END;
CREATE TRIGGER bank_disposition_link_insert BEFORE INSERT ON ai_bank_links
 WHEN EXISTS(SELECT 1 FROM bank_source_dispositions WHERE source_id=NEW.bank_source_id AND book_id=NEW.book_id)
 BEGIN SELECT RAISE(ABORT,'isolated duplicate cannot link business'); END;
CREATE TRIGGER bank_disposition_distinct_insert BEFORE INSERT ON bank_duplicate_decisions
 WHEN EXISTS(SELECT 1 FROM bank_source_dispositions WHERE source_id IN(NEW.source_id,NEW.other_source_id) AND book_id=NEW.book_id)
 BEGIN SELECT RAISE(ABORT,'isolated duplicates cannot be distinguished'); END;
