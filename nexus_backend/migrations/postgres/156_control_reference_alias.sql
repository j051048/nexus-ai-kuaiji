-- compatibility: incremental
-- The SQL alias OLD conflicts with PL/pgSQL's trigger record. Preserve every
-- existing ownership/version check and change only that alias, forward-only.
CREATE OR REPLACE FUNCTION accounting.c01_control_ref_guard() RETURNS TRIGGER
 LANGUAGE plpgsql SET search_path=pg_catalog,accounting,pg_temp AS $$
DECLARE v JSONB;
BEGIN
 v:=to_jsonb(NEW);
 IF TG_TABLE_NAME='subledger_control_accounts' THEN
  IF NOT EXISTS(SELECT 1 FROM accounting.ledger_accounts la
   WHERE la.id=v->>'control_account_id' AND la.ledger_id=v->>'ledger_id'
   AND la.current_version=(v->>'control_account_version')::int)
  THEN RAISE EXCEPTION 'Control account/version does not belong to ledger' USING ERRCODE='23514'; END IF;
  IF NOT EXISTS(SELECT 1 FROM accounting.ledgers l WHERE l.id=v->>'ledger_id'
   AND l.firm_id=v->>'firm_id' AND l.company_id=v->>'company_id')
  THEN RAISE EXCEPTION 'Control config ownership mismatch' USING ERRCODE='23514'; END IF;
 ELSIF TG_TABLE_NAME='bank_accounts' THEN
  IF NOT EXISTS(SELECT 1 FROM accounting.books b WHERE b.id=v->>'book_id'
   AND b.firm_id=v->>'firm_id' AND b.company_id=v->>'company_id')
  THEN RAISE EXCEPTION 'Bank account ownership mismatch' USING ERRCODE='23514'; END IF;
  IF v->>'ledger_account_id' IS NOT NULL AND NOT EXISTS(
   SELECT 1 FROM accounting.ledger_periods p JOIN accounting.ledger_accounts la
    ON la.ledger_id=p.ledger_id AND la.id=v->>'ledger_account_id'
   WHERE p.book_id=v->>'book_id' AND la.current_version=(v->>'ledger_account_version')::int)
  THEN RAISE EXCEPTION 'Bank mapping does not belong to book ledger' USING ERRCODE='23514'; END IF;
 ELSIF TG_TABLE_NAME='subledger_control_applicability' THEN
  IF NEW.supersedes_id IS NOT NULL AND NOT EXISTS(
   SELECT 1 FROM accounting.subledger_control_applicability prior_ref
   WHERE prior_ref.id=NEW.supersedes_id AND prior_ref.ledger_id=NEW.ledger_id
    AND prior_ref.firm_id=NEW.firm_id AND prior_ref.company_id=NEW.company_id)
  THEN RAISE EXCEPTION 'Supersedes reference crosses ledger/company' USING ERRCODE='23514'; END IF;
 END IF;
 RETURN NEW;
END $$;
