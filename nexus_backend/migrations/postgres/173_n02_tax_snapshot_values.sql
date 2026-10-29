-- compatibility: breaking
-- N02: materialized tax fields must match the frozen source envelope. Business
-- derivation and live-source revalidation remain mandatory in the service.
ALTER TABLE accounting.tax_workpapers
 ALTER COLUMN output_net_cents TYPE BIGINT,
 ALTER COLUMN output_tax_cents TYPE BIGINT,
 ALTER COLUMN input_net_cents TYPE BIGINT,
 ALTER COLUMN input_tax_cents TYPE BIGINT,
 ALTER COLUMN tax_cents TYPE BIGINT,
 ALTER COLUMN surtax_cents TYPE BIGINT,
 ALTER COLUMN credit_carry_cents TYPE BIGINT,
 ALTER COLUMN ledger_cents TYPE BIGINT,
 ALTER COLUMN variance_cents TYPE BIGINT;

CREATE OR REPLACE FUNCTION accounting.n02_tax_value_guard() RETURNS TRIGGER
 LANGUAGE plpgsql SET search_path=pg_catalog,accounting,pg_temp AS $$
DECLARE source JSONB; lines JSONB; totals BIGINT[]; value JSONB; i INTEGER;
BEGIN
 totals:=ARRAY[NEW.output_net_cents,NEW.output_tax_cents,NEW.input_net_cents,NEW.input_tax_cents,
               NEW.tax_cents,NEW.surtax_cents,NEW.credit_carry_cents,NEW.ledger_cents];
 FOREACH i IN ARRAY ARRAY[1,2,3,4,5,6,7,8] LOOP
  IF totals[i] < -99999999999999 OR totals[i] > 99999999999999 THEN
   RAISE EXCEPTION 'Tax snapshot amount exceeds accounting limit' USING ERRCODE='23514';
  END IF;
 END LOOP;
 IF NEW.variance_cents < -99999999999999 OR NEW.variance_cents > 99999999999999
    OR NEW.variance_cents<>NEW.tax_cents-NEW.ledger_cents THEN
  RAISE EXCEPTION 'Tax variance must equal tax less formal ledger' USING ERRCODE='23514';
 END IF;
 -- Existing empty drafts stay readable but 172 prohibits their review.
 IF NEW.source_snapshot_json='' AND NEW.status='draft' THEN RETURN NEW; END IF;
 BEGIN
  source:=NEW.source_snapshot_json::jsonb;
  lines:=NEW.lines_json::jsonb;
 EXCEPTION WHEN invalid_text_representation THEN
  RAISE EXCEPTION 'Tax source snapshot or lines are invalid JSON' USING ERRCODE='23514';
 END;
 IF source->>'schema' IS DISTINCT FROM 'n02-tax-v2'
    OR jsonb_typeof(source->'totals') IS DISTINCT FROM 'array'
    OR jsonb_typeof(source->'invoices') IS DISTINCT FROM 'array'
    OR jsonb_typeof(lines) IS DISTINCT FROM 'array' THEN
  RAISE EXCEPTION 'Tax source snapshot value shape is invalid' USING ERRCODE='23514';
 END IF;
 IF jsonb_array_length(source->'totals')<>8 THEN
  RAISE EXCEPTION 'Tax source snapshot must contain eight totals' USING ERRCODE='23514';
 END IF;
 FOR i IN 1..8 LOOP
  value:=source->'totals'->(i-1);
  IF jsonb_typeof(value) IS DISTINCT FROM 'number'
     OR value::TEXT !~ '^-?(0|[1-9][0-9]*)$' THEN
   RAISE EXCEPTION 'Tax source snapshot totals must be strict integers' USING ERRCODE='23514';
  END IF;
  IF value::TEXT::NUMERIC<>totals[i] THEN
   RAISE EXCEPTION 'Tax source snapshot totals do not match stored fields' USING ERRCODE='23514';
  END IF;
 END LOOP;
 IF source->'invoices' IS DISTINCT FROM lines THEN
  RAISE EXCEPTION 'Tax source snapshot invoices do not match stored lines' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION accounting.n02_tax_value_guard() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION accounting.n02_tax_value_guard() TO accounting_app;
DROP TRIGGER IF EXISTS zzzz_n02_tax_value_guard ON accounting.tax_workpapers;
CREATE TRIGGER zzzz_n02_tax_value_guard BEFORE INSERT OR UPDATE ON accounting.tax_workpapers
 FOR EACH ROW EXECUTE FUNCTION accounting.n02_tax_value_guard();
