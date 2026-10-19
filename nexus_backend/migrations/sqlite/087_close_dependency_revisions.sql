-- compatibility: breaking
-- F03: monotonic close revisions. Existing runs have no valid frozen vector.
ALTER TABLE internal_close_runs ADD COLUMN dependencies_json TEXT NOT NULL DEFAULT '{}';
CREATE TABLE close_dependency_revisions(
 scope_kind TEXT NOT NULL CHECK(scope_kind IN('book','ledger','template')),
 scope_id TEXT NOT NULL, revision INTEGER NOT NULL CHECK(revision>0),
 PRIMARY KEY(scope_kind,scope_id)
);

CREATE TRIGGER p0_close_revision_books_insert AFTER INSERT ON books
BEGIN
 INSERT INTO close_dependency_revisions(scope_kind,scope_id,revision) VALUES('book',NEW.id,1)
 ON CONFLICT(scope_kind,scope_id) DO UPDATE SET revision=revision+1;
END;

CREATE TRIGGER p0_close_revision_books_update AFTER UPDATE ON books
BEGIN
 INSERT INTO close_dependency_revisions(scope_kind,scope_id,revision) VALUES('book',NEW.id,1)
 ON CONFLICT(scope_kind,scope_id) DO UPDATE SET revision=revision+1;
END;
CREATE TRIGGER p0_close_scope_books BEFORE UPDATE ON books
WHEN NEW.id IS NOT OLD.id
BEGIN SELECT RAISE(ABORT,'Close dependency scope cannot be changed'); END;

CREATE TRIGGER p0_close_revision_books_delete AFTER DELETE ON books
BEGIN
 INSERT INTO close_dependency_revisions(scope_kind,scope_id,revision) VALUES('book',OLD.id,1)
 ON CONFLICT(scope_kind,scope_id) DO UPDATE SET revision=revision+1;
END;

CREATE TRIGGER p0_close_revision_periods_insert AFTER INSERT ON periods
BEGIN
 INSERT INTO close_dependency_revisions(scope_kind,scope_id,revision) VALUES('book',NEW.book_id,1)
 ON CONFLICT(scope_kind,scope_id) DO UPDATE SET revision=revision+1;
END;

CREATE TRIGGER p0_close_revision_periods_update AFTER UPDATE ON periods
BEGIN
 INSERT INTO close_dependency_revisions(scope_kind,scope_id,revision) VALUES('book',NEW.book_id,1)
 ON CONFLICT(scope_kind,scope_id) DO UPDATE SET revision=revision+1;
END;
CREATE TRIGGER p0_close_scope_periods BEFORE UPDATE ON periods
WHEN NEW.book_id IS NOT OLD.book_id
BEGIN SELECT RAISE(ABORT,'Close dependency scope cannot be changed'); END;

CREATE TRIGGER p0_close_revision_periods_delete AFTER DELETE ON periods
BEGIN
 INSERT INTO close_dependency_revisions(scope_kind,scope_id,revision) VALUES('book',OLD.book_id,1)
 ON CONFLICT(scope_kind,scope_id) DO UPDATE SET revision=revision+1;
END;

CREATE TRIGGER p0_close_revision_accounts_insert AFTER INSERT ON accounts
BEGIN
 INSERT INTO close_dependency_revisions(scope_kind,scope_id,revision) VALUES('book',NEW.book_id,1)
 ON CONFLICT(scope_kind,scope_id) DO UPDATE SET revision=revision+1;
END;

CREATE TRIGGER p0_close_revision_accounts_update AFTER UPDATE ON accounts
BEGIN
 INSERT INTO close_dependency_revisions(scope_kind,scope_id,revision) VALUES('book',NEW.book_id,1)
 ON CONFLICT(scope_kind,scope_id) DO UPDATE SET revision=revision+1;
END;
CREATE TRIGGER p0_close_scope_accounts BEFORE UPDATE ON accounts
WHEN NEW.book_id IS NOT OLD.book_id
BEGIN SELECT RAISE(ABORT,'Close dependency scope cannot be changed'); END;

CREATE TRIGGER p0_close_revision_accounts_delete AFTER DELETE ON accounts
BEGIN
 INSERT INTO close_dependency_revisions(scope_kind,scope_id,revision) VALUES('book',OLD.book_id,1)
 ON CONFLICT(scope_kind,scope_id) DO UPDATE SET revision=revision+1;
END;

CREATE TRIGGER p0_close_revision_sources_insert AFTER INSERT ON sources
BEGIN
 INSERT INTO close_dependency_revisions(scope_kind,scope_id,revision) VALUES('book',NEW.book_id,1)
 ON CONFLICT(scope_kind,scope_id) DO UPDATE SET revision=revision+1;
END;
CREATE TRIGGER p0_close_locked_sources_insert BEFORE INSERT ON sources
WHEN EXISTS(SELECT 1 FROM internal_period_locks WHERE book_id=NEW.book_id)
BEGIN SELECT RAISE(ABORT,'Internally locked period evidence cannot change'); END;

CREATE TRIGGER p0_close_revision_sources_update AFTER UPDATE ON sources
BEGIN
 INSERT INTO close_dependency_revisions(scope_kind,scope_id,revision) VALUES('book',NEW.book_id,1)
 ON CONFLICT(scope_kind,scope_id) DO UPDATE SET revision=revision+1;
END;
CREATE TRIGGER p0_close_scope_sources BEFORE UPDATE ON sources
WHEN NEW.book_id IS NOT OLD.book_id
BEGIN SELECT RAISE(ABORT,'Close dependency scope cannot be changed'); END;
CREATE TRIGGER p0_close_locked_sources_update BEFORE UPDATE ON sources
WHEN EXISTS(SELECT 1 FROM internal_period_locks WHERE book_id=NEW.book_id)
BEGIN SELECT RAISE(ABORT,'Internally locked period evidence cannot change'); END;

CREATE TRIGGER p0_close_revision_sources_delete AFTER DELETE ON sources
BEGIN
 INSERT INTO close_dependency_revisions(scope_kind,scope_id,revision) VALUES('book',OLD.book_id,1)
 ON CONFLICT(scope_kind,scope_id) DO UPDATE SET revision=revision+1;
END;
CREATE TRIGGER p0_close_locked_sources_delete BEFORE DELETE ON sources
WHEN EXISTS(SELECT 1 FROM internal_period_locks WHERE book_id=OLD.book_id)
BEGIN SELECT RAISE(ABORT,'Internally locked period evidence cannot change'); END;

CREATE TRIGGER p0_close_revision_source_evidence_insert AFTER INSERT ON source_evidence
BEGIN
 INSERT INTO close_dependency_revisions(scope_kind,scope_id,revision) VALUES('book',NEW.book_id,1)
 ON CONFLICT(scope_kind,scope_id) DO UPDATE SET revision=revision+1;
END;
CREATE TRIGGER p0_close_locked_source_evidence_insert BEFORE INSERT ON source_evidence
WHEN EXISTS(SELECT 1 FROM internal_period_locks WHERE book_id=NEW.book_id)
BEGIN SELECT RAISE(ABORT,'Internally locked period evidence cannot change'); END;

CREATE TRIGGER p0_close_revision_source_evidence_update AFTER UPDATE ON source_evidence
BEGIN
 INSERT INTO close_dependency_revisions(scope_kind,scope_id,revision) VALUES('book',NEW.book_id,1)
 ON CONFLICT(scope_kind,scope_id) DO UPDATE SET revision=revision+1;
END;
CREATE TRIGGER p0_close_scope_source_evidence BEFORE UPDATE ON source_evidence
WHEN NEW.book_id IS NOT OLD.book_id
BEGIN SELECT RAISE(ABORT,'Close dependency scope cannot be changed'); END;
CREATE TRIGGER p0_close_locked_source_evidence_update BEFORE UPDATE ON source_evidence
WHEN EXISTS(SELECT 1 FROM internal_period_locks WHERE book_id=NEW.book_id)
BEGIN SELECT RAISE(ABORT,'Internally locked period evidence cannot change'); END;

CREATE TRIGGER p0_close_revision_source_evidence_delete AFTER DELETE ON source_evidence
BEGIN
 INSERT INTO close_dependency_revisions(scope_kind,scope_id,revision) VALUES('book',OLD.book_id,1)
 ON CONFLICT(scope_kind,scope_id) DO UPDATE SET revision=revision+1;
END;
CREATE TRIGGER p0_close_locked_source_evidence_delete BEFORE DELETE ON source_evidence
WHEN EXISTS(SELECT 1 FROM internal_period_locks WHERE book_id=OLD.book_id)
BEGIN SELECT RAISE(ABORT,'Internally locked period evidence cannot change'); END;

CREATE TRIGGER p0_close_revision_drafts_insert AFTER INSERT ON drafts
BEGIN
 INSERT INTO close_dependency_revisions(scope_kind,scope_id,revision) VALUES('book',NEW.book_id,1)
 ON CONFLICT(scope_kind,scope_id) DO UPDATE SET revision=revision+1;
END;
CREATE TRIGGER p0_close_locked_drafts_insert BEFORE INSERT ON drafts
WHEN EXISTS(SELECT 1 FROM internal_period_locks WHERE book_id=NEW.book_id)
BEGIN SELECT RAISE(ABORT,'Internally locked period evidence cannot change'); END;

CREATE TRIGGER p0_close_revision_drafts_update AFTER UPDATE ON drafts
BEGIN
 INSERT INTO close_dependency_revisions(scope_kind,scope_id,revision) VALUES('book',NEW.book_id,1)
 ON CONFLICT(scope_kind,scope_id) DO UPDATE SET revision=revision+1;
END;
CREATE TRIGGER p0_close_scope_drafts BEFORE UPDATE ON drafts
WHEN NEW.book_id IS NOT OLD.book_id
BEGIN SELECT RAISE(ABORT,'Close dependency scope cannot be changed'); END;
CREATE TRIGGER p0_close_locked_drafts_update BEFORE UPDATE ON drafts
WHEN EXISTS(SELECT 1 FROM internal_period_locks WHERE book_id=NEW.book_id) AND NOT(OLD.status='approved' AND NEW.status='exported' AND NEW.book_id=OLD.book_id AND NEW.source_id=OLD.source_id AND NEW.version=OLD.version+1 AND NEW.id=OLD.id AND NEW.last_manual_edit_version=OLD.last_manual_edit_version AND NEW.description=OLD.description AND NEW.explanation=OLD.explanation AND NEW.approved_by IS OLD.approved_by AND NEW.approved_at IS OLD.approved_at)
BEGIN SELECT RAISE(ABORT,'Internally locked period evidence cannot change'); END;

