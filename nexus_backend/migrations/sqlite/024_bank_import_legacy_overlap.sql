-- Keep 021 immutable triggers; add canonical decision truth for mixed legacy
-- and identified bank evidence using the same predicate as PostgreSQL 037.
CREATE TRIGGER bank_duplicate_canonical_truth BEFORE INSERT ON bank_duplicate_decisions
 WHEN NOT EXISTS(SELECT 1 FROM sources s JOIN sources o ON o.id=NEW.other_source_id AND o.book_id=s.book_id
  LEFT JOIN bank_import_keys k ON k.source_id=s.id AND k.book_id=s.book_id
  LEFT JOIN bank_import_keys ko ON ko.source_id=o.id AND ko.book_id=o.book_id
  JOIN books b ON b.id=s.book_id JOIN periods p ON p.book_id=b.id AND p.period=b.period
  WHERE s.id=NEW.source_id AND s.book_id=NEW.book_id AND b.firm_id=NEW.firm_id AND p.status='open'
   AND s.source_kind='bank' AND o.source_kind='bank'
   AND s.fingerprint=NEW.source_fingerprint AND o.fingerprint=NEW.other_fingerprint
   AND (k.source_id IS NOT NULL OR ko.source_id IS NOT NULL)
   AND (k.source_id IS NULL OR ko.source_id IS NULL OR k.bank_account=ko.bank_account)
   AND s.date=o.date AND s.direction=o.direction AND s.counterparty=o.counterparty AND s.amount_cents=o.amount_cents
   AND EXISTS(SELECT 1 FROM drafts d WHERE d.source_id IN (s.id,o.id) AND d.book_id=s.book_id AND d.status='draft' AND d.version=NEW.expected_draft_version))
 BEGIN SELECT RAISE(ABORT,'Duplicate decision must use current canonical bank facts'); END;
