-- compatibility: incremental
-- C01-R3A: dependency trigger, ownership validation and lock guard for
-- period-scoped applicability / bank coverage. Forward-only.
CREATE TRIGGER c01_applicability_dependency_revision
 AFTER INSERT OR UPDATE OR DELETE ON accounting.subledger_control_applicability
 FOR EACH ROW EXECUTE FUNCTION accounting.close_dependency_bump('ledger');

DROP TRIGGER IF EXISTS c01_bank_coverage_company_gate ON accounting.bank_statement_coverages;
CREATE OR REPLACE FUNCTION accounting.c01_bank_coverage_company_guard() RETURNS TRIGGER
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
DECLARE b RECORD; a RECORD;
BEGIN
 SELECT company_id,firm_id INTO b FROM accounting.books
  WHERE id=CASE WHEN TG_OP='DELETE' THEN OLD.book_id ELSE NEW.book_id END;
 IF b IS NULL THEN RAISE EXCEPTION 'Bank coverage book not found' USING ERRCODE='23503'; END IF;
 SELECT company_id,book_id INTO a FROM accounting.bank_accounts
  WHERE id=CASE WHEN TG_OP='DELETE' THEN OLD.account_id ELSE NEW.account_id END;
 IF a IS NULL OR a.book_id IS DISTINCT FROM (CASE WHEN TG_OP='DELETE' THEN OLD.book_id ELSE NEW.book_id END)
  OR a.company_id IS DISTINCT FROM b.company_id THEN
  RAISE EXCEPTION 'Bank coverage account/book/company mismatch' USING ERRCODE='23514';
 END IF;
 IF coalesce(current_setting('app.write_scope_company',true),'')='' THEN
  PERFORM accounting.lock_legacy_write();
 ELSIF b.company_id IS DISTINCT FROM current_setting('app.write_scope_company',true)
  OR b.firm_id IS DISTINCT FROM current_setting('app.write_scope_firm',true) THEN
  RAISE EXCEPTION 'Bank coverage exceeds company scope' USING ERRCODE='42501';
 END IF;
 RETURN CASE WHEN TG_OP='DELETE' THEN OLD ELSE NEW END;
END $$;
CREATE TRIGGER c01_bank_coverage_company_guard BEFORE INSERT OR UPDATE OR DELETE
 ON accounting.bank_statement_coverages FOR EACH ROW
 EXECUTE FUNCTION accounting.c01_bank_coverage_company_guard();

CREATE OR REPLACE FUNCTION accounting.c01_bank_coverage_immutable() RETURNS TRIGGER
 LANGUAGE plpgsql SET search_path=pg_catalog,accounting,pg_temp AS $$
BEGIN
 RAISE EXCEPTION 'Bank coverage is append-only; create a new verified version' USING ERRCODE='23514';
END $$;
CREATE TRIGGER c01_bank_coverage_append_only BEFORE UPDATE OR DELETE
 ON accounting.bank_statement_coverages FOR EACH ROW
 EXECUTE FUNCTION accounting.c01_bank_coverage_immutable();

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
   SELECT 1 FROM accounting.subledger_control_applicability old
   WHERE old.id=NEW.supersedes_id AND old.ledger_id=NEW.ledger_id
    AND old.firm_id=NEW.firm_id AND old.company_id=NEW.company_id)
  THEN RAISE EXCEPTION 'Supersedes reference crosses ledger/company' USING ERRCODE='23514'; END IF;
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER c01_control_ref_guard BEFORE INSERT OR UPDATE ON accounting.subledger_control_accounts
 FOR EACH ROW EXECUTE FUNCTION accounting.c01_control_ref_guard();
CREATE TRIGGER c01_bank_account_ref_guard BEFORE INSERT OR UPDATE ON accounting.bank_accounts
 FOR EACH ROW EXECUTE FUNCTION accounting.c01_control_ref_guard();
CREATE TRIGGER c01_applicability_ref_guard BEFORE INSERT ON accounting.subledger_control_applicability
 FOR EACH ROW EXECUTE FUNCTION accounting.c01_control_ref_guard();

CREATE OR REPLACE FUNCTION accounting.c01_control_immutable() RETURNS TRIGGER
 LANGUAGE plpgsql SET search_path=pg_catalog,accounting,pg_temp AS $$
BEGIN
 IF NEW.firm_id IS DISTINCT FROM OLD.firm_id OR NEW.company_id IS DISTINCT FROM OLD.company_id
  OR NEW.ledger_id IS DISTINCT FROM OLD.ledger_id
  OR to_jsonb(NEW)->>'created_by' IS DISTINCT FROM to_jsonb(OLD)->>'created_by' THEN
  RAISE EXCEPTION 'Control identity fields are immutable' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER c01_control_immutable BEFORE UPDATE ON accounting.subledger_control_accounts
 FOR EACH ROW EXECUTE FUNCTION accounting.c01_control_immutable();
CREATE TRIGGER c01_applicability_immutable BEFORE UPDATE ON accounting.subledger_control_applicability
 FOR EACH ROW EXECUTE FUNCTION accounting.c01_control_immutable();