CREATE TRIGGER p0_close_revision_drafts_delete AFTER DELETE ON drafts
BEGIN
 INSERT INTO close_dependency_revisions(scope_kind,scope_id,revision) VALUES('book',OLD.book_id,1)
 ON CONFLICT(scope_kind,scope_id) DO UPDATE SET revision=revision+1;
END;
CREATE TRIGGER p0_close_locked_drafts_delete BEFORE DELETE ON drafts
WHEN EXISTS(SELECT 1 FROM internal_period_locks WHERE book_id=OLD.book_id)
BEGIN SELECT RAISE(ABORT,'Internally locked period evidence cannot change'); END;

CREATE TRIGGER p0_close_revision_draft_lines_insert AFTER INSERT ON draft_lines
BEGIN
 INSERT INTO close_dependency_revisions(scope_kind,scope_id,revision) VALUES('book',NEW.book_id,1)
 ON CONFLICT(scope_kind,scope_id) DO UPDATE SET revision=revision+1;
END;
CREATE TRIGGER p0_close_locked_draft_lines_insert BEFORE INSERT ON draft_lines
WHEN EXISTS(SELECT 1 FROM internal_period_locks WHERE book_id=NEW.book_id)
BEGIN SELECT RAISE(ABORT,'Internally locked period evidence cannot change'); END;

CREATE TRIGGER p0_close_revision_draft_lines_update AFTER UPDATE ON draft_lines
BEGIN
 INSERT INTO close_dependency_revisions(scope_kind,scope_id,revision) VALUES('book',NEW.book_id,1)
 ON CONFLICT(scope_kind,scope_id) DO UPDATE SET revision=revision+1;
END;
CREATE TRIGGER p0_close_scope_draft_lines BEFORE UPDATE ON draft_lines
WHEN NEW.book_id IS NOT OLD.book_id
BEGIN SELECT RAISE(ABORT,'Close dependency scope cannot be changed'); END;
CREATE TRIGGER p0_close_locked_draft_lines_update BEFORE UPDATE ON draft_lines
WHEN EXISTS(SELECT 1 FROM internal_period_locks WHERE book_id=NEW.book_id)
BEGIN SELECT RAISE(ABORT,'Internally locked period evidence cannot change'); END;

CREATE TRIGGER p0_close_revision_draft_lines_delete AFTER DELETE ON draft_lines
BEGIN
 INSERT INTO close_dependency_revisions(scope_kind,scope_id,revision) VALUES('book',OLD.book_id,1)
 ON CONFLICT(scope_kind,scope_id) DO UPDATE SET revision=revision+1;
END;
CREATE TRIGGER p0_close_locked_draft_lines_delete BEFORE DELETE ON draft_lines
WHEN EXISTS(SELECT 1 FROM internal_period_locks WHERE book_id=OLD.book_id)
BEGIN SELECT RAISE(ABORT,'Internally locked period evidence cannot change'); END;

CREATE TRIGGER p0_close_revision_ledger_periods_insert AFTER INSERT ON ledger_periods
BEGIN
 INSERT INTO close_dependency_revisions(scope_kind,scope_id,revision) VALUES('book',NEW.book_id,1)
 ON CONFLICT(scope_kind,scope_id) DO UPDATE SET revision=revision+1;
END;

CREATE TRIGGER p0_close_revision_ledger_periods_update AFTER UPDATE ON ledger_periods
BEGIN
 INSERT INTO close_dependency_revisions(scope_kind,scope_id,revision) VALUES('book',NEW.book_id,1)
 ON CONFLICT(scope_kind,scope_id) DO UPDATE SET revision=revision+1;
END;
CREATE TRIGGER p0_close_scope_ledger_periods BEFORE UPDATE ON ledger_periods
WHEN NEW.book_id IS NOT OLD.book_id
BEGIN SELECT RAISE(ABORT,'Close dependency scope cannot be changed'); END;

CREATE TRIGGER p0_close_revision_ledger_periods_delete AFTER DELETE ON ledger_periods
BEGIN
 INSERT INTO close_dependency_revisions(scope_kind,scope_id,revision) VALUES('book',OLD.book_id,1)
 ON CONFLICT(scope_kind,scope_id) DO UPDATE SET revision=revision+1;
END;

CREATE TRIGGER p0_close_revision_opening_bindings_insert AFTER INSERT ON opening_bindings
BEGIN
 INSERT INTO close_dependency_revisions(scope_kind,scope_id,revision) VALUES('book',NEW.book_id,1)
 ON CONFLICT(scope_kind,scope_id) DO UPDATE SET revision=revision+1;
END;
CREATE TRIGGER p0_close_locked_opening_bindings_insert BEFORE INSERT ON opening_bindings
WHEN EXISTS(SELECT 1 FROM internal_period_locks WHERE book_id=NEW.book_id)
BEGIN SELECT RAISE(ABORT,'Internally locked period evidence cannot change'); END;

CREATE TRIGGER p0_close_revision_opening_bindings_update AFTER UPDATE ON opening_bindings
BEGIN
 INSERT INTO close_dependency_revisions(scope_kind,scope_id,revision) VALUES('book',NEW.book_id,1)
 ON CONFLICT(scope_kind,scope_id) DO UPDATE SET revision=revision+1;
END;
CREATE TRIGGER p0_close_scope_opening_bindings BEFORE UPDATE ON opening_bindings
WHEN NEW.book_id IS NOT OLD.book_id
BEGIN SELECT RAISE(ABORT,'Close dependency scope cannot be changed'); END;
CREATE TRIGGER p0_close_locked_opening_bindings_update BEFORE UPDATE ON opening_bindings
WHEN EXISTS(SELECT 1 FROM internal_period_locks WHERE book_id=NEW.book_id)
BEGIN SELECT RAISE(ABORT,'Internally locked period evidence cannot change'); END;

CREATE TRIGGER p0_close_revision_opening_bindings_delete AFTER DELETE ON opening_bindings
BEGIN
 INSERT INTO close_dependency_revisions(scope_kind,scope_id,revision) VALUES('book',OLD.book_id,1)
 ON CONFLICT(scope_kind,scope_id) DO UPDATE SET revision=revision+1;
END;
CREATE TRIGGER p0_close_locked_opening_bindings_delete BEFORE DELETE ON opening_bindings
WHEN EXISTS(SELECT 1 FROM internal_period_locks WHERE book_id=OLD.book_id)
BEGIN SELECT RAISE(ABORT,'Internally locked period evidence cannot change'); END;

CREATE TRIGGER p0_close_revision_vouchers_insert AFTER INSERT ON vouchers
BEGIN
 INSERT INTO close_dependency_revisions(scope_kind,scope_id,revision) VALUES('book',NEW.book_id,1)
 ON CONFLICT(scope_kind,scope_id) DO UPDATE SET revision=revision+1;
END;
CREATE TRIGGER p0_close_locked_vouchers_insert BEFORE INSERT ON vouchers
WHEN EXISTS(SELECT 1 FROM internal_period_locks WHERE book_id=NEW.book_id)
BEGIN SELECT RAISE(ABORT,'Internally locked period evidence cannot change'); END;

CREATE TRIGGER p0_close_revision_vouchers_update AFTER UPDATE ON vouchers
BEGIN
 INSERT INTO close_dependency_revisions(scope_kind,scope_id,revision) VALUES('book',NEW.book_id,1)
 ON CONFLICT(scope_kind,scope_id) DO UPDATE SET revision=revision+1;
END;
CREATE TRIGGER p0_close_scope_vouchers BEFORE UPDATE ON vouchers
WHEN NEW.book_id IS NOT OLD.book_id
BEGIN SELECT RAISE(ABORT,'Close dependency scope cannot be changed'); END;
CREATE TRIGGER p0_close_locked_vouchers_update BEFORE UPDATE ON vouchers
WHEN EXISTS(SELECT 1 FROM internal_period_locks WHERE book_id=NEW.book_id)
BEGIN SELECT RAISE(ABORT,'Internally locked period evidence cannot change'); END;

CREATE TRIGGER p0_close_revision_vouchers_delete AFTER DELETE ON vouchers
BEGIN
 INSERT INTO close_dependency_revisions(scope_kind,scope_id,revision) VALUES('book',OLD.book_id,1)
 ON CONFLICT(scope_kind,scope_id) DO UPDATE SET revision=revision+1;
END;
CREATE TRIGGER p0_close_locked_vouchers_delete BEFORE DELETE ON vouchers
WHEN EXISTS(SELECT 1 FROM internal_period_locks WHERE book_id=OLD.book_id)
BEGIN SELECT RAISE(ABORT,'Internally locked period evidence cannot change'); END;

CREATE TRIGGER p0_close_revision_voucher_lines_insert AFTER INSERT ON voucher_lines
BEGIN
 INSERT INTO close_dependency_revisions(scope_kind,scope_id,revision) VALUES('book',(SELECT book_id FROM vouchers WHERE id=NEW.voucher_id),1)
 ON CONFLICT(scope_kind,scope_id) DO UPDATE SET revision=revision+1;
END;
CREATE TRIGGER p0_close_locked_voucher_lines_insert BEFORE INSERT ON voucher_lines
WHEN EXISTS(SELECT 1 FROM internal_period_locks WHERE book_id=(SELECT book_id FROM vouchers WHERE id=NEW.voucher_id))
BEGIN SELECT RAISE(ABORT,'Internally locked period evidence cannot change'); END;

CREATE TRIGGER p0_close_revision_voucher_lines_update AFTER UPDATE ON voucher_lines
BEGIN
 INSERT INTO close_dependency_revisions(scope_kind,scope_id,revision) VALUES('book',(SELECT book_id FROM vouchers WHERE id=NEW.voucher_id),1)
 ON CONFLICT(scope_kind,scope_id) DO UPDATE SET revision=revision+1;
