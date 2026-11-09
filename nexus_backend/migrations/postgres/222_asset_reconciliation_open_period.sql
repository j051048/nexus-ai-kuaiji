-- compatibility: incremental
CREATE OR REPLACE FUNCTION accounting.asset_reconciliation_open_period() RETURNS trigger
 LANGUAGE plpgsql SET search_path=pg_catalog,accounting,pg_temp AS $$
DECLARE required boolean;
BEGIN
 IF TG_TABLE_NAME='periodic_runs' THEN
  required:=NEW.reconciliation_json IS NOT NULL;
 ELSE
  SELECT reconciliation_json IS NOT NULL INTO required FROM accounting.periodic_runs WHERE id=NEW.run_id;
 END IF;
 IF required AND (NOT EXISTS(SELECT 1 FROM accounting.periods WHERE book_id=NEW.book_id AND period=NEW.period AND status='open')
  OR EXISTS(SELECT 1 FROM accounting.internal_period_locks WHERE book_id=NEW.book_id AND period=NEW.period)) THEN
  RAISE EXCEPTION 'Reconciliation requires an open unlocked period' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION accounting.asset_reconciliation_open_period() FROM PUBLIC;
DROP TRIGGER IF EXISTS asset_reconciliation_open_period ON accounting.periodic_runs;
CREATE TRIGGER asset_reconciliation_open_period BEFORE INSERT OR UPDATE ON accounting.periodic_runs
 FOR EACH ROW EXECUTE FUNCTION accounting.asset_reconciliation_open_period();
DROP TRIGGER IF EXISTS asset_reconciliation_open_period ON accounting.periodic_drafts;
CREATE TRIGGER asset_reconciliation_open_period BEFORE INSERT OR UPDATE ON accounting.periodic_drafts
 FOR EACH ROW EXECUTE FUNCTION accounting.asset_reconciliation_open_period();
