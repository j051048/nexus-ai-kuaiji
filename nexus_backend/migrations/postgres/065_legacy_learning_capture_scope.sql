-- Historical debt clearance is not an ordinary income/expense classifier.
-- Preserve all existing evidence while tightening capture and every rule gate.
CREATE OR REPLACE FUNCTION accounting.learning_sample_ordinary(target_book TEXT,target_sample TEXT) RETURNS BOOLEAN LANGUAGE sql STABLE SECURITY DEFINER
 SET search_path=pg_catalog,accounting,pg_temp AS $$
 SELECT accounting.book_allowed(target_book) AND EXISTS(
  SELECT 1 FROM accounting.learning_samples x WHERE x.id=target_sample AND x.book_id=target_book AND x.firm_id=accounting.actor_firm()
  AND NOT EXISTS(SELECT 1 FROM accounting.sources s JOIN accounting.settlement_allocations a ON a.bank_source_id=s.id WHERE s.id=x.source_id AND s.source_kind='bank' AND NOT EXISTS(SELECT 1 FROM accounting.settlement_reversals r WHERE r.allocation_id=a.id))
  AND NOT EXISTS(SELECT 1 FROM accounting.migration_clearance_allocations a WHERE a.bank_source_id=x.source_id AND a.bank_book_id=x.book_id AND NOT EXISTS(SELECT 1 FROM accounting.migration_clearance_reversals r WHERE r.command_id=a.command_id)))
$$;
CREATE FUNCTION accounting.learning_legacy_capture_guard() RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER
 SET search_path=pg_catalog,accounting,pg_temp AS $$ BEGIN
 IF EXISTS(SELECT 1 FROM accounting.migration_clearance_allocations a WHERE a.bank_source_id=NEW.source_id AND a.bank_book_id=NEW.book_id AND NOT EXISTS(SELECT 1 FROM accounting.migration_clearance_reversals r WHERE r.command_id=a.command_id)) THEN
  RAISE EXCEPTION 'Historical clearance cannot be captured as an ordinary classification sample' USING ERRCODE='23514';
 END IF;RETURN NEW;END $$;
CREATE TRIGGER learning_legacy_capture BEFORE INSERT ON accounting.learning_samples FOR EACH ROW EXECUTE FUNCTION accounting.learning_legacy_capture_guard();
REVOKE ALL ON FUNCTION accounting.learning_legacy_capture_guard() FROM PUBLIC,accounting_app,accounting_worker;
REVOKE ALL ON FUNCTION accounting.learning_sample_ordinary(TEXT,TEXT) FROM PUBLIC,accounting_worker;
GRANT EXECUTE ON FUNCTION accounting.learning_sample_ordinary(TEXT,TEXT) TO accounting_app;
