-- compatibility: incremental
-- C01-R3A: period-scoped applicability history and write guards.
CREATE TABLE IF NOT EXISTS accounting.subledger_control_applicability(
 id TEXT PRIMARY KEY, ledger_id TEXT NOT NULL REFERENCES accounting.ledgers(id),
 firm_id TEXT NOT NULL REFERENCES accounting.firms(id),
 company_id TEXT NOT NULL REFERENCES accounting.companies(id),
 status TEXT NOT NULL CHECK(status IN('configured','not_applicable')),
 effective_from TEXT NOT NULL, effective_to TEXT,
 reason TEXT NOT NULL, confirmed_by TEXT NOT NULL REFERENCES accounting.users(id),
 confirmed_at TEXT NOT NULL, supersedes_id TEXT REFERENCES accounting.subledger_control_applicability(id),
 created_at TEXT NOT NULL,
 UNIQUE(ledger_id,effective_from),
 CHECK(effective_to IS NULL OR effective_to>=effective_from)
);
ALTER TABLE accounting.subledger_control_applicability ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.subledger_control_applicability FORCE ROW LEVEL SECURITY;
CREATE POLICY subledger_applicability_read ON accounting.subledger_control_applicability
 FOR SELECT TO accounting_app USING(EXISTS(
  SELECT 1 FROM accounting.ledger_periods p
  WHERE p.ledger_id=subledger_control_applicability.ledger_id AND accounting.book_allowed(p.book_id)));
CREATE POLICY subledger_applicability_write ON accounting.subledger_control_applicability
 FOR ALL TO accounting_app USING(EXISTS(
  SELECT 1 FROM accounting.ledger_periods p
  WHERE p.ledger_id=subledger_control_applicability.ledger_id AND accounting.book_allowed(p.book_id,true)))
 WITH CHECK(EXISTS(
  SELECT 1 FROM accounting.ledger_periods p
  WHERE p.ledger_id=subledger_control_applicability.ledger_id AND accounting.book_allowed(p.book_id,true)));
GRANT SELECT,INSERT ON accounting.subledger_control_applicability TO accounting_app;

CREATE OR REPLACE FUNCTION accounting.subledger_control_identity_guard() RETURNS TRIGGER
 LANGUAGE plpgsql SET search_path=pg_catalog,accounting,pg_temp AS $$
BEGIN
 IF TG_TABLE_NAME='subledger_control_applicability' AND NEW.confirmed_by IS DISTINCT FROM accounting.actor_user() THEN
  RAISE EXCEPTION 'confirmed_by must be current authenticated actor' USING ERRCODE='42501';
 END IF;
 IF TG_TABLE_NAME='subledger_control_accounts' AND NEW.created_by IS DISTINCT FROM accounting.actor_user() THEN
  RAISE EXCEPTION 'created_by must be current authenticated actor' USING ERRCODE='42501';
 END IF;
 IF TG_TABLE_NAME='bank_accounts' AND NEW.created_by IS DISTINCT FROM accounting.actor_user() THEN
  RAISE EXCEPTION 'created_by must be current authenticated actor' USING ERRCODE='42501';
 END IF;
 IF TG_TABLE_NAME='bank_statement_coverages' AND NEW.verified_by IS DISTINCT FROM accounting.actor_user() THEN
  RAISE EXCEPTION 'verified_by must be current authenticated actor' USING ERRCODE='42501';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER c01_control_identity BEFORE INSERT OR UPDATE ON accounting.subledger_control_accounts
 FOR EACH ROW EXECUTE FUNCTION accounting.subledger_control_identity_guard();
CREATE TRIGGER c01_applicability_identity BEFORE INSERT OR UPDATE ON accounting.subledger_control_applicability
 FOR EACH ROW EXECUTE FUNCTION accounting.subledger_control_identity_guard();
CREATE TRIGGER c01_bank_account_identity BEFORE INSERT OR UPDATE ON accounting.bank_accounts
 FOR EACH ROW EXECUTE FUNCTION accounting.subledger_control_identity_guard();
CREATE TRIGGER c01_bank_coverage_identity BEFORE INSERT OR UPDATE ON accounting.bank_statement_coverages
 FOR EACH ROW EXECUTE FUNCTION accounting.subledger_control_identity_guard();

CREATE OR REPLACE FUNCTION accounting.bank_coverage_lock_guard() RETURNS TRIGGER
 LANGUAGE plpgsql SET search_path=pg_catalog,accounting,pg_temp AS $$
BEGIN
 IF EXISTS(SELECT 1 FROM accounting.internal_period_locks
  WHERE book_id=NEW.book_id AND period=NEW.period) THEN
  RAISE EXCEPTION 'locked period bank coverage is immutable' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER c01_bank_coverage_lock BEFORE INSERT OR UPDATE ON accounting.bank_statement_coverages
 FOR EACH ROW EXECUTE FUNCTION accounting.bank_coverage_lock_guard();

CREATE TRIGGER c01_control_company_gate BEFORE INSERT OR UPDATE OR DELETE ON accounting.subledger_control_accounts
 FOR EACH ROW EXECUTE FUNCTION accounting.company_write_table_guard();
CREATE TRIGGER c01_applicability_company_gate BEFORE INSERT OR UPDATE OR DELETE ON accounting.subledger_control_applicability
 FOR EACH ROW EXECUTE FUNCTION accounting.company_write_table_guard();
CREATE TRIGGER c01_bank_account_company_gate BEFORE INSERT OR UPDATE OR DELETE ON accounting.bank_accounts
 FOR EACH ROW EXECUTE FUNCTION accounting.company_write_table_guard();
CREATE TRIGGER c01_bank_coverage_company_gate BEFORE INSERT OR UPDATE OR DELETE ON accounting.bank_statement_coverages
 FOR EACH ROW EXECUTE FUNCTION accounting.company_write_table_guard();