END;
CREATE TRIGGER p0_close_scope_voucher_lines BEFORE UPDATE ON voucher_lines
WHEN (SELECT book_id FROM vouchers WHERE id=NEW.voucher_id) IS NOT (SELECT book_id FROM vouchers WHERE id=OLD.voucher_id)
BEGIN SELECT RAISE(ABORT,'Close dependency scope cannot be changed'); END;
CREATE TRIGGER p0_close_locked_voucher_lines_update BEFORE UPDATE ON voucher_lines
WHEN EXISTS(SELECT 1 FROM internal_period_locks WHERE book_id=(SELECT book_id FROM vouchers WHERE id=NEW.voucher_id))
BEGIN SELECT RAISE(ABORT,'Internally locked period evidence cannot change'); END;

CREATE TRIGGER p0_close_revision_voucher_lines_delete AFTER DELETE ON voucher_lines
BEGIN
 INSERT INTO close_dependency_revisions(scope_kind,scope_id,revision) VALUES('book',(SELECT book_id FROM vouchers WHERE id=OLD.voucher_id),1)
 ON CONFLICT(scope_kind,scope_id) DO UPDATE SET revision=revision+1;
END;
CREATE TRIGGER p0_close_locked_voucher_lines_delete BEFORE DELETE ON voucher_lines
WHEN EXISTS(SELECT 1 FROM internal_period_locks WHERE book_id=(SELECT book_id FROM vouchers WHERE id=OLD.voucher_id))
BEGIN SELECT RAISE(ABORT,'Internally locked period evidence cannot change'); END;

CREATE TRIGGER p0_close_revision_voucher_sources_insert AFTER INSERT ON voucher_sources
BEGIN
 INSERT INTO close_dependency_revisions(scope_kind,scope_id,revision) VALUES('book',(SELECT book_id FROM vouchers WHERE id=NEW.voucher_id),1)
 ON CONFLICT(scope_kind,scope_id) DO UPDATE SET revision=revision+1;
END;
CREATE TRIGGER p0_close_locked_voucher_sources_insert BEFORE INSERT ON voucher_sources
WHEN EXISTS(SELECT 1 FROM internal_period_locks WHERE book_id=(SELECT book_id FROM vouchers WHERE id=NEW.voucher_id))
BEGIN SELECT RAISE(ABORT,'Internally locked period evidence cannot change'); END;

CREATE TRIGGER p0_close_revision_voucher_sources_update AFTER UPDATE ON voucher_sources
BEGIN
 INSERT INTO close_dependency_revisions(scope_kind,scope_id,revision) VALUES('book',(SELECT book_id FROM vouchers WHERE id=NEW.voucher_id),1)
 ON CONFLICT(scope_kind,scope_id) DO UPDATE SET revision=revision+1;
END;
CREATE TRIGGER p0_close_scope_voucher_sources BEFORE UPDATE ON voucher_sources
WHEN (SELECT book_id FROM vouchers WHERE id=NEW.voucher_id) IS NOT (SELECT book_id FROM vouchers WHERE id=OLD.voucher_id)
BEGIN SELECT RAISE(ABORT,'Close dependency scope cannot be changed'); END;
CREATE TRIGGER p0_close_locked_voucher_sources_update BEFORE UPDATE ON voucher_sources
WHEN EXISTS(SELECT 1 FROM internal_period_locks WHERE book_id=(SELECT book_id FROM vouchers WHERE id=NEW.voucher_id))
BEGIN SELECT RAISE(ABORT,'Internally locked period evidence cannot change'); END;

CREATE TRIGGER p0_close_revision_voucher_sources_delete AFTER DELETE ON voucher_sources
BEGIN
 INSERT INTO close_dependency_revisions(scope_kind,scope_id,revision) VALUES('book',(SELECT book_id FROM vouchers WHERE id=OLD.voucher_id),1)
 ON CONFLICT(scope_kind,scope_id) DO UPDATE SET revision=revision+1;
END;
CREATE TRIGGER p0_close_locked_voucher_sources_delete BEFORE DELETE ON voucher_sources
WHEN EXISTS(SELECT 1 FROM internal_period_locks WHERE book_id=(SELECT book_id FROM vouchers WHERE id=OLD.voucher_id))
BEGIN SELECT RAISE(ABORT,'Internally locked period evidence cannot change'); END;

CREATE TRIGGER p0_close_revision_subledger_items_insert AFTER INSERT ON subledger_items
BEGIN
 INSERT INTO close_dependency_revisions(scope_kind,scope_id,revision) VALUES('book',NEW.book_id,1)
 ON CONFLICT(scope_kind,scope_id) DO UPDATE SET revision=revision+1;
END;
CREATE TRIGGER p0_close_locked_subledger_items_insert BEFORE INSERT ON subledger_items
WHEN EXISTS(SELECT 1 FROM internal_period_locks WHERE book_id=NEW.book_id)
BEGIN SELECT RAISE(ABORT,'Internally locked period evidence cannot change'); END;

CREATE TRIGGER p0_close_revision_subledger_items_update AFTER UPDATE ON subledger_items
BEGIN
 INSERT INTO close_dependency_revisions(scope_kind,scope_id,revision) VALUES('book',NEW.book_id,1)
 ON CONFLICT(scope_kind,scope_id) DO UPDATE SET revision=revision+1;
END;
CREATE TRIGGER p0_close_scope_subledger_items BEFORE UPDATE ON subledger_items
WHEN NEW.book_id IS NOT OLD.book_id
BEGIN SELECT RAISE(ABORT,'Close dependency scope cannot be changed'); END;
CREATE TRIGGER p0_close_locked_subledger_items_update BEFORE UPDATE ON subledger_items
WHEN EXISTS(SELECT 1 FROM internal_period_locks WHERE book_id=NEW.book_id)
BEGIN SELECT RAISE(ABORT,'Internally locked period evidence cannot change'); END;

CREATE TRIGGER p0_close_revision_subledger_items_delete AFTER DELETE ON subledger_items
BEGIN
 INSERT INTO close_dependency_revisions(scope_kind,scope_id,revision) VALUES('book',OLD.book_id,1)
 ON CONFLICT(scope_kind,scope_id) DO UPDATE SET revision=revision+1;
END;
CREATE TRIGGER p0_close_locked_subledger_items_delete BEFORE DELETE ON subledger_items
WHEN EXISTS(SELECT 1 FROM internal_period_locks WHERE book_id=OLD.book_id)
BEGIN SELECT RAISE(ABORT,'Internally locked period evidence cannot change'); END;

CREATE TRIGGER p0_close_revision_subledger_allocations_insert AFTER INSERT ON subledger_allocations
BEGIN
 INSERT INTO close_dependency_revisions(scope_kind,scope_id,revision) VALUES('book',NEW.book_id,1)
 ON CONFLICT(scope_kind,scope_id) DO UPDATE SET revision=revision+1;
END;
CREATE TRIGGER p0_close_locked_subledger_allocations_insert BEFORE INSERT ON subledger_allocations
WHEN EXISTS(SELECT 1 FROM internal_period_locks WHERE book_id=NEW.book_id)
BEGIN SELECT RAISE(ABORT,'Internally locked period evidence cannot change'); END;

CREATE TRIGGER p0_close_revision_subledger_allocations_update AFTER UPDATE ON subledger_allocations
BEGIN
 INSERT INTO close_dependency_revisions(scope_kind,scope_id,revision) VALUES('book',NEW.book_id,1)
 ON CONFLICT(scope_kind,scope_id) DO UPDATE SET revision=revision+1;
END;
CREATE TRIGGER p0_close_scope_subledger_allocations BEFORE UPDATE ON subledger_allocations
WHEN NEW.book_id IS NOT OLD.book_id
BEGIN SELECT RAISE(ABORT,'Close dependency scope cannot be changed'); END;
CREATE TRIGGER p0_close_locked_subledger_allocations_update BEFORE UPDATE ON subledger_allocations
WHEN EXISTS(SELECT 1 FROM internal_period_locks WHERE book_id=NEW.book_id)
BEGIN SELECT RAISE(ABORT,'Internally locked period evidence cannot change'); END;

CREATE TRIGGER p0_close_revision_subledger_allocations_delete AFTER DELETE ON subledger_allocations
BEGIN
 INSERT INTO close_dependency_revisions(scope_kind,scope_id,revision) VALUES('book',OLD.book_id,1)
 ON CONFLICT(scope_kind,scope_id) DO UPDATE SET revision=revision+1;
END;
CREATE TRIGGER p0_close_locked_subledger_allocations_delete BEFORE DELETE ON subledger_allocations
WHEN EXISTS(SELECT 1 FROM internal_period_locks WHERE book_id=OLD.book_id)
BEGIN SELECT RAISE(ABORT,'Internally locked period evidence cannot change'); END;

CREATE TRIGGER p0_close_revision_subledger_allocation_reversals_insert AFTER INSERT ON subledger_allocation_reversals
BEGIN
 INSERT INTO close_dependency_revisions(scope_kind,scope_id,revision) VALUES('book',NEW.book_id,1)
 ON CONFLICT(scope_kind,scope_id) DO UPDATE SET revision=revision+1;
END;
CREATE TRIGGER p0_close_locked_subledger_allocation_reversals_insert BEFORE INSERT ON subledger_allocation_reversals
WHEN EXISTS(SELECT 1 FROM internal_period_locks WHERE book_id=NEW.book_id)
BEGIN SELECT RAISE(ABORT,'Internally locked period evidence cannot change'); END;

CREATE TRIGGER p0_close_revision_subledger_allocation_reversals_update AFTER UPDATE ON subledger_allocation_reversals
BEGIN
 INSERT INTO close_dependency_revisions(scope_kind,scope_id,revision) VALUES('book',NEW.book_id,1)
 ON CONFLICT(scope_kind,scope_id) DO UPDATE SET revision=revision+1;
END;
CREATE TRIGGER p0_close_scope_subledger_allocation_reversals BEFORE UPDATE ON subledger_allocation_reversals
WHEN NEW.book_id IS NOT OLD.book_id
BEGIN SELECT RAISE(ABORT,'Close dependency scope cannot be changed'); END;
CREATE TRIGGER p0_close_locked_subledger_allocation_reversals_update BEFORE UPDATE ON subledger_allocation_reversals
WHEN EXISTS(SELECT 1 FROM internal_period_locks WHERE book_id=NEW.book_id)
BEGIN SELECT RAISE(ABORT,'Internally locked period evidence cannot change'); END;

