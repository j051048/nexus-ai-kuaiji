-- compatibility: incremental
-- Add guards without rewriting historical facts. Legacy nonzero adjustments remain unresolved.
ALTER TABLE bank_recon_adjustments ADD COLUMN bank_account_id TEXT REFERENCES bank_accounts(id);
CREATE TABLE bank_adjustment_resolutions(
 id TEXT PRIMARY KEY,book_id TEXT NOT NULL REFERENCES books(id),firm_id TEXT NOT NULL,company_id TEXT NOT NULL,
 adjustment_id TEXT NOT NULL UNIQUE REFERENCES bank_recon_adjustments(id),bank_line_id TEXT NOT NULL UNIQUE REFERENCES bank_statement_lines(id),
 reason TEXT NOT NULL CHECK(length(trim(reason)) BETWEEN 3 AND 1000),created_by TEXT NOT NULL REFERENCES users(id),created_at TEXT NOT NULL
);
CREATE TRIGGER c01_exact_subledger_allocations_insert BEFORE INSERT ON subledger_allocations BEGIN
 SELECT CASE WHEN (EXISTS(SELECT 1 FROM books b WHERE b.id=NEW.book_id AND b.firm_id=NEW.firm_id AND b.company_id=NEW.company_id)
 AND NEW.created_by=accounting_actor_user() AND NEW.firm_id=accounting_actor_firm() AND EXISTS(SELECT 1 FROM service_book_user_permissions WHERE book_id=NEW.book_id AND user_id=accounting_actor_user() AND can_review)
 AND EXISTS(SELECT 1 FROM subledger_items i JOIN vouchers v ON v.id=NEW.voucher_id
 WHERE i.id=NEW.item_id AND i.book_id=NEW.book_id AND i.firm_id=NEW.firm_id AND i.company_id=NEW.company_id
 AND v.book_id=NEW.book_id AND NEW.allocated_on=v.voucher_date AND i.original_date<=NEW.allocated_on
 AND NEW.amount_cents>0 AND NEW.amount_cents+COALESCE((SELECT SUM(a.amount_cents) FROM subledger_allocations a
 WHERE a.item_id=i.id AND NOT EXISTS(SELECT 1 FROM subledger_allocation_reversals r WHERE r.allocation_id=a.id)),0)<=i.amount_cents
 AND NEW.amount_cents+COALESCE((SELECT SUM(a.amount_cents) FROM subledger_allocations a JOIN subledger_items j ON j.id=a.item_id
 WHERE a.voucher_id=NEW.voucher_id AND j.control_account_id=i.control_account_id AND (NOT EXISTS(SELECT key,value FROM json_each(j.aux_json) EXCEPT SELECT key,value FROM json_each(i.aux_json)) AND NOT EXISTS(SELECT key,value FROM json_each(i.aux_json) EXCEPT SELECT key,value FROM json_each(j.aux_json)))
 AND (j.kind IN('receivable','advance_paid'))=(i.kind IN('receivable','advance_paid'))
 AND NOT EXISTS(SELECT 1 FROM subledger_allocation_reversals r WHERE r.allocation_id=a.id)),0)
 <=COALESCE((SELECT SUM(CASE WHEN i.kind IN('receivable','advance_paid') THEN l.credit_cents ELSE l.debit_cents END)
 FROM voucher_lines l WHERE l.voucher_id=NEW.voucher_id AND l.account_id=i.control_account_id AND (NOT EXISTS(SELECT key,value FROM json_each(l.aux_json) EXCEPT SELECT key,value FROM json_each(i.aux_json)) AND NOT EXISTS(SELECT key,value FROM json_each(i.aux_json) EXCEPT SELECT key,value FROM json_each(l.aux_json)))),0))) IS NOT TRUE THEN RAISE(ABORT,'C01 fact scope, auxiliary bucket or capacity mismatch') END; END;
CREATE TRIGGER c01_exact_subledger_allocation_reversals_insert BEFORE INSERT ON subledger_allocation_reversals BEGIN
 SELECT CASE WHEN (EXISTS(SELECT 1 FROM books b WHERE b.id=NEW.book_id AND b.firm_id=NEW.firm_id AND b.company_id=NEW.company_id)
 AND NEW.created_by=accounting_actor_user() AND NEW.firm_id=accounting_actor_firm() AND EXISTS(SELECT 1 FROM service_book_user_permissions WHERE book_id=NEW.book_id AND user_id=accounting_actor_user() AND can_review) AND EXISTS(SELECT 1 FROM subledger_allocations a
 WHERE a.id=NEW.allocation_id AND a.book_id=NEW.book_id AND a.firm_id=NEW.firm_id AND a.company_id=NEW.company_id)) IS NOT TRUE THEN RAISE(ABORT,'C01 fact scope, auxiliary bucket or capacity mismatch') END; END;
