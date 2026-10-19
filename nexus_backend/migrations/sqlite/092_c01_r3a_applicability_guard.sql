-- compatibility: incremental
-- C01-R3A: period-scoped applicability history and SQLite guards.
CREATE TABLE IF NOT EXISTS subledger_control_applicability(
 id TEXT PRIMARY KEY, ledger_id TEXT NOT NULL, firm_id TEXT NOT NULL, company_id TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN('configured','not_applicable')),
 effective_from TEXT NOT NULL, effective_to TEXT,
 reason TEXT NOT NULL, confirmed_by TEXT NOT NULL, confirmed_at TEXT NOT NULL,
 supersedes_id TEXT, created_at TEXT NOT NULL,
 UNIQUE(ledger_id,effective_from),
 CHECK(effective_to IS NULL OR effective_to>=effective_from)
);
CREATE TRIGGER IF NOT EXISTS c01_bank_coverage_lock
BEFORE INSERT ON bank_statement_coverages
WHEN EXISTS(SELECT 1 FROM internal_period_locks WHERE book_id=NEW.book_id AND period=NEW.period)
BEGIN SELECT RAISE(ABORT,'locked period bank coverage is immutable'); END;