CREATE TRIGGER p0_close_revision_subledger_allocation_reversals_delete AFTER DELETE ON subledger_allocation_reversals
BEGIN
 INSERT INTO close_dependency_revisions(scope_kind,scope_id,revision) VALUES('book',OLD.book_id,1)
 ON CONFLICT(scope_kind,scope_id) DO UPDATE SET revision=revision+1;
END;
CREATE TRIGGER p0_close_locked_subledger_allocation_reversals_delete BEFORE DELETE ON subledger_allocation_reversals
WHEN EXISTS(SELECT 1 FROM internal_period_locks WHERE book_id=OLD.book_id)
BEGIN SELECT RAISE(ABORT,'Internally locked period evidence cannot change'); END;

CREATE TRIGGER p0_close_revision_bank_statement_lines_insert AFTER INSERT ON bank_statement_lines
BEGIN
 INSERT INTO close_dependency_revisions(scope_kind,scope_id,revision) VALUES('book',NEW.book_id,1)
 ON CONFLICT(scope_kind,scope_id) DO UPDATE SET revision=revision+1;
END;
CREATE TRIGGER p0_close_locked_bank_statement_lines_insert BEFORE INSERT ON bank_statement_lines
WHEN EXISTS(SELECT 1 FROM internal_period_locks WHERE book_id=NEW.book_id)
BEGIN SELECT RAISE(ABORT,'Internally locked period evidence cannot change'); END;

CREATE TRIGGER p0_close_revision_bank_statement_lines_update AFTER UPDATE ON bank_statement_lines
BEGIN
 INSERT INTO close_dependency_revisions(scope_kind,scope_id,revision) VALUES('book',NEW.book_id,1)
 ON CONFLICT(scope_kind,scope_id) DO UPDATE SET revision=revision+1;
END;
CREATE TRIGGER p0_close_scope_bank_statement_lines BEFORE UPDATE ON bank_statement_lines
WHEN NEW.book_id IS NOT OLD.book_id
BEGIN SELECT RAISE(ABORT,'Close dependency scope cannot be changed'); END;
CREATE TRIGGER p0_close_locked_bank_statement_lines_update BEFORE UPDATE ON bank_statement_lines
WHEN EXISTS(SELECT 1 FROM internal_period_locks WHERE book_id=NEW.book_id)
BEGIN SELECT RAISE(ABORT,'Internally locked period evidence cannot change'); END;

CREATE TRIGGER p0_close_revision_bank_statement_lines_delete AFTER DELETE ON bank_statement_lines
BEGIN
 INSERT INTO close_dependency_revisions(scope_kind,scope_id,revision) VALUES('book',OLD.book_id,1)
 ON CONFLICT(scope_kind,scope_id) DO UPDATE SET revision=revision+1;
END;
CREATE TRIGGER p0_close_locked_bank_statement_lines_delete BEFORE DELETE ON bank_statement_lines
WHEN EXISTS(SELECT 1 FROM internal_period_locks WHERE book_id=OLD.book_id)
BEGIN SELECT RAISE(ABORT,'Internally locked period evidence cannot change'); END;

CREATE TRIGGER p0_close_revision_bank_recon_adjustments_insert AFTER INSERT ON bank_recon_adjustments
BEGIN
 INSERT INTO close_dependency_revisions(scope_kind,scope_id,revision) VALUES('book',NEW.book_id,1)
 ON CONFLICT(scope_kind,scope_id) DO UPDATE SET revision=revision+1;
END;
CREATE TRIGGER p0_close_locked_bank_recon_adjustments_insert BEFORE INSERT ON bank_recon_adjustments
WHEN EXISTS(SELECT 1 FROM internal_period_locks WHERE book_id=NEW.book_id)
BEGIN SELECT RAISE(ABORT,'Internally locked period evidence cannot change'); END;

CREATE TRIGGER p0_close_revision_bank_recon_adjustments_update AFTER UPDATE ON bank_recon_adjustments
BEGIN
 INSERT INTO close_dependency_revisions(scope_kind,scope_id,revision) VALUES('book',NEW.book_id,1)
 ON CONFLICT(scope_kind,scope_id) DO UPDATE SET revision=revision+1;
END;
CREATE TRIGGER p0_close_scope_bank_recon_adjustments BEFORE UPDATE ON bank_recon_adjustments
WHEN NEW.book_id IS NOT OLD.book_id
BEGIN SELECT RAISE(ABORT,'Close dependency scope cannot be changed'); END;
CREATE TRIGGER p0_close_locked_bank_recon_adjustments_update BEFORE UPDATE ON bank_recon_adjustments
WHEN EXISTS(SELECT 1 FROM internal_period_locks WHERE book_id=NEW.book_id)
BEGIN SELECT RAISE(ABORT,'Internally locked period evidence cannot change'); END;

CREATE TRIGGER p0_close_revision_bank_recon_adjustments_delete AFTER DELETE ON bank_recon_adjustments
BEGIN
 INSERT INTO close_dependency_revisions(scope_kind,scope_id,revision) VALUES('book',OLD.book_id,1)
 ON CONFLICT(scope_kind,scope_id) DO UPDATE SET revision=revision+1;
END;
CREATE TRIGGER p0_close_locked_bank_recon_adjustments_delete BEFORE DELETE ON bank_recon_adjustments
WHEN EXISTS(SELECT 1 FROM internal_period_locks WHERE book_id=OLD.book_id)
BEGIN SELECT RAISE(ABORT,'Internally locked period evidence cannot change'); END;

CREATE TRIGGER p0_close_revision_periodic_rules_insert AFTER INSERT ON periodic_rules
BEGIN
 INSERT INTO close_dependency_revisions(scope_kind,scope_id,revision) VALUES('book',NEW.book_id,1)
 ON CONFLICT(scope_kind,scope_id) DO UPDATE SET revision=revision+1;
END;
CREATE TRIGGER p0_close_locked_periodic_rules_insert BEFORE INSERT ON periodic_rules
WHEN EXISTS(SELECT 1 FROM internal_period_locks WHERE book_id=NEW.book_id)
BEGIN SELECT RAISE(ABORT,'Internally locked period evidence cannot change'); END;

CREATE TRIGGER p0_close_revision_periodic_rules_update AFTER UPDATE ON periodic_rules
BEGIN
 INSERT INTO close_dependency_revisions(scope_kind,scope_id,revision) VALUES('book',NEW.book_id,1)
 ON CONFLICT(scope_kind,scope_id) DO UPDATE SET revision=revision+1;
END;
CREATE TRIGGER p0_close_scope_periodic_rules BEFORE UPDATE ON periodic_rules
WHEN NEW.book_id IS NOT OLD.book_id
BEGIN SELECT RAISE(ABORT,'Close dependency scope cannot be changed'); END;
CREATE TRIGGER p0_close_locked_periodic_rules_update BEFORE UPDATE ON periodic_rules
WHEN EXISTS(SELECT 1 FROM internal_period_locks WHERE book_id=NEW.book_id)
BEGIN SELECT RAISE(ABORT,'Internally locked period evidence cannot change'); END;

CREATE TRIGGER p0_close_revision_periodic_rules_delete AFTER DELETE ON periodic_rules
BEGIN
 INSERT INTO close_dependency_revisions(scope_kind,scope_id,revision) VALUES('book',OLD.book_id,1)
 ON CONFLICT(scope_kind,scope_id) DO UPDATE SET revision=revision+1;
END;
CREATE TRIGGER p0_close_locked_periodic_rules_delete BEFORE DELETE ON periodic_rules
WHEN EXISTS(SELECT 1 FROM internal_period_locks WHERE book_id=OLD.book_id)
BEGIN SELECT RAISE(ABORT,'Internally locked period evidence cannot change'); END;

CREATE TRIGGER p0_close_revision_periodic_assets_insert AFTER INSERT ON periodic_assets
BEGIN
 INSERT INTO close_dependency_revisions(scope_kind,scope_id,revision) VALUES('book',NEW.book_id,1)
 ON CONFLICT(scope_kind,scope_id) DO UPDATE SET revision=revision+1;
END;
CREATE TRIGGER p0_close_locked_periodic_assets_insert BEFORE INSERT ON periodic_assets
WHEN EXISTS(SELECT 1 FROM internal_period_locks WHERE book_id=NEW.book_id)
BEGIN SELECT RAISE(ABORT,'Internally locked period evidence cannot change'); END;

CREATE TRIGGER p0_close_revision_periodic_assets_update AFTER UPDATE ON periodic_assets
BEGIN
 INSERT INTO close_dependency_revisions(scope_kind,scope_id,revision) VALUES('book',NEW.book_id,1)
 ON CONFLICT(scope_kind,scope_id) DO UPDATE SET revision=revision+1;
END;
CREATE TRIGGER p0_close_scope_periodic_assets BEFORE UPDATE ON periodic_assets
WHEN NEW.book_id IS NOT OLD.book_id
BEGIN SELECT RAISE(ABORT,'Close dependency scope cannot be changed'); END;
CREATE TRIGGER p0_close_locked_periodic_assets_update BEFORE UPDATE ON periodic_assets
WHEN EXISTS(SELECT 1 FROM internal_period_locks WHERE book_id=NEW.book_id)
BEGIN SELECT RAISE(ABORT,'Internally locked period evidence cannot change'); END;

CREATE TRIGGER p0_close_revision_periodic_assets_delete AFTER DELETE ON periodic_assets
BEGIN
 INSERT INTO close_dependency_revisions(scope_kind,scope_id,revision) VALUES('book',OLD.book_id,1)
 ON CONFLICT(scope_kind,scope_id) DO UPDATE SET revision=revision+1;
END;
CREATE TRIGGER p0_close_locked_periodic_assets_delete BEFORE DELETE ON periodic_assets
WHEN EXISTS(SELECT 1 FROM internal_period_locks WHERE book_id=OLD.book_id)
BEGIN SELECT RAISE(ABORT,'Internally locked period evidence cannot change'); END;

CREATE TRIGGER p0_close_revision_periodic_runs_insert AFTER INSERT ON periodic_runs
BEGIN
 INSERT INTO close_dependency_revisions(scope_kind,scope_id,revision) VALUES('book',NEW.book_id,1)
 ON CONFLICT(scope_kind,scope_id) DO UPDATE SET revision=revision+1;
