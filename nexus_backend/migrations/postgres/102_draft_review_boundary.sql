-- compatibility: incremental
-- Defense in depth for the review boundary. The Python service remains the
-- authoritative place for permissions, evidence and auxiliary validation; this
-- trigger prevents a direct SQL writer from turning an unbalanced, closed-period
-- or inactive-source draft into an approved/exported accounting record.
CREATE OR REPLACE FUNCTION accounting.draft_review_boundary_guard()
RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,accounting,pg_temp AS $$
DECLARE
 debit_total BIGINT;
 credit_total BIGINT;
BEGIN
 IF TG_OP<>'UPDATE' OR NEW.status NOT IN ('approved','exported')
  OR NEW.status IS NOT DISTINCT FROM OLD.status THEN
  IF TG_OP='UPDATE' AND OLD.status IN ('approved','exported')
   AND NEW.status NOT IN ('approved','exported') THEN
   RAISE EXCEPTION 'Reviewed draft cannot be reopened by direct update' USING ERRCODE='23514';
  END IF;
  RETURN NEW;
 END IF;

 IF OLD.status='draft' AND NEW.status='exported' THEN
  RAISE EXCEPTION 'Review must precede export' USING ERRCODE='23514';
 END IF;

 IF NOT EXISTS(
  SELECT 1 FROM accounting.periods p
  JOIN accounting.books b ON b.id=p.book_id AND p.period=b.period
  WHERE p.book_id=NEW.book_id AND p.status='open'
 ) THEN
  RAISE EXCEPTION 'Draft review requires an open period' USING ERRCODE='23514';
 END IF;

 IF NOT EXISTS(
  SELECT 1 FROM accounting.sources s
  WHERE s.id=NEW.source_id AND s.book_id=NEW.book_id
 ) THEN
  RAISE EXCEPTION 'Draft review requires its current source' USING ERRCODE='23514';
 END IF;

 IF EXISTS(
  SELECT 1 FROM accounting.bank_source_dispositions z
  WHERE z.book_id=NEW.book_id AND z.source_id=NEW.source_id
 ) OR EXISTS(
  SELECT 1 FROM accounting.fact_revision_lineage z
  WHERE z.old_book_id=NEW.book_id AND z.old_source_id=NEW.source_id
 ) THEN
  RAISE EXCEPTION 'Inactive source cannot be approved or exported' USING ERRCODE='23514';
 END IF;

 SELECT COALESCE(SUM(l.debit_cents),0),COALESCE(SUM(l.credit_cents),0)
  INTO debit_total,credit_total
  FROM accounting.draft_lines l
  WHERE l.draft_id=NEW.id AND l.book_id=NEW.book_id;
 IF debit_total<=0 OR debit_total<>credit_total THEN
  RAISE EXCEPTION 'Draft review requires a balanced non-empty entry' USING ERRCODE='23514';
 END IF;

 IF EXISTS(
  SELECT 1 FROM accounting.draft_lines l
  LEFT JOIN accounting.accounts a ON a.book_id=l.book_id AND a.code=l.account_code
  WHERE l.draft_id=NEW.id AND l.book_id=NEW.book_id
   AND (a.code IS NULL OR a.active<>1)
 ) THEN
  RAISE EXCEPTION 'Draft review requires active accounts' USING ERRCODE='23514';
 END IF;

 RETURN NEW;
END $$;

REVOKE ALL ON FUNCTION accounting.draft_review_boundary_guard() FROM PUBLIC;
DROP TRIGGER IF EXISTS draft_review_boundary_guard ON accounting.drafts;
CREATE TRIGGER draft_review_boundary_guard
 BEFORE UPDATE ON accounting.drafts
 FOR EACH ROW EXECUTE FUNCTION accounting.draft_review_boundary_guard();