CREATE TRIGGER c01_exact_bank_accounts_insert BEFORE INSERT ON bank_accounts BEGIN
 SELECT CASE WHEN (EXISTS(SELECT 1 FROM books b WHERE b.id=NEW.book_id AND b.firm_id=NEW.firm_id AND b.company_id=NEW.company_id)
 AND NEW.created_by=accounting_actor_user() AND NEW.firm_id=accounting_actor_firm() AND EXISTS(SELECT 1 FROM service_book_user_permissions WHERE book_id=NEW.book_id AND user_id=accounting_actor_user() AND can_review) AND NEW.ledger_account_id IS NOT NULL
 AND NOT EXISTS(SELECT 1 FROM bank_accounts a WHERE a.id<>NEW.id AND a.book_id=NEW.book_id AND a.active IS TRUE
 AND NEW.active IS TRUE AND a.ledger_account_id=NEW.ledger_account_id AND (NOT EXISTS(SELECT key,value FROM json_each(a.aux_json) EXCEPT SELECT key,value FROM json_each(NEW.aux_json)) AND NOT EXISTS(SELECT key,value FROM json_each(NEW.aux_json) EXCEPT SELECT key,value FROM json_each(a.aux_json))))) IS NOT TRUE THEN RAISE(ABORT,'C01 fact scope, auxiliary bucket or capacity mismatch') END; END;
CREATE TRIGGER c01_exact_bank_accounts_update BEFORE UPDATE ON bank_accounts BEGIN
 SELECT CASE WHEN (EXISTS(SELECT 1 FROM books b WHERE b.id=NEW.book_id AND b.firm_id=NEW.firm_id AND b.company_id=NEW.company_id)
 AND NEW.created_by=accounting_actor_user() AND NEW.firm_id=accounting_actor_firm() AND EXISTS(SELECT 1 FROM service_book_user_permissions WHERE book_id=NEW.book_id AND user_id=accounting_actor_user() AND can_review) AND NEW.ledger_account_id IS NOT NULL
 AND NOT EXISTS(SELECT 1 FROM bank_accounts a WHERE a.id<>NEW.id AND a.book_id=NEW.book_id AND a.active IS TRUE
 AND NEW.active IS TRUE AND a.ledger_account_id=NEW.ledger_account_id AND (NOT EXISTS(SELECT key,value FROM json_each(a.aux_json) EXCEPT SELECT key,value FROM json_each(NEW.aux_json)) AND NOT EXISTS(SELECT key,value FROM json_each(NEW.aux_json) EXCEPT SELECT key,value FROM json_each(a.aux_json))))) IS NOT TRUE THEN RAISE(ABORT,'C01 fact scope, auxiliary bucket or capacity mismatch') END; END;
CREATE TRIGGER c01_exact_bank_recon_adjustments_insert BEFORE INSERT ON bank_recon_adjustments BEGIN
 SELECT CASE WHEN (NEW.created_by=accounting_actor_user() AND EXISTS(SELECT 1 FROM service_book_user_permissions WHERE book_id=NEW.book_id AND user_id=accounting_actor_user() AND can_review) AND EXISTS(SELECT 1 FROM books b WHERE b.id=NEW.book_id AND b.period=NEW.period)
 AND (NEW.amount_cents=0 OR NEW.bank_account_id IS NOT NULL)
 AND (NEW.bank_account_id IS NULL OR EXISTS(SELECT 1 FROM bank_accounts a WHERE a.id=NEW.bank_account_id AND a.book_id=NEW.book_id AND a.active IS TRUE))) IS NOT TRUE THEN RAISE(ABORT,'C01 fact scope, auxiliary bucket or capacity mismatch') END; END;
CREATE TRIGGER c01_exact_bank_adjustment_resolutions_insert BEFORE INSERT ON bank_adjustment_resolutions BEGIN
 SELECT CASE WHEN (EXISTS(SELECT 1 FROM books b WHERE b.id=NEW.book_id AND b.firm_id=NEW.firm_id AND b.company_id=NEW.company_id)
 AND NEW.created_by=accounting_actor_user() AND NEW.firm_id=accounting_actor_firm() AND EXISTS(SELECT 1 FROM service_book_user_permissions WHERE book_id=NEW.book_id AND user_id=accounting_actor_user() AND can_review) AND EXISTS(SELECT 1 FROM bank_recon_adjustments a JOIN bank_statement_lines l ON l.id=NEW.bank_line_id
 WHERE a.id=NEW.adjustment_id AND a.book_id=NEW.book_id AND l.book_id=NEW.book_id AND a.bank_account_id=l.bank_account_id
 AND a.amount_cents<>0 AND a.amount_cents=l.amount_cents AND l.status='matched' AND l.matched_voucher_line_id IS NOT NULL AND a.created_by<>NEW.created_by)) IS NOT TRUE THEN RAISE(ABORT,'C01 fact scope, auxiliary bucket or capacity mismatch') END; END;