END;
CREATE TRIGGER p0_close_locked_periodic_runs_insert BEFORE INSERT ON periodic_runs
WHEN EXISTS(SELECT 1 FROM internal_period_locks WHERE book_id=NEW.book_id)
BEGIN SELECT RAISE(ABORT,'Internally locked period evidence cannot change'); END;

CREATE TRIGGER p0_close_revision_periodic_runs_update AFTER UPDATE ON periodic_runs
BEGIN
 INSERT INTO close_dependency_revisions(scope_kind,scope_id,revision) VALUES('book',NEW.book_id,1)
 ON CONFLICT(scope_kind,scope_id) DO UPDATE SET revision=revision+1;
END;
CREATE TRIGGER p0_close_scope_periodic_runs BEFORE UPDATE ON periodic_runs
WHEN NEW.book_id IS NOT OLD.book_id
BEGIN SELECT RAISE(ABORT,'Close dependency scope cannot be changed'); END;
CREATE TRIGGER p0_close_locked_periodic_runs_update BEFORE UPDATE ON periodic_runs
WHEN EXISTS(SELECT 1 FROM internal_period_locks WHERE book_id=NEW.book_id)
BEGIN SELECT RAISE(ABORT,'Internally locked period evidence cannot change'); END;

CREATE TRIGGER p0_close_revision_periodic_runs_delete AFTER DELETE ON periodic_runs
BEGIN
 INSERT INTO close_dependency_revisions(scope_kind,scope_id,revision) VALUES('book',OLD.book_id,1)
 ON CONFLICT(scope_kind,scope_id) DO UPDATE SET revision=revision+1;
END;
CREATE TRIGGER p0_close_locked_periodic_runs_delete BEFORE DELETE ON periodic_runs
WHEN EXISTS(SELECT 1 FROM internal_period_locks WHERE book_id=OLD.book_id)
BEGIN SELECT RAISE(ABORT,'Internally locked period evidence cannot change'); END;

CREATE TRIGGER p0_close_revision_periodic_drafts_insert AFTER INSERT ON periodic_drafts
BEGIN
 INSERT INTO close_dependency_revisions(scope_kind,scope_id,revision) VALUES('book',NEW.book_id,1)
 ON CONFLICT(scope_kind,scope_id) DO UPDATE SET revision=revision+1;
END;
CREATE TRIGGER p0_close_locked_periodic_drafts_insert BEFORE INSERT ON periodic_drafts
WHEN EXISTS(SELECT 1 FROM internal_period_locks WHERE book_id=NEW.book_id)
BEGIN SELECT RAISE(ABORT,'Internally locked period evidence cannot change'); END;

CREATE TRIGGER p0_close_revision_periodic_drafts_update AFTER UPDATE ON periodic_drafts
BEGIN
 INSERT INTO close_dependency_revisions(scope_kind,scope_id,revision) VALUES('book',NEW.book_id,1)
 ON CONFLICT(scope_kind,scope_id) DO UPDATE SET revision=revision+1;
END;
CREATE TRIGGER p0_close_scope_periodic_drafts BEFORE UPDATE ON periodic_drafts
WHEN NEW.book_id IS NOT OLD.book_id
BEGIN SELECT RAISE(ABORT,'Close dependency scope cannot be changed'); END;
CREATE TRIGGER p0_close_locked_periodic_drafts_update BEFORE UPDATE ON periodic_drafts
WHEN EXISTS(SELECT 1 FROM internal_period_locks WHERE book_id=NEW.book_id)
BEGIN SELECT RAISE(ABORT,'Internally locked period evidence cannot change'); END;

CREATE TRIGGER p0_close_revision_periodic_drafts_delete AFTER DELETE ON periodic_drafts
BEGIN
 INSERT INTO close_dependency_revisions(scope_kind,scope_id,revision) VALUES('book',OLD.book_id,1)
 ON CONFLICT(scope_kind,scope_id) DO UPDATE SET revision=revision+1;
END;
CREATE TRIGGER p0_close_locked_periodic_drafts_delete BEFORE DELETE ON periodic_drafts
WHEN EXISTS(SELECT 1 FROM internal_period_locks WHERE book_id=OLD.book_id)
BEGIN SELECT RAISE(ABORT,'Internally locked period evidence cannot change'); END;

CREATE TRIGGER p0_close_revision_periodic_draft_lines_insert AFTER INSERT ON periodic_draft_lines
BEGIN
 INSERT INTO close_dependency_revisions(scope_kind,scope_id,revision) VALUES('book',(SELECT book_id FROM periodic_drafts WHERE id=NEW.draft_id),1)
 ON CONFLICT(scope_kind,scope_id) DO UPDATE SET revision=revision+1;
END;
CREATE TRIGGER p0_close_locked_periodic_draft_lines_insert BEFORE INSERT ON periodic_draft_lines
WHEN EXISTS(SELECT 1 FROM internal_period_locks WHERE book_id=(SELECT book_id FROM periodic_drafts WHERE id=NEW.draft_id))
BEGIN SELECT RAISE(ABORT,'Internally locked period evidence cannot change'); END;

CREATE TRIGGER p0_close_revision_periodic_draft_lines_update AFTER UPDATE ON periodic_draft_lines
BEGIN
 INSERT INTO close_dependency_revisions(scope_kind,scope_id,revision) VALUES('book',(SELECT book_id FROM periodic_drafts WHERE id=NEW.draft_id),1)
 ON CONFLICT(scope_kind,scope_id) DO UPDATE SET revision=revision+1;
END;
CREATE TRIGGER p0_close_scope_periodic_draft_lines BEFORE UPDATE ON periodic_draft_lines
WHEN (SELECT book_id FROM periodic_drafts WHERE id=NEW.draft_id) IS NOT (SELECT book_id FROM periodic_drafts WHERE id=OLD.draft_id)
BEGIN SELECT RAISE(ABORT,'Close dependency scope cannot be changed'); END;
CREATE TRIGGER p0_close_locked_periodic_draft_lines_update BEFORE UPDATE ON periodic_draft_lines
WHEN EXISTS(SELECT 1 FROM internal_period_locks WHERE book_id=(SELECT book_id FROM periodic_drafts WHERE id=NEW.draft_id))
BEGIN SELECT RAISE(ABORT,'Internally locked period evidence cannot change'); END;

CREATE TRIGGER p0_close_revision_periodic_draft_lines_delete AFTER DELETE ON periodic_draft_lines
BEGIN
 INSERT INTO close_dependency_revisions(scope_kind,scope_id,revision) VALUES('book',(SELECT book_id FROM periodic_drafts WHERE id=OLD.draft_id),1)
 ON CONFLICT(scope_kind,scope_id) DO UPDATE SET revision=revision+1;
END;
CREATE TRIGGER p0_close_locked_periodic_draft_lines_delete BEFORE DELETE ON periodic_draft_lines
WHEN EXISTS(SELECT 1 FROM internal_period_locks WHERE book_id=(SELECT book_id FROM periodic_drafts WHERE id=OLD.draft_id))
BEGIN SELECT RAISE(ABORT,'Internally locked period evidence cannot change'); END;

CREATE TRIGGER p0_close_revision_report_runs_insert AFTER INSERT ON report_runs
BEGIN
 INSERT INTO close_dependency_revisions(scope_kind,scope_id,revision) VALUES('book',NEW.book_id,1)
 ON CONFLICT(scope_kind,scope_id) DO UPDATE SET revision=revision+1;
END;
CREATE TRIGGER p0_close_locked_report_runs_insert BEFORE INSERT ON report_runs
WHEN EXISTS(SELECT 1 FROM internal_period_locks WHERE book_id=NEW.book_id)
BEGIN SELECT RAISE(ABORT,'Internally locked period evidence cannot change'); END;

CREATE TRIGGER p0_close_revision_report_runs_update AFTER UPDATE ON report_runs
BEGIN
 INSERT INTO close_dependency_revisions(scope_kind,scope_id,revision) VALUES('book',NEW.book_id,1)
 ON CONFLICT(scope_kind,scope_id) DO UPDATE SET revision=revision+1;
END;
CREATE TRIGGER p0_close_scope_report_runs BEFORE UPDATE ON report_runs
WHEN NEW.book_id IS NOT OLD.book_id
BEGIN SELECT RAISE(ABORT,'Close dependency scope cannot be changed'); END;
CREATE TRIGGER p0_close_locked_report_runs_update BEFORE UPDATE ON report_runs
WHEN EXISTS(SELECT 1 FROM internal_period_locks WHERE book_id=NEW.book_id) AND NOT(OLD.status='reviewed' AND NEW.status='published' AND NEW.book_id=OLD.book_id AND NEW.version=OLD.version AND NEW.inputs_fingerprint=OLD.inputs_fingerprint AND NEW.content_json=OLD.content_json AND NEW.content_hash=OLD.content_hash AND NEW.reviewed_by IS OLD.reviewed_by AND NEW.reviewed_at IS OLD.reviewed_at AND NEW.template_id=OLD.template_id AND NEW.template_version=OLD.template_version AND NEW.id=OLD.id AND NEW.period=OLD.period AND NEW.created_by=OLD.created_by AND NEW.created_at=OLD.created_at AND NEW.supersedes_id IS OLD.supersedes_id)
BEGIN SELECT RAISE(ABORT,'Internally locked period evidence cannot change'); END;

CREATE TRIGGER p0_close_revision_report_runs_delete AFTER DELETE ON report_runs
BEGIN
 INSERT INTO close_dependency_revisions(scope_kind,scope_id,revision) VALUES('book',OLD.book_id,1)
 ON CONFLICT(scope_kind,scope_id) DO UPDATE SET revision=revision+1;
END;
CREATE TRIGGER p0_close_locked_report_runs_delete BEFORE DELETE ON report_runs
WHEN EXISTS(SELECT 1 FROM internal_period_locks WHERE book_id=OLD.book_id)
BEGIN SELECT RAISE(ABORT,'Internally locked period evidence cannot change'); END;

CREATE TRIGGER p0_close_revision_report_notes_insert AFTER INSERT ON report_notes
BEGIN
 INSERT INTO close_dependency_revisions(scope_kind,scope_id,revision) VALUES('book',(SELECT book_id FROM report_runs WHERE id=NEW.report_id),1)
 ON CONFLICT(scope_kind,scope_id) DO UPDATE SET revision=revision+1;
