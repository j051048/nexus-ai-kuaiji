-- compatibility: breaking
-- N02: require a bound source snapshot when reviewing. Exact source bytes,
-- formal-voucher versions and digest are revalidated by the service; SQL also
-- rejects legacy/invalid/foreign snapshot envelopes and keeps 171 immutable.
CREATE OR REPLACE FUNCTION accounting.n02_tax_snapshot_review_guard() RETURNS TRIGGER
 LANGUAGE plpgsql SET search_path=pg_catalog,accounting,pg_temp AS $$
DECLARE source JSONB; ledger TEXT;
BEGIN
 IF NEW.status<>'reviewed' THEN RETURN NEW; END IF;
 BEGIN
  source:=NEW.source_snapshot_json::jsonb;
 EXCEPTION WHEN invalid_text_representation THEN
  RAISE EXCEPTION 'Tax source snapshot is not valid JSON' USING ERRCODE='23514';
 END;
 SELECT ledger_id INTO ledger FROM accounting.ledger_periods
  WHERE book_id=NEW.book_id AND period=NEW.period;
 IF source->>'schema' IS DISTINCT FROM 'n02-tax-v2'
    OR source#>>'{scope,firm_id}' IS DISTINCT FROM NEW.firm_id
    OR source#>>'{scope,company_id}' IS DISTINCT FROM NEW.company_id
    OR source#>>'{scope,book_id}' IS DISTINCT FROM NEW.book_id
    OR source#>>'{scope,period}' IS DISTINCT FROM NEW.period
    OR source->>'period' IS DISTINCT FROM NEW.period
    OR ledger IS NULL OR source#>>'{scope,ledger_id}' IS DISTINCT FROM ledger
    OR source#>>'{profile,id}' IS DISTINCT FROM NEW.profile_id
    OR jsonb_typeof(source->'vat_accounts') IS DISTINCT FROM 'array'
    OR jsonb_typeof(source->'invoices') IS DISTINCT FROM 'array'
    OR jsonb_typeof(source#>'{ledger,vouchers}') IS DISTINCT FROM 'array'
    OR jsonb_typeof(source#>'{ledger,lines}') IS DISTINCT FROM 'array'
    OR jsonb_typeof(source->'totals') IS DISTINCT FROM 'array' THEN
  RAISE EXCEPTION 'Tax source snapshot schema or scope is invalid' USING ERRCODE='23514';
 END IF;
 IF jsonb_array_length(source->'totals')<>8 THEN
  RAISE EXCEPTION 'Tax source snapshot totals shape is invalid' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION accounting.n02_tax_snapshot_review_guard() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION accounting.n02_tax_snapshot_review_guard() TO accounting_app;
DROP TRIGGER IF EXISTS zzz_n02_tax_snapshot_review_guard ON accounting.tax_workpapers;
CREATE TRIGGER zzz_n02_tax_snapshot_review_guard BEFORE UPDATE ON accounting.tax_workpapers
 FOR EACH ROW EXECUTE FUNCTION accounting.n02_tax_snapshot_review_guard();
