-- compatibility: breaking
-- Match PostgreSQL173 materialized-value and integer/limit contracts. Exact
-- current sources and actor permissions remain service responsibilities.
CREATE TRIGGER IF NOT EXISTS n02_tax_value_insert
 BEFORE INSERT ON tax_workpapers
 WHEN NEW.output_net_cents NOT BETWEEN -99999999999999 AND 99999999999999 OR NEW.output_tax_cents NOT BETWEEN -99999999999999 AND 99999999999999 OR NEW.input_net_cents NOT BETWEEN -99999999999999 AND 99999999999999 OR NEW.input_tax_cents NOT BETWEEN -99999999999999 AND 99999999999999 OR NEW.tax_cents NOT BETWEEN -99999999999999 AND 99999999999999 OR NEW.surtax_cents NOT BETWEEN -99999999999999 AND 99999999999999 OR NEW.credit_carry_cents NOT BETWEEN -99999999999999 AND 99999999999999 OR NEW.ledger_cents NOT BETWEEN -99999999999999 AND 99999999999999 OR NEW.variance_cents NOT BETWEEN -99999999999999 AND 99999999999999
 OR NEW.variance_cents<>NEW.tax_cents-NEW.ledger_cents
 OR (NOT (NEW.source_snapshot_json='' AND NEW.status='draft') AND
  CASE WHEN json_valid(NEW.source_snapshot_json) AND json_valid(NEW.lines_json) THEN
  json_extract(NEW.source_snapshot_json,'$.schema') IS NOT 'n02-tax-v2'
  OR json_type(NEW.source_snapshot_json,'$.totals') IS NOT 'array'
  OR json_array_length(NEW.source_snapshot_json,'$.totals') IS NOT 8
  OR json_type(NEW.source_snapshot_json,'$.invoices') IS NOT 'array'
  OR json_type(NEW.lines_json) IS NOT 'array'
  OR json_type(NEW.source_snapshot_json,'$.totals[0]') IS NOT 'integer' OR json_extract(NEW.source_snapshot_json,'$.totals[0]') IS NOT NEW.output_net_cents
  OR json_type(NEW.source_snapshot_json,'$.totals[1]') IS NOT 'integer' OR json_extract(NEW.source_snapshot_json,'$.totals[1]') IS NOT NEW.output_tax_cents
  OR json_type(NEW.source_snapshot_json,'$.totals[2]') IS NOT 'integer' OR json_extract(NEW.source_snapshot_json,'$.totals[2]') IS NOT NEW.input_net_cents
  OR json_type(NEW.source_snapshot_json,'$.totals[3]') IS NOT 'integer' OR json_extract(NEW.source_snapshot_json,'$.totals[3]') IS NOT NEW.input_tax_cents
  OR json_type(NEW.source_snapshot_json,'$.totals[4]') IS NOT 'integer' OR json_extract(NEW.source_snapshot_json,'$.totals[4]') IS NOT NEW.tax_cents
  OR json_type(NEW.source_snapshot_json,'$.totals[5]') IS NOT 'integer' OR json_extract(NEW.source_snapshot_json,'$.totals[5]') IS NOT NEW.surtax_cents
  OR json_type(NEW.source_snapshot_json,'$.totals[6]') IS NOT 'integer' OR json_extract(NEW.source_snapshot_json,'$.totals[6]') IS NOT NEW.credit_carry_cents
  OR json_type(NEW.source_snapshot_json,'$.totals[7]') IS NOT 'integer' OR json_extract(NEW.source_snapshot_json,'$.totals[7]') IS NOT NEW.ledger_cents
  OR EXISTS(SELECT fullkey,type,atom FROM json_tree(json_extract(NEW.source_snapshot_json,'$.invoices'))
      EXCEPT SELECT fullkey,type,atom FROM json_tree(NEW.lines_json))
  OR EXISTS(SELECT fullkey,type,atom FROM json_tree(NEW.lines_json)
      EXCEPT SELECT fullkey,type,atom FROM json_tree(json_extract(NEW.source_snapshot_json,'$.invoices')))
  ELSE 1 END)
 BEGIN SELECT RAISE(ABORT,'Tax source snapshot values or variance mismatch'); END;
