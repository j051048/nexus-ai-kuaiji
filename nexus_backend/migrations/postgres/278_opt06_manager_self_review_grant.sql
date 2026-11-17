-- OPT06-B: managers cannot grant reviewer to themselves or another manager.
CREATE OR REPLACE FUNCTION accounting.book_access_separation_guard() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
BEGIN
 IF NEW.role='reviewer'
    AND EXISTS(SELECT 1 FROM accounting.firm_review_controls c WHERE c.firm_id=NEW.firm_id AND c.management_review_separation)
    AND EXISTS(SELECT 1 FROM accounting.memberships m WHERE m.firm_id=NEW.firm_id AND m.user_id=NEW.user_id AND m.role IN('owner','admin')) THEN
  RAISE EXCEPTION 'Management/review separation forbids manager reviewer grants' USING ERRCODE='42501';
 END IF;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION accounting.book_access_separation_guard() FROM PUBLIC,accounting_app,accounting_worker,accounting_system;
DROP TRIGGER IF EXISTS book_access_separation_guard ON accounting.book_access;
CREATE TRIGGER book_access_separation_guard BEFORE INSERT OR UPDATE ON accounting.book_access
FOR EACH ROW EXECUTE FUNCTION accounting.book_access_separation_guard();