END;
CREATE TRIGGER p0_close_locked_report_notes_insert BEFORE INSERT ON report_notes
WHEN EXISTS(SELECT 1 FROM internal_period_locks WHERE book_id=(SELECT book_id FROM report_runs WHERE id=NEW.report_id))
BEGIN SELECT RAISE(ABORT,'Internally locked period evidence cannot change'); END;

CREATE TRIGGER p0_close_revision_report_notes_update AFTER UPDATE ON report_notes
BEGIN
 INSERT INTO close_dependency_revisions(scope_kind,scope_id,revision) VALUES('book',(SELECT book_id FROM report_runs WHERE id=NEW.report_id),1)
 ON CONFLICT(scope_kind,scope_id) DO UPDATE SET revision=revision+1;
END;
CREATE TRIGGER p0_close_scope_report_notes BEFORE UPDATE ON report_notes
WHEN (SELECT book_id FROM report_runs WHERE id=NEW.report_id) IS NOT (SELECT book_id FROM report_runs WHERE id=OLD.report_id)
BEGIN SELECT RAISE(ABORT,'Close dependency scope cannot be changed'); END;
CREATE TRIGGER p0_close_locked_report_notes_update BEFORE UPDATE ON report_notes
WHEN EXISTS(SELECT 1 FROM internal_period_locks WHERE book_id=(SELECT book_id FROM report_runs WHERE id=NEW.report_id))
BEGIN SELECT RAISE(ABORT,'Internally locked period evidence cannot change'); END;

CREATE TRIGGER p0_close_revision_report_notes_delete AFTER DELETE ON report_notes
BEGIN
 INSERT INTO close_dependency_revisions(scope_kind,scope_id,revision) VALUES('book',(SELECT book_id FROM report_runs WHERE id=OLD.report_id),1)
 ON CONFLICT(scope_kind,scope_id) DO UPDATE SET revision=revision+1;
END;
CREATE TRIGGER p0_close_locked_report_notes_delete BEFORE DELETE ON report_notes
WHEN EXISTS(SELECT 1 FROM internal_period_locks WHERE book_id=(SELECT book_id FROM report_runs WHERE id=OLD.report_id))
BEGIN SELECT RAISE(ABORT,'Internally locked period evidence cannot change'); END;

CREATE TRIGGER p0_close_revision_accounting_policies_insert AFTER INSERT ON accounting_policies
BEGIN
 INSERT INTO close_dependency_revisions(scope_kind,scope_id,revision) VALUES('ledger',NEW.ledger_id,1)
 ON CONFLICT(scope_kind,scope_id) DO UPDATE SET revision=revision+1;
END;

CREATE TRIGGER p0_close_revision_accounting_policies_update AFTER UPDATE ON accounting_policies
BEGIN
 INSERT INTO close_dependency_revisions(scope_kind,scope_id,revision) VALUES('ledger',NEW.ledger_id,1)
 ON CONFLICT(scope_kind,scope_id) DO UPDATE SET revision=revision+1;
END;
CREATE TRIGGER p0_close_scope_accounting_policies BEFORE UPDATE ON accounting_policies
WHEN NEW.ledger_id IS NOT OLD.ledger_id
BEGIN SELECT RAISE(ABORT,'Close dependency scope cannot be changed'); END;

CREATE TRIGGER p0_close_revision_accounting_policies_delete AFTER DELETE ON accounting_policies
BEGIN
 INSERT INTO close_dependency_revisions(scope_kind,scope_id,revision) VALUES('ledger',OLD.ledger_id,1)
 ON CONFLICT(scope_kind,scope_id) DO UPDATE SET revision=revision+1;
END;

CREATE TRIGGER p0_close_revision_ledger_accounts_insert AFTER INSERT ON ledger_accounts
BEGIN
 INSERT INTO close_dependency_revisions(scope_kind,scope_id,revision) VALUES('ledger',NEW.ledger_id,1)
 ON CONFLICT(scope_kind,scope_id) DO UPDATE SET revision=revision+1;
END;

CREATE TRIGGER p0_close_revision_ledger_accounts_update AFTER UPDATE ON ledger_accounts
BEGIN
 INSERT INTO close_dependency_revisions(scope_kind,scope_id,revision) VALUES('ledger',NEW.ledger_id,1)
 ON CONFLICT(scope_kind,scope_id) DO UPDATE SET revision=revision+1;
END;
CREATE TRIGGER p0_close_scope_ledger_accounts BEFORE UPDATE ON ledger_accounts
WHEN NEW.ledger_id IS NOT OLD.ledger_id
BEGIN SELECT RAISE(ABORT,'Close dependency scope cannot be changed'); END;

CREATE TRIGGER p0_close_revision_ledger_accounts_delete AFTER DELETE ON ledger_accounts
BEGIN
 INSERT INTO close_dependency_revisions(scope_kind,scope_id,revision) VALUES('ledger',OLD.ledger_id,1)
 ON CONFLICT(scope_kind,scope_id) DO UPDATE SET revision=revision+1;
END;

CREATE TRIGGER p0_close_revision_ledger_account_versions_insert AFTER INSERT ON ledger_account_versions
BEGIN
 INSERT INTO close_dependency_revisions(scope_kind,scope_id,revision) VALUES('ledger',NEW.ledger_id,1)
 ON CONFLICT(scope_kind,scope_id) DO UPDATE SET revision=revision+1;
END;

CREATE TRIGGER p0_close_revision_ledger_account_versions_update AFTER UPDATE ON ledger_account_versions
BEGIN
 INSERT INTO close_dependency_revisions(scope_kind,scope_id,revision) VALUES('ledger',NEW.ledger_id,1)
 ON CONFLICT(scope_kind,scope_id) DO UPDATE SET revision=revision+1;
END;
CREATE TRIGGER p0_close_scope_ledger_account_versions BEFORE UPDATE ON ledger_account_versions
WHEN NEW.ledger_id IS NOT OLD.ledger_id
BEGIN SELECT RAISE(ABORT,'Close dependency scope cannot be changed'); END;

CREATE TRIGGER p0_close_revision_ledger_account_versions_delete AFTER DELETE ON ledger_account_versions
BEGIN
 INSERT INTO close_dependency_revisions(scope_kind,scope_id,revision) VALUES('ledger',OLD.ledger_id,1)
 ON CONFLICT(scope_kind,scope_id) DO UPDATE SET revision=revision+1;
END;

CREATE TRIGGER p0_close_revision_ledger_account_mappings_insert AFTER INSERT ON ledger_account_mappings
BEGIN
 INSERT INTO close_dependency_revisions(scope_kind,scope_id,revision) VALUES('ledger',NEW.ledger_id,1)
 ON CONFLICT(scope_kind,scope_id) DO UPDATE SET revision=revision+1;
END;

CREATE TRIGGER p0_close_revision_ledger_account_mappings_update AFTER UPDATE ON ledger_account_mappings
BEGIN
 INSERT INTO close_dependency_revisions(scope_kind,scope_id,revision) VALUES('ledger',NEW.ledger_id,1)
 ON CONFLICT(scope_kind,scope_id) DO UPDATE SET revision=revision+1;
END;
CREATE TRIGGER p0_close_scope_ledger_account_mappings BEFORE UPDATE ON ledger_account_mappings
WHEN NEW.ledger_id IS NOT OLD.ledger_id
BEGIN SELECT RAISE(ABORT,'Close dependency scope cannot be changed'); END;

CREATE TRIGGER p0_close_revision_ledger_account_mappings_delete AFTER DELETE ON ledger_account_mappings
BEGIN
 INSERT INTO close_dependency_revisions(scope_kind,scope_id,revision) VALUES('ledger',OLD.ledger_id,1)
 ON CONFLICT(scope_kind,scope_id) DO UPDATE SET revision=revision+1;
END;

CREATE TRIGGER p0_close_revision_opening_packages_insert AFTER INSERT ON opening_packages
BEGIN
 INSERT INTO close_dependency_revisions(scope_kind,scope_id,revision) VALUES('ledger',NEW.ledger_id,1)
 ON CONFLICT(scope_kind,scope_id) DO UPDATE SET revision=revision+1;
END;

CREATE TRIGGER p0_close_revision_opening_packages_update AFTER UPDATE ON opening_packages
BEGIN
 INSERT INTO close_dependency_revisions(scope_kind,scope_id,revision) VALUES('ledger',NEW.ledger_id,1)
 ON CONFLICT(scope_kind,scope_id) DO UPDATE SET revision=revision+1;
END;
CREATE TRIGGER p0_close_scope_opening_packages BEFORE UPDATE ON opening_packages
WHEN NEW.ledger_id IS NOT OLD.ledger_id
BEGIN SELECT RAISE(ABORT,'Close dependency scope cannot be changed'); END;

CREATE TRIGGER p0_close_revision_opening_packages_delete AFTER DELETE ON opening_packages
BEGIN
 INSERT INTO close_dependency_revisions(scope_kind,scope_id,revision) VALUES('ledger',OLD.ledger_id,1)
 ON CONFLICT(scope_kind,scope_id) DO UPDATE SET revision=revision+1;
END;

CREATE TRIGGER p0_close_revision_opening_package_versions_insert AFTER INSERT ON opening_package_versions
BEGIN
 INSERT INTO close_dependency_revisions(scope_kind,scope_id,revision) VALUES('ledger',(SELECT ledger_id FROM opening_packages WHERE id=NEW.package_id),1)
 ON CONFLICT(scope_kind,scope_id) DO UPDATE SET revision=revision+1;
END;

CREATE TRIGGER p0_close_revision_opening_package_versions_update AFTER UPDATE ON opening_package_versions
BEGIN
 INSERT INTO close_dependency_revisions(scope_kind,scope_id,revision) VALUES('ledger',(SELECT ledger_id FROM opening_packages WHERE id=NEW.package_id),1)
 ON CONFLICT(scope_kind,scope_id) DO UPDATE SET revision=revision+1;
END;
CREATE TRIGGER p0_close_scope_opening_package_versions BEFORE UPDATE ON opening_package_versions
WHEN (SELECT ledger_id FROM opening_packages WHERE id=NEW.package_id) IS NOT (SELECT ledger_id FROM opening_packages WHERE id=OLD.package_id)
BEGIN SELECT RAISE(ABORT,'Close dependency scope cannot be changed'); END;

