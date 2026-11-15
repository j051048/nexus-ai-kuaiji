-- Forward-only A-D integrity guard. PostgreSQL additionally enforces actor RLS.
CREATE INDEX IF NOT EXISTS taxi_fee_draft_idx ON tax_fee_batches(draft_id,book_id);
CREATE TRIGGER IF NOT EXISTS taxi_fee_insert_scope BEFORE INSERT ON tax_fee_batches
WHEN NEW.status<>'draft' OR NEW.version<>1 OR NEW.reviewed_by IS NOT NULL OR NEW.reviewed_at IS NOT NULL
 OR NOT EXISTS(SELECT 1 FROM books b JOIN drafts d ON d.book_id=b.id JOIN periods p ON p.book_id=b.id AND p.period=b.period
 WHERE b.id=NEW.book_id AND b.firm_id=NEW.firm_id AND b.company_id=NEW.company_id AND b.period=NEW.period
 AND p.status='open' AND d.id=NEW.draft_id AND NOT EXISTS(SELECT 1 FROM internal_period_locks l WHERE l.book_id=b.id))
BEGIN SELECT RAISE(ABORT,'Taxi fee scope or initial state invalid'); END;
CREATE TRIGGER IF NOT EXISTS taxi_fee_review_evidence BEFORE UPDATE OF status ON tax_fee_batches
WHEN NEW.status='reviewed' AND OLD.status<>'reviewed' AND NOT EXISTS(
 SELECT 1 FROM drafts d JOIN effective_draft_review_receipts r ON r.draft_id=d.id AND r.book_id=d.book_id AND r.reviewed_version=d.version-1
 WHERE d.id=NEW.draft_id AND d.book_id=NEW.book_id AND d.status='approved' AND r.independent=1
 AND NEW.reviewed_by=r.reviewer_user_id AND NEW.reviewed_by<>NEW.created_by AND NEW.reviewed_at IS NOT NULL)
BEGIN SELECT RAISE(ABORT,'Independent draft review evidence required'); END;
CREATE TRIGGER IF NOT EXISTS taxi_fee_terminal BEFORE UPDATE OF status ON tax_fee_batches
WHEN NEW.status<>OLD.status AND (OLD.status='invalidated' OR NEW.version<>OLD.version+1)
BEGIN SELECT RAISE(ABORT,'Taxi fee terminal state or version invalid'); END;
CREATE TRIGGER IF NOT EXISTS taxi_fee_draft_guard BEFORE UPDATE OF status ON drafts
WHEN NEW.status IN('approved','exported') AND NEW.status<>OLD.status AND EXISTS(
 SELECT 1 FROM tax_fee_batches b WHERE b.draft_id=NEW.id AND b.book_id=NEW.book_id AND (
 b.status='invalidated' OR json_array_length(b.source_snapshot_json)=0
 OR EXISTS(SELECT 1 FROM json_each(b.source_snapshot_json) s WHERE NOT EXISTS(
   SELECT 1 FROM tax_source_files f WHERE f.id=json_extract(s.value,'$[0]') AND f.fingerprint=json_extract(s.value,'$[1]')
    AND f.book_id=b.book_id AND f.firm_id=b.firm_id AND f.company_id=b.company_id AND f.status='active'
    AND NOT EXISTS(SELECT 1 FROM json_each(f.rows_json) r WHERE json_extract(r.value,'$.status')='needs_review')))
 OR EXISTS(SELECT 1 FROM tax_fee_batch_rows r WHERE r.batch_id=b.id AND r.status='rejected')))
BEGIN SELECT RAISE(ABORT,'Taxi fee source is stale, incomplete or invalidated'); END;