CREATE TRIGGER IF NOT EXISTS n02_tax_value_update
 BEFORE UPDATE ON tax_workpapers
 WHEN NEW.output_net_cents NOT BETWEEN -99999999999999 AND 99999999999999 OR NEW.output_tax_cents NOT BETWEEN -99999999999999 AND 99999999999999 OR NEW.input_net_cents NOT BETWEEN -99999999999999 AND 99999999999999 OR NEW.input_tax_cents NOT BETWEEN -99999999999999 AND 99999999999999 OR NEW.tax_cents NOT BETWEEN -99999999999999 AND 99999999999999 OR NEW.surtax_cents NOT BETWEEN -99999999999999 AND 99999999999999 OR NEW.credit_carry_cents NOT BETWEEN -99999999999999 AND 99999999999999 OR NEW.ledger_cents NOT BETWEEN -99999999999999 AND 99999999999999 OR NEW.variance_cents NOT BETWEEN -99999999999999 AND 99999999999999
 OR NEW.variance_cents<>NEW.tax_cents-NEW.ledger_cents
 OR (NOT (NEW.source_snapshot_json='' AND NEW.status='draft') AND
  CASE WHEN json_valid(NEW.source_snapshot_json) AND json_valid(NEW.lines_json) THEN
  json_extract(NEW.source_snapshot_json,'$.schema') IS NOT 'n02-tax-v2'
  OR json_type(NEW.source_snapshot_json,'$.totals') IS NOT 'array'
  OR json_array_length(NEW.source_snapshot_json,'$.totals') IS NOT 8
  OR json_type(NEW.source_snapshot_json,'$.invoices') IS NOT 'array'
  OR json_type(NEW.lines_json) IS NOT 'array'
  OR json_type(NEW.source_snapshot_json,'$.totals[0]') IS NOT 'integer' OR json_extract(NEW.source_snapshot_json,'$.totals[0]') IS NOT NEW.output_net_cents
  OR json_type(NEW.source_snapshot_json,'$.totals[1]') IS NOT 'integer' OR json_extract(NEW.source_snapshot_json,'$.totals[1]') IS NOT NEW.output_tax_cents
  OR json_type(NEW.source_snapshot_json,'$.totals[2]') IS NOT 'integer' OR json_extract(NEW.source_snapshot_json,'$.totals[2]') IS NOT NEW.input_net_cents
  OR json_type(NEW.source_snapshot_json,'$.totals[3]') IS NOT 'integer' OR json_extract(NEW.source_snapshot_json,'$.totals[3]') IS NOT NEW.input_tax_cents
  OR json_type(NEW.source_snapshot_json,'$.totals[4]') IS NOT 'integer' OR json_extract(NEW.source_snapshot_json,'$.totals[4]') IS NOT NEW.tax_cents
  OR json_type(NEW.source_snapshot_json,'$.totals[5]') IS NOT 'integer' OR json_extract(NEW.source_snapshot_json,'$.totals[5]') IS NOT NEW.surtax_cents
  OR json_type(NEW.source_snapshot_json,'$.totals[6]') IS NOT 'integer' OR json_extract(NEW.source_snapshot_json,'$.totals[6]') IS NOT NEW.credit_carry_cents
  OR json_type(NEW.source_snapshot_json,'$.totals[7]') IS NOT 'integer' OR json_extract(NEW.source_snapshot_json,'$.totals[7]') IS NOT NEW.ledger_cents
  OR EXISTS(SELECT fullkey,type,atom FROM json_tree(json_extract(NEW.source_snapshot_json,'$.invoices'))
      EXCEPT SELECT fullkey,type,atom FROM json_tree(NEW.lines_json))
  OR EXISTS(SELECT fullkey,type,atom FROM json_tree(NEW.lines_json)
      EXCEPT SELECT fullkey,type,atom FROM json_tree(json_extract(NEW.source_snapshot_json,'$.invoices')))
  ELSE 1 END)
 BEGIN SELECT RAISE(ABORT,'Tax source snapshot values or variance mismatch'); END;