CREATE TRIGGER p0_close_revision_opening_package_versions_delete AFTER DELETE ON opening_package_versions
BEGIN
 INSERT INTO close_dependency_revisions(scope_kind,scope_id,revision) VALUES('ledger',(SELECT ledger_id FROM opening_packages WHERE id=OLD.package_id),1)
 ON CONFLICT(scope_kind,scope_id) DO UPDATE SET revision=revision+1;
END;

CREATE TRIGGER p0_close_revision_opening_lines_insert AFTER INSERT ON opening_lines
BEGIN
 INSERT INTO close_dependency_revisions(scope_kind,scope_id,revision) VALUES('ledger',(SELECT p.ledger_id FROM opening_packages p JOIN opening_package_versions v ON v.package_id=p.id WHERE v.id=NEW.package_version_id),1)
 ON CONFLICT(scope_kind,scope_id) DO UPDATE SET revision=revision+1;
END;

CREATE TRIGGER p0_close_revision_opening_lines_update AFTER UPDATE ON opening_lines
BEGIN
 INSERT INTO close_dependency_revisions(scope_kind,scope_id,revision) VALUES('ledger',(SELECT p.ledger_id FROM opening_packages p JOIN opening_package_versions v ON v.package_id=p.id WHERE v.id=NEW.package_version_id),1)
 ON CONFLICT(scope_kind,scope_id) DO UPDATE SET revision=revision+1;
END;
CREATE TRIGGER p0_close_scope_opening_lines BEFORE UPDATE ON opening_lines
WHEN (SELECT p.ledger_id FROM opening_packages p JOIN opening_package_versions v ON v.package_id=p.id WHERE v.id=NEW.package_version_id) IS NOT (SELECT p.ledger_id FROM opening_packages p JOIN opening_package_versions v ON v.package_id=p.id WHERE v.id=OLD.package_version_id)
BEGIN SELECT RAISE(ABORT,'Close dependency scope cannot be changed'); END;

CREATE TRIGGER p0_close_revision_opening_lines_delete AFTER DELETE ON opening_lines
BEGIN
 INSERT INTO close_dependency_revisions(scope_kind,scope_id,revision) VALUES('ledger',(SELECT p.ledger_id FROM opening_packages p JOIN opening_package_versions v ON v.package_id=p.id WHERE v.id=OLD.package_version_id),1)
 ON CONFLICT(scope_kind,scope_id) DO UPDATE SET revision=revision+1;
END;

CREATE TRIGGER p0_close_revision_opening_open_items_insert AFTER INSERT ON opening_open_items
BEGIN
 INSERT INTO close_dependency_revisions(scope_kind,scope_id,revision) VALUES('ledger',(SELECT p.ledger_id FROM opening_packages p JOIN opening_package_versions v ON v.package_id=p.id WHERE v.id=NEW.package_version_id),1)
 ON CONFLICT(scope_kind,scope_id) DO UPDATE SET revision=revision+1;
END;

CREATE TRIGGER p0_close_revision_opening_open_items_update AFTER UPDATE ON opening_open_items
BEGIN
 INSERT INTO close_dependency_revisions(scope_kind,scope_id,revision) VALUES('ledger',(SELECT p.ledger_id FROM opening_packages p JOIN opening_package_versions v ON v.package_id=p.id WHERE v.id=NEW.package_version_id),1)
 ON CONFLICT(scope_kind,scope_id) DO UPDATE SET revision=revision+1;
END;
CREATE TRIGGER p0_close_scope_opening_open_items BEFORE UPDATE ON opening_open_items
WHEN (SELECT p.ledger_id FROM opening_packages p JOIN opening_package_versions v ON v.package_id=p.id WHERE v.id=NEW.package_version_id) IS NOT (SELECT p.ledger_id FROM opening_packages p JOIN opening_package_versions v ON v.package_id=p.id WHERE v.id=OLD.package_version_id)
BEGIN SELECT RAISE(ABORT,'Close dependency scope cannot be changed'); END;

CREATE TRIGGER p0_close_revision_opening_open_items_delete AFTER DELETE ON opening_open_items
BEGIN
 INSERT INTO close_dependency_revisions(scope_kind,scope_id,revision) VALUES('ledger',(SELECT p.ledger_id FROM opening_packages p JOIN opening_package_versions v ON v.package_id=p.id WHERE v.id=OLD.package_version_id),1)
 ON CONFLICT(scope_kind,scope_id) DO UPDATE SET revision=revision+1;
END;

CREATE TRIGGER p0_close_revision_report_templates_insert AFTER INSERT ON report_templates
BEGIN
 INSERT INTO close_dependency_revisions(scope_kind,scope_id,revision) VALUES('template',NEW.id,1)
 ON CONFLICT(scope_kind,scope_id) DO UPDATE SET revision=revision+1;
END;

CREATE TRIGGER p0_close_revision_report_templates_update AFTER UPDATE ON report_templates
BEGIN
 INSERT INTO close_dependency_revisions(scope_kind,scope_id,revision) VALUES('template',NEW.id,1)
 ON CONFLICT(scope_kind,scope_id) DO UPDATE SET revision=revision+1;
END;
CREATE TRIGGER p0_close_scope_report_templates BEFORE UPDATE ON report_templates
WHEN NEW.id IS NOT OLD.id
BEGIN SELECT RAISE(ABORT,'Close dependency scope cannot be changed'); END;

CREATE TRIGGER p0_close_revision_report_templates_delete AFTER DELETE ON report_templates
BEGIN
 INSERT INTO close_dependency_revisions(scope_kind,scope_id,revision) VALUES('template',OLD.id,1)
 ON CONFLICT(scope_kind,scope_id) DO UPDATE SET revision=revision+1;
END;

CREATE VIEW close_reviewable_books AS
 SELECT b.id FROM books b
 JOIN users u ON u.id=accounting_actor_user() AND u.active=1
 JOIN companies c ON c.id=b.company_id AND c.status='active'
 JOIN firm_clients fc ON fc.firm_id=b.firm_id AND fc.company_id=b.company_id AND fc.status='active'
 WHERE b.firm_id=accounting_actor_firm() AND (
  EXISTS(SELECT 1 FROM memberships m WHERE m.firm_id=b.firm_id AND m.user_id=u.id AND m.role IN('owner','admin'))
  OR EXISTS(SELECT 1 FROM book_access ba WHERE ba.book_id=b.id AND ba.firm_id=b.firm_id
   AND ba.user_id=u.id AND ba.role='reviewer' AND (
    EXISTS(SELECT 1 FROM memberships m WHERE m.firm_id=b.firm_id AND m.user_id=u.id AND m.role IN('owner','admin','reviewer'))
    OR (NOT EXISTS(SELECT 1 FROM memberships m WHERE m.firm_id=b.firm_id AND m.user_id=u.id)
     AND EXISTS(SELECT 1 FROM company_memberships cm WHERE cm.company_id=b.company_id AND cm.user_id=u.id AND cm.role='finance')))));

CREATE TRIGGER p0_close_insert_guard BEFORE INSERT ON internal_close_runs
WHEN NEW.book_id NOT IN(SELECT id FROM close_reviewable_books)
 OR NEW.status NOT IN('preflight_passed','preflight_failed')
 OR NEW.created_by<>accounting_actor_user() OR NEW.reviewed_by IS NOT NULL OR NEW.reviewed_at IS NOT NULL
 OR NEW.locked_by IS NOT NULL OR NEW.locked_at IS NOT NULL OR NEW.published_by IS NOT NULL OR NEW.published_at IS NOT NULL
 OR NEW.version<1 OR length(NEW.inputs_fingerprint)<>64
 OR NOT coalesce((json_valid(NEW.dependencies_json) AND json_extract(NEW.dependencies_json,'$.version')=1
 AND json_extract(NEW.dependencies_json,'$.book.id')=NEW.book_id
 AND json_extract(NEW.dependencies_json,'$.ledger.id')=(SELECT ledger_id FROM ledger_periods WHERE book_id=NEW.book_id)
 AND NOT EXISTS(SELECT 1 FROM json_each(NEW.dependencies_json) j
  WHERE j.key IN('book','ledger','template') AND json_extract(j.value,'$.revision') IS NOT
   coalesce((SELECT revision FROM close_dependency_revisions WHERE scope_kind=j.key AND scope_id=json_extract(j.value,'$.id')),0))
 AND EXISTS(SELECT 1 FROM report_runs rr JOIN books bb ON bb.id=rr.book_id
  JOIN periods pp ON pp.book_id=bb.id AND pp.period=bb.period
  JOIN report_templates tt ON tt.id=rr.template_id AND tt.version=rr.template_version
  WHERE rr.id=NEW.report_run_id AND rr.book_id=NEW.book_id AND rr.period=NEW.period
   AND bb.period=NEW.period AND pp.status='open' AND rr.status IN('reviewed','published')
   AND tt.active=1 AND json_extract(NEW.dependencies_json,'$.template.id')=rr.template_id)),0)
 OR EXISTS(SELECT 1 FROM internal_period_locks WHERE book_id=NEW.book_id AND period=NEW.period)
BEGIN SELECT RAISE(ABORT,'Current actor must create an unreviewed current preflight'); END;

CREATE TRIGGER p0_close_insert_checks_guard BEFORE INSERT ON internal_close_runs
WHEN NOT json_valid(NEW.checks_json) OR json_type(NEW.checks_json)<>'array' OR json_array_length(NEW.checks_json)<>6
 OR (SELECT count(DISTINCT json_extract(c.value,'$.code')) FROM json_each(NEW.checks_json) c
  WHERE json_extract(c.value,'$.code') IN('unposted_periodic','unapproved_drafts','subledger_control',
   'bank_reconciliation','trial_balance','report_checks'))<>6
 OR EXISTS(SELECT 1 FROM json_each(NEW.checks_json) c WHERE coalesce(json_extract(c.value,'$.status'),'') NOT IN('pass','blocked'))
 OR (NEW.status='preflight_passed' AND EXISTS(SELECT 1 FROM json_each(NEW.checks_json) c WHERE json_extract(c.value,'$.status')<>'pass'))
 OR (NEW.status='preflight_failed' AND NOT EXISTS(SELECT 1 FROM json_each(NEW.checks_json) c WHERE json_extract(c.value,'$.status')='blocked'))
