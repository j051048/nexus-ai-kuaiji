-- compatibility: incremental
-- A ledger remains configurable for future months; frozen months cannot change.
CREATE OR REPLACE FUNCTION accounting.dcp_configuration_lock_guard() RETURNS TRIGGER
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
DECLARE entry JSONB; entries JSONB[]; ledger TEXT; first_month TEXT; last_month TEXT;
BEGIN
 entries:=CASE WHEN TG_OP='INSERT' THEN ARRAY[to_jsonb(NEW)] WHEN TG_OP='DELETE' THEN ARRAY[to_jsonb(OLD)] ELSE ARRAY[to_jsonb(OLD),to_jsonb(NEW)] END;
 FOREACH entry IN ARRAY entries LOOP
  PERFORM accounting.lock_company_write(entry->>'firm_id',entry->>'company_id');
  IF TG_TABLE_NAME='bank_accounts' THEN
   SELECT lp.ledger_id,b.period,b.period INTO ledger,first_month,last_month
    FROM accounting.books b JOIN accounting.ledger_periods lp ON lp.book_id=b.id WHERE b.id=entry->>'book_id';
  ELSE
   ledger:=entry->>'ledger_id'; first_month:=entry->>'effective_from'; last_month:=entry->>'effective_to';
  END IF;
  IF EXISTS(SELECT 1 FROM accounting.ledger_periods lp JOIN accounting.books b ON b.id=lp.book_id
    JOIN accounting.periods p ON p.book_id=b.id AND p.period=b.period
    WHERE lp.ledger_id=ledger AND b.period>=first_month AND (last_month IS NULL OR b.period<=last_month)
    AND (p.status<>'open' OR EXISTS(SELECT 1 FROM accounting.internal_period_locks l WHERE l.book_id=b.id AND l.period=b.period))) THEN
   RAISE EXCEPTION 'Configuration affects a locked period' USING ERRCODE='23514';
  END IF;
 END LOOP;
 IF TG_OP='DELETE' THEN RETURN OLD; END IF;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION accounting.dcp_configuration_lock_guard() FROM PUBLIC;
DROP TRIGGER IF EXISTS dcp_control_lock ON accounting.subledger_control_accounts;
CREATE TRIGGER dcp_control_lock BEFORE INSERT OR UPDATE OR DELETE ON accounting.subledger_control_accounts
 FOR EACH ROW EXECUTE FUNCTION accounting.dcp_configuration_lock_guard();
DROP TRIGGER IF EXISTS dcp_applicability_lock ON accounting.subledger_control_applicability;
CREATE TRIGGER dcp_applicability_lock BEFORE INSERT OR UPDATE OR DELETE ON accounting.subledger_control_applicability
 FOR EACH ROW EXECUTE FUNCTION accounting.dcp_configuration_lock_guard();
DROP TRIGGER IF EXISTS dcp_bank_mapping_lock ON accounting.bank_accounts;
CREATE TRIGGER dcp_bank_mapping_lock BEFORE INSERT OR UPDATE OR DELETE ON accounting.bank_accounts
 FOR EACH ROW EXECUTE FUNCTION accounting.dcp_configuration_lock_guard();