CREATE TRIGGER c01_exact_bank_statement_lines_insert BEFORE INSERT ON bank_statement_lines BEGIN
 SELECT CASE WHEN (EXISTS(SELECT 1 FROM books b WHERE b.id=NEW.book_id AND b.period=NEW.period AND substr(NEW.line_date,1,7)=b.period)
 AND (NEW.bank_account_id IS NULL OR EXISTS(SELECT 1 FROM bank_accounts a WHERE a.id=NEW.bank_account_id AND a.book_id=NEW.book_id))
 AND (NEW.status<>'matched' OR EXISTS(SELECT 1 FROM voucher_lines l JOIN vouchers v ON v.id=l.voucher_id
 WHERE l.id=NEW.matched_voucher_line_id AND v.book_id=NEW.book_id AND l.debit_cents-l.credit_cents=NEW.amount_cents
 AND (NEW.bank_account_id IS NULL OR EXISTS(SELECT 1 FROM bank_accounts a WHERE a.id=NEW.bank_account_id AND a.ledger_account_id=l.account_id AND (NOT EXISTS(SELECT key,value FROM json_each(a.aux_json) EXCEPT SELECT key,value FROM json_each(l.aux_json)) AND NOT EXISTS(SELECT key,value FROM json_each(l.aux_json) EXCEPT SELECT key,value FROM json_each(a.aux_json)))))
 AND NOT EXISTS(SELECT 1 FROM bank_statement_lines other_line WHERE other_line.id<>NEW.id AND other_line.matched_voucher_line_id=NEW.matched_voucher_line_id)))) IS NOT TRUE THEN RAISE(ABORT,'C01 fact scope, auxiliary bucket or capacity mismatch') END; END;
CREATE TRIGGER c01_exact_bank_statement_lines_update BEFORE UPDATE ON bank_statement_lines BEGIN
 SELECT CASE WHEN (EXISTS(SELECT 1 FROM books b WHERE b.id=NEW.book_id AND b.period=NEW.period AND substr(NEW.line_date,1,7)=b.period)
 AND (NEW.bank_account_id IS NULL OR EXISTS(SELECT 1 FROM bank_accounts a WHERE a.id=NEW.bank_account_id AND a.book_id=NEW.book_id))
 AND (NEW.status<>'matched' OR EXISTS(SELECT 1 FROM voucher_lines l JOIN vouchers v ON v.id=l.voucher_id
 WHERE l.id=NEW.matched_voucher_line_id AND v.book_id=NEW.book_id AND l.debit_cents-l.credit_cents=NEW.amount_cents
 AND (NEW.bank_account_id IS NULL OR EXISTS(SELECT 1 FROM bank_accounts a WHERE a.id=NEW.bank_account_id AND a.ledger_account_id=l.account_id AND (NOT EXISTS(SELECT key,value FROM json_each(a.aux_json) EXCEPT SELECT key,value FROM json_each(l.aux_json)) AND NOT EXISTS(SELECT key,value FROM json_each(l.aux_json) EXCEPT SELECT key,value FROM json_each(a.aux_json)))))
 AND NOT EXISTS(SELECT 1 FROM bank_statement_lines other_line WHERE other_line.id<>NEW.id AND other_line.matched_voucher_line_id=NEW.matched_voucher_line_id)))) IS NOT TRUE THEN RAISE(ABORT,'C01 fact scope, auxiliary bucket or capacity mismatch') END; END;
CREATE TRIGGER c01_resolution_closed BEFORE INSERT ON bank_adjustment_resolutions BEGIN
 SELECT CASE WHEN EXISTS(SELECT 1 FROM internal_period_locks WHERE book_id=NEW.book_id) THEN RAISE(ABORT,'Locked period') END; END;
CREATE TRIGGER c01_resolution_revision AFTER INSERT ON bank_adjustment_resolutions BEGIN
 INSERT INTO close_dependency_revisions(scope_kind,scope_id,revision) VALUES('book',NEW.book_id,1) ON CONFLICT(scope_kind,scope_id) DO UPDATE SET revision=revision+1; END;
CREATE TRIGGER c01_bank_adjustment_resolutions_immutable_update BEFORE UPDATE ON bank_adjustment_resolutions BEGIN SELECT RAISE(ABORT,'C01 facts are immutable'); END;
CREATE TRIGGER c01_bank_adjustment_resolutions_immutable_delete BEFORE DELETE ON bank_adjustment_resolutions BEGIN SELECT RAISE(ABORT,'C01 facts are immutable'); END;
CREATE TRIGGER c01_bank_recon_adjustments_immutable_update BEFORE UPDATE ON bank_recon_adjustments BEGIN SELECT RAISE(ABORT,'C01 facts are immutable'); END;
CREATE TRIGGER c01_bank_recon_adjustments_immutable_delete BEFORE DELETE ON bank_recon_adjustments BEGIN SELECT RAISE(ABORT,'C01 facts are immutable'); END;
