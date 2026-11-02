-- compatibility: incremental
-- Forward native locked-period backstops.

CREATE TRIGGER IF NOT EXISTS dcp_subledger_control_accounts_insert BEFORE INSERT ON subledger_control_accounts
WHEN EXISTS(SELECT 1 FROM ledger_periods lp JOIN books b ON b.id=lp.book_id JOIN periods p ON p.book_id=b.id AND p.period=b.period WHERE lp.ledger_id=NEW.ledger_id AND b.period>=NEW.effective_from AND (NEW.effective_to IS NULL OR b.period<=NEW.effective_to) AND (p.status<>'open' OR EXISTS(SELECT 1 FROM internal_period_locks l WHERE l.book_id=b.id AND l.period=b.period)))
BEGIN SELECT RAISE(ABORT,'Configuration affects a locked period'); END;

CREATE TRIGGER IF NOT EXISTS dcp_subledger_control_accounts_update BEFORE UPDATE ON subledger_control_accounts
WHEN EXISTS(SELECT 1 FROM ledger_periods lp JOIN books b ON b.id=lp.book_id JOIN periods p ON p.book_id=b.id AND p.period=b.period WHERE lp.ledger_id=OLD.ledger_id AND b.period>=OLD.effective_from AND (OLD.effective_to IS NULL OR b.period<=OLD.effective_to) AND (p.status<>'open' OR EXISTS(SELECT 1 FROM internal_period_locks l WHERE l.book_id=b.id AND l.period=b.period))) OR EXISTS(SELECT 1 FROM ledger_periods lp JOIN books b ON b.id=lp.book_id JOIN periods p ON p.book_id=b.id AND p.period=b.period WHERE lp.ledger_id=NEW.ledger_id AND b.period>=NEW.effective_from AND (NEW.effective_to IS NULL OR b.period<=NEW.effective_to) AND (p.status<>'open' OR EXISTS(SELECT 1 FROM internal_period_locks l WHERE l.book_id=b.id AND l.period=b.period)))
BEGIN SELECT RAISE(ABORT,'Configuration affects a locked period'); END;

CREATE TRIGGER IF NOT EXISTS dcp_subledger_control_accounts_delete BEFORE DELETE ON subledger_control_accounts
WHEN EXISTS(SELECT 1 FROM ledger_periods lp JOIN books b ON b.id=lp.book_id JOIN periods p ON p.book_id=b.id AND p.period=b.period WHERE lp.ledger_id=OLD.ledger_id AND b.period>=OLD.effective_from AND (OLD.effective_to IS NULL OR b.period<=OLD.effective_to) AND (p.status<>'open' OR EXISTS(SELECT 1 FROM internal_period_locks l WHERE l.book_id=b.id AND l.period=b.period)))
BEGIN SELECT RAISE(ABORT,'Configuration affects a locked period'); END;

CREATE TRIGGER IF NOT EXISTS dcp_subledger_control_applicability_insert BEFORE INSERT ON subledger_control_applicability
WHEN EXISTS(SELECT 1 FROM ledger_periods lp JOIN books b ON b.id=lp.book_id JOIN periods p ON p.book_id=b.id AND p.period=b.period WHERE lp.ledger_id=NEW.ledger_id AND b.period>=NEW.effective_from AND (NEW.effective_to IS NULL OR b.period<=NEW.effective_to) AND (p.status<>'open' OR EXISTS(SELECT 1 FROM internal_period_locks l WHERE l.book_id=b.id AND l.period=b.period)))
BEGIN SELECT RAISE(ABORT,'Configuration affects a locked period'); END;

