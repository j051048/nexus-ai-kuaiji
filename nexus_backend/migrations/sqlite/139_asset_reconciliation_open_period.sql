-- compatibility: incremental
CREATE TRIGGER IF NOT EXISTS asset_reconciliation_open_run_insert BEFORE INSERT ON periodic_runs
WHEN NEW.reconciliation_json IS NOT NULL AND (NOT EXISTS(SELECT 1 FROM periods WHERE book_id=NEW.book_id AND period=NEW.period AND status='open') OR EXISTS(SELECT 1 FROM internal_period_locks WHERE book_id=NEW.book_id AND period=NEW.period))
BEGIN SELECT RAISE(ABORT,'Reconciliation requires an open unlocked period'); END;
CREATE TRIGGER IF NOT EXISTS asset_reconciliation_open_run_update BEFORE UPDATE ON periodic_runs
WHEN NEW.reconciliation_json IS NOT NULL AND (NOT EXISTS(SELECT 1 FROM periods WHERE book_id=NEW.book_id AND period=NEW.period AND status='open') OR EXISTS(SELECT 1 FROM internal_period_locks WHERE book_id=NEW.book_id AND period=NEW.period))
BEGIN SELECT RAISE(ABORT,'Reconciliation requires an open unlocked period'); END;
CREATE TRIGGER IF NOT EXISTS asset_reconciliation_open_draft_insert BEFORE INSERT ON periodic_drafts
WHEN EXISTS(SELECT 1 FROM periodic_runs WHERE id=NEW.run_id AND reconciliation_json IS NOT NULL) AND (NOT EXISTS(SELECT 1 FROM periods WHERE book_id=NEW.book_id AND period=NEW.period AND status='open') OR EXISTS(SELECT 1 FROM internal_period_locks WHERE book_id=NEW.book_id AND period=NEW.period))
BEGIN SELECT RAISE(ABORT,'Reconciliation requires an open unlocked period'); END;
CREATE TRIGGER IF NOT EXISTS asset_reconciliation_open_draft_update BEFORE UPDATE ON periodic_drafts
WHEN EXISTS(SELECT 1 FROM periodic_runs WHERE id=NEW.run_id AND reconciliation_json IS NOT NULL) AND (NOT EXISTS(SELECT 1 FROM periods WHERE book_id=NEW.book_id AND period=NEW.period AND status='open') OR EXISTS(SELECT 1 FROM internal_period_locks WHERE book_id=NEW.book_id AND period=NEW.period))
BEGIN SELECT RAISE(ABORT,'Reconciliation requires an open unlocked period'); END;
