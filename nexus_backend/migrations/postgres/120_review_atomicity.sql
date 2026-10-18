-- compatibility: incremental
-- R01/F01/F02: prohibit report/close status transitions to review/publish by
-- an editor, and bind a periodic draft to at most one posted voucher. Service
-- checks are still mandatory; these are the database backstops.
CREATE OR REPLACE FUNCTION accounting.book_review_allowed(target TEXT) RETURNS BOOLEAN
 LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 SELECT accounting.actor_active() AND EXISTS(
  SELECT 1 FROM accounting.books b
  JOIN accounting.firm_clients fc ON fc.firm_id=b.firm_id AND fc.company_id=b.company_id
  WHERE b.id=target AND b.firm_id=accounting.actor_firm() AND fc.status='active' AND (
   accounting.firm_manager(b.firm_id) OR EXISTS(
    SELECT 1 FROM accounting.book_access ba
    WHERE ba.book_id=b.id AND ba.firm_id=b.firm_id AND ba.user_id=accounting.actor_user()
      AND ba.role='reviewer' AND (
       accounting.firm_role(b.firm_id) IN ('owner','admin','reviewer')
       OR (accounting.firm_role(b.firm_id) IS NULL AND EXISTS(
        SELECT 1 FROM accounting.company_memberships cm
        WHERE cm.company_id=b.company_id AND cm.user_id=accounting.actor_user()
          AND cm.role='finance'))
      )
   )
  )
 )
$$;
REVOKE ALL ON FUNCTION accounting.book_review_allowed(TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION accounting.book_review_allowed(TEXT) TO accounting_app;

CREATE OR REPLACE FUNCTION accounting.report_review_role_guard() RETURNS TRIGGER
 LANGUAGE plpgsql SET search_path=pg_catalog,accounting,pg_temp AS $$
BEGIN
 IF NEW.status IN ('reviewed','published') AND OLD.status IS DISTINCT FROM NEW.status
  AND NOT accounting.book_review_allowed(NEW.book_id) THEN
  RAISE EXCEPTION 'current review permission required' USING ERRCODE='42501';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER b04_report_review_role BEFORE UPDATE ON accounting.report_runs
 FOR EACH ROW EXECUTE FUNCTION accounting.report_review_role_guard();

CREATE OR REPLACE FUNCTION accounting.close_review_role_guard() RETURNS TRIGGER
 LANGUAGE plpgsql SET search_path=pg_catalog,accounting,pg_temp AS $$
BEGIN
 IF NEW.status IN ('reviewed','locked','published') AND OLD.status IS DISTINCT FROM NEW.status
  AND NOT accounting.book_review_allowed(NEW.book_id) THEN
  RAISE EXCEPTION 'current review permission required' USING ERRCODE='42501';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER b04_close_review_role BEFORE UPDATE ON accounting.internal_close_runs
 FOR EACH ROW EXECUTE FUNCTION accounting.close_review_role_guard();

CREATE UNIQUE INDEX IF NOT EXISTS periodic_draft_posted_voucher
 ON accounting.periodic_drafts(posted_voucher_id) WHERE posted_voucher_id IS NOT NULL;