CREATE TRIGGER IF NOT EXISTS dcp_subledger_control_applicability_update BEFORE UPDATE ON subledger_control_applicability
WHEN EXISTS(SELECT 1 FROM ledger_periods lp JOIN books b ON b.id=lp.book_id JOIN periods p ON p.book_id=b.id AND p.period=b.period WHERE lp.ledger_id=OLD.ledger_id AND b.period>=OLD.effective_from AND (OLD.effective_to IS NULL OR b.period<=OLD.effective_to) AND (p.status<>'open' OR EXISTS(SELECT 1 FROM internal_period_locks l WHERE l.book_id=b.id AND l.period=b.period))) OR EXISTS(SELECT 1 FROM ledger_periods lp JOIN books b ON b.id=lp.book_id JOIN periods p ON p.book_id=b.id AND p.period=b.period WHERE lp.ledger_id=NEW.ledger_id AND b.period>=NEW.effective_from AND (NEW.effective_to IS NULL OR b.period<=NEW.effective_to) AND (p.status<>'open' OR EXISTS(SELECT 1 FROM internal_period_locks l WHERE l.book_id=b.id AND l.period=b.period)))
BEGIN SELECT RAISE(ABORT,'Configuration affects a locked period'); END;

CREATE TRIGGER IF NOT EXISTS dcp_subledger_control_applicability_delete BEFORE DELETE ON subledger_control_applicability
WHEN EXISTS(SELECT 1 FROM ledger_periods lp JOIN books b ON b.id=lp.book_id JOIN periods p ON p.book_id=b.id AND p.period=b.period WHERE lp.ledger_id=OLD.ledger_id AND b.period>=OLD.effective_from AND (OLD.effective_to IS NULL OR b.period<=OLD.effective_to) AND (p.status<>'open' OR EXISTS(SELECT 1 FROM internal_period_locks l WHERE l.book_id=b.id AND l.period=b.period)))
BEGIN SELECT RAISE(ABORT,'Configuration affects a locked period'); END;

CREATE TRIGGER IF NOT EXISTS dcp_bank_accounts_insert BEFORE INSERT ON bank_accounts
WHEN EXISTS(SELECT 1 FROM ledger_periods lp JOIN books b ON b.id=lp.book_id JOIN periods p ON p.book_id=b.id AND p.period=b.period WHERE b.id=NEW.book_id AND (p.status<>'open' OR EXISTS(SELECT 1 FROM internal_period_locks l WHERE l.book_id=b.id AND l.period=b.period)))
BEGIN SELECT RAISE(ABORT,'Configuration affects a locked period'); END;

CREATE TRIGGER IF NOT EXISTS dcp_bank_accounts_update BEFORE UPDATE ON bank_accounts
WHEN EXISTS(SELECT 1 FROM ledger_periods lp JOIN books b ON b.id=lp.book_id JOIN periods p ON p.book_id=b.id AND p.period=b.period WHERE b.id=OLD.book_id AND (p.status<>'open' OR EXISTS(SELECT 1 FROM internal_period_locks l WHERE l.book_id=b.id AND l.period=b.period))) OR EXISTS(SELECT 1 FROM ledger_periods lp JOIN books b ON b.id=lp.book_id JOIN periods p ON p.book_id=b.id AND p.period=b.period WHERE b.id=NEW.book_id AND (p.status<>'open' OR EXISTS(SELECT 1 FROM internal_period_locks l WHERE l.book_id=b.id AND l.period=b.period)))
BEGIN SELECT RAISE(ABORT,'Configuration affects a locked period'); END;

CREATE TRIGGER IF NOT EXISTS dcp_bank_accounts_delete BEFORE DELETE ON bank_accounts
WHEN EXISTS(SELECT 1 FROM ledger_periods lp JOIN books b ON b.id=lp.book_id JOIN periods p ON p.book_id=b.id AND p.period=b.period WHERE b.id=OLD.book_id AND (p.status<>'open' OR EXISTS(SELECT 1 FROM internal_period_locks l WHERE l.book_id=b.id AND l.period=b.period)))
BEGIN SELECT RAISE(ABORT,'Configuration affects a locked period'); END;

CREATE TRIGGER IF NOT EXISTS dcp_filing_date BEFORE INSERT ON tax_filings WHEN date(NEW.occurred_at,'+0 days') IS NULL OR date(NEW.occurred_at,'+0 days')<>NEW.occurred_at BEGIN SELECT RAISE(ABORT,'Invalid filing date'); END;