BEGIN SELECT RAISE(ABORT,'Close preflight checks must be complete and consistent'); END;

CREATE TRIGGER p0_close_status_guard BEFORE UPDATE ON internal_close_runs
WHEN NEW.book_id NOT IN(SELECT id FROM close_reviewable_books)
 OR NOT(NEW.status=OLD.status OR (OLD.status='preflight_passed' AND NEW.status='reviewed')
  OR (OLD.status='reviewed' AND NEW.status='locked') OR (OLD.status='locked' AND NEW.status='published'))
BEGIN SELECT RAISE(ABORT,'Invalid close status transition or current review permission'); END;

CREATE TRIGGER p0_close_review_actor_guard BEFORE UPDATE ON internal_close_runs
WHEN OLD.status='preflight_passed' AND NEW.status='reviewed' AND (
 NEW.reviewed_by IS NOT accounting_actor_user() OR NEW.reviewed_by=NEW.created_by OR NEW.reviewed_at IS NULL)
BEGIN SELECT RAISE(ABORT,'Independent current close reviewer required'); END;

CREATE TRIGGER p0_close_review_revision BEFORE UPDATE ON internal_close_runs
WHEN NEW.status IN('reviewed','locked') AND OLD.status<>NEW.status AND NOT coalesce((json_valid(NEW.dependencies_json) AND json_extract(NEW.dependencies_json,'$.version')=1
 AND json_extract(NEW.dependencies_json,'$.book.id')=NEW.book_id
 AND json_extract(NEW.dependencies_json,'$.ledger.id')=(SELECT ledger_id FROM ledger_periods WHERE book_id=NEW.book_id)
 AND NOT EXISTS(SELECT 1 FROM json_each(NEW.dependencies_json) j
  WHERE j.key IN('book','ledger','template') AND json_extract(j.value,'$.revision') IS NOT
   coalesce((SELECT revision FROM close_dependency_revisions WHERE scope_kind=j.key AND scope_id=json_extract(j.value,'$.id')),0))
 AND EXISTS(SELECT 1 FROM report_runs rr JOIN books bb ON bb.id=rr.book_id
  JOIN periods pp ON pp.book_id=bb.id AND pp.period=bb.period
  JOIN report_templates tt ON tt.id=rr.template_id AND tt.version=rr.template_version
  WHERE rr.id=NEW.report_run_id AND rr.book_id=NEW.book_id AND rr.period=NEW.period
   AND bb.period=NEW.period AND pp.status='open' AND rr.status IN('reviewed','published')
   AND tt.active=1 AND json_extract(NEW.dependencies_json,'$.template.id')=rr.template_id)),0)
BEGIN SELECT RAISE(ABORT,'Close dependencies changed; repeat preflight and review'); END;

CREATE TRIGGER p0_close_matching_lock_guard BEFORE UPDATE ON internal_close_runs
WHEN OLD.status='reviewed' AND NEW.status='locked' AND (
 NEW.locked_by IS NOT accounting_actor_user() OR NEW.locked_at IS NULL
 OR NOT EXISTS(SELECT 1 FROM internal_period_locks l WHERE l.close_run_id=NEW.id
  AND l.book_id=NEW.book_id AND l.period=NEW.period AND l.locked_by=accounting_actor_user()))
BEGIN SELECT RAISE(ABORT,'Matching current period lock required'); END;

CREATE TRIGGER p0_close_publish_guard BEFORE UPDATE ON internal_close_runs
WHEN OLD.status='locked' AND NEW.status='published' AND (
 NEW.published_by IS NOT accounting_actor_user() OR NEW.published_at IS NULL
 OR NOT EXISTS(SELECT 1 FROM report_runs r WHERE r.id=NEW.report_run_id
  AND r.book_id=NEW.book_id AND r.period=NEW.period AND r.status='published'))
BEGIN SELECT RAISE(ABORT,'Published bound report required'); END;

CREATE TRIGGER p0_close_lock_revision BEFORE INSERT ON internal_period_locks
WHEN NEW.book_id NOT IN(SELECT id FROM close_reviewable_books)
 OR NEW.locked_by IS NOT accounting_actor_user()
 OR NOT EXISTS(SELECT 1 FROM internal_close_runs r JOIN books b ON b.id=r.book_id
 JOIN ledger_periods lp ON lp.book_id=b.id
 WHERE r.id=NEW.close_run_id AND r.status='reviewed' AND r.reviewed_by IS NOT NULL
 AND r.reviewed_by<>r.created_by AND r.reviewed_at IS NOT NULL
 AND r.book_id=NEW.book_id AND r.period=NEW.period AND b.period=NEW.period
 AND b.firm_id=NEW.firm_id AND b.company_id=NEW.company_id AND lp.ledger_id=NEW.ledger_id
 AND (json_valid(r.dependencies_json) AND json_extract(r.dependencies_json,'$.version')=1
 AND json_extract(r.dependencies_json,'$.book.id')=r.book_id
 AND json_extract(r.dependencies_json,'$.ledger.id')=(SELECT ledger_id FROM ledger_periods WHERE book_id=r.book_id)
 AND NOT EXISTS(SELECT 1 FROM json_each(r.dependencies_json) j
  WHERE j.key IN('book','ledger','template') AND json_extract(j.value,'$.revision') IS NOT
   coalesce((SELECT revision FROM close_dependency_revisions WHERE scope_kind=j.key AND scope_id=json_extract(j.value,'$.id')),0))
 AND EXISTS(SELECT 1 FROM report_runs rr JOIN books bb ON bb.id=rr.book_id
  JOIN periods pp ON pp.book_id=bb.id AND pp.period=bb.period
  JOIN report_templates tt ON tt.id=rr.template_id AND tt.version=rr.template_version
  WHERE rr.id=r.report_run_id AND rr.book_id=r.book_id AND rr.period=r.period
   AND bb.period=r.period AND pp.status='open' AND rr.status IN('reviewed','published')
   AND tt.active=1 AND json_extract(r.dependencies_json,'$.template.id')=rr.template_id)))
BEGIN SELECT RAISE(ABORT,'Current independently reviewed close dependencies required'); END;

CREATE TRIGGER p0_close_evidence_immutable BEFORE UPDATE ON internal_close_runs
WHEN NEW.id IS NOT OLD.id OR NEW.book_id IS NOT OLD.book_id OR NEW.period IS NOT OLD.period OR NEW.version IS NOT OLD.version OR NEW.inputs_fingerprint IS NOT OLD.inputs_fingerprint OR NEW.checks_json IS NOT OLD.checks_json OR NEW.dependencies_json IS NOT OLD.dependencies_json OR NEW.report_run_id IS NOT OLD.report_run_id OR NEW.created_by IS NOT OLD.created_by OR NEW.created_at IS NOT OLD.created_at
BEGIN SELECT RAISE(ABORT,'Close preflight evidence is immutable'); END;

CREATE TRIGGER p0_close_reviewed_immutable BEFORE UPDATE ON internal_close_runs
WHEN (NEW.reviewed_by IS NOT OLD.reviewed_by OR NEW.reviewed_at IS NOT OLD.reviewed_at)
 AND NOT(OLD.status='preflight_passed' AND NEW.status='reviewed')
BEGIN SELECT RAISE(ABORT,'Close prior approvals are immutable'); END;

CREATE TRIGGER p0_close_locked_immutable BEFORE UPDATE ON internal_close_runs
WHEN (NEW.locked_by IS NOT OLD.locked_by OR NEW.locked_at IS NOT OLD.locked_at)
 AND NOT(OLD.status='reviewed' AND NEW.status='locked')
BEGIN SELECT RAISE(ABORT,'Close prior approvals are immutable'); END;

CREATE TRIGGER p0_close_published_immutable BEFORE UPDATE ON internal_close_runs
WHEN (NEW.published_by IS NOT OLD.published_by OR NEW.published_at IS NOT OLD.published_at)
 AND NOT(OLD.status='locked' AND NEW.status='published')
BEGIN SELECT RAISE(ABORT,'Close prior approvals are immutable'); END;

CREATE TRIGGER p0_close_book_identity_locked BEFORE UPDATE ON books
WHEN EXISTS(SELECT 1 FROM internal_period_locks WHERE book_id=OLD.id)
 AND (NEW.period IS NOT OLD.period OR NEW.firm_id IS NOT OLD.firm_id OR NEW.company_id IS NOT OLD.company_id)
BEGIN SELECT RAISE(ABORT,'Locked book identity cannot change'); END;

CREATE TRIGGER p0_close_ledger_periods_locked_insert BEFORE INSERT ON ledger_periods
WHEN EXISTS(SELECT 1 FROM internal_period_locks WHERE book_id=NEW.book_id)
BEGIN SELECT RAISE(ABORT,'Internally locked period evidence cannot change'); END;
CREATE TRIGGER p0_close_ledger_periods_locked_update BEFORE UPDATE ON ledger_periods
WHEN EXISTS(SELECT 1 FROM internal_period_locks WHERE book_id=OLD.book_id)
BEGIN SELECT RAISE(ABORT,'Internally locked period evidence cannot change'); END;
CREATE TRIGGER p0_close_ledger_periods_locked_delete BEFORE DELETE ON ledger_periods
WHEN EXISTS(SELECT 1 FROM internal_period_locks WHERE book_id=OLD.book_id)
BEGIN SELECT RAISE(ABORT,'Internally locked period evidence cannot change'); END;

CREATE TRIGGER p0_close_bound_ledger_period_update BEFORE UPDATE ON ledger_periods
WHEN NEW.ledger_id IS NOT OLD.ledger_id AND (
 EXISTS(SELECT 1 FROM opening_bindings WHERE book_id=OLD.book_id)
 OR EXISTS(SELECT 1 FROM vouchers WHERE book_id=OLD.book_id))
BEGIN SELECT RAISE(ABORT,'Formally bound book ledger identity cannot change'); END;
CREATE TRIGGER p0_close_bound_ledger_period_delete BEFORE DELETE ON ledger_periods
WHEN EXISTS(SELECT 1 FROM opening_bindings WHERE book_id=OLD.book_id)
 OR EXISTS(SELECT 1 FROM vouchers WHERE book_id=OLD.book_id)
BEGIN SELECT RAISE(ABORT,'Formally bound book ledger identity cannot change'); END;
