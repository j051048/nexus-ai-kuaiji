-- Bank supplement acceptance changes the draft version. Finish those tasks
-- before freezing a duplicate; isolation never silently cancels evidence work.
CREATE FUNCTION accounting.disposition_active_case_guard() RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER
 SET search_path=pg_catalog,accounting,pg_temp AS $$ BEGIN
 IF accounting.case_source_blocked(NEW.book_id,NEW.source_id) THEN
  RAISE EXCEPTION 'Original active exception or supplement must finish before duplicate isolation' USING ERRCODE='23514';
 END IF; RETURN NEW; END $$;
REVOKE ALL ON FUNCTION accounting.disposition_active_case_guard() FROM PUBLIC,accounting_app,accounting_worker;
CREATE TRIGGER disposition_active_case_guard BEFORE INSERT ON accounting.bank_source_dispositions FOR EACH ROW EXECUTE FUNCTION accounting.disposition_active_case_guard();
