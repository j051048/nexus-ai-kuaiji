-- compatibility: incremental
ALTER TABLE accounting.bank_recon_adjustments ADD COLUMN bank_account_id TEXT REFERENCES accounting.bank_accounts(id);
CREATE TABLE accounting.bank_adjustment_resolutions(
 id TEXT PRIMARY KEY,book_id TEXT NOT NULL REFERENCES accounting.books(id),firm_id TEXT NOT NULL,company_id TEXT NOT NULL,
 adjustment_id TEXT NOT NULL UNIQUE REFERENCES accounting.bank_recon_adjustments(id),bank_line_id TEXT NOT NULL UNIQUE REFERENCES accounting.bank_statement_lines(id),
 reason TEXT NOT NULL CHECK(length(btrim(reason)) BETWEEN 3 AND 1000),created_by TEXT NOT NULL REFERENCES accounting.users(id),created_at TEXT NOT NULL
);
CREATE FUNCTION accounting.c01_exact_fact_guard() RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER
 SET search_path=pg_catalog,accounting,pg_temp AS $$ DECLARE b RECORD; ok BOOLEAN; BEGIN
 SELECT firm_id,company_id INTO b FROM accounting.books WHERE id=NEW.book_id;
 IF NOT FOUND THEN RAISE EXCEPTION 'Invalid C01 scope' USING ERRCODE='23514'; END IF;
 PERFORM accounting.lock_company_write(b.firm_id,b.company_id);
 IF TG_TABLE_NAME='subledger_allocations' THEN
 SELECT (EXISTS(SELECT 1 FROM accounting.books b WHERE b.id=NEW.book_id AND b.firm_id=NEW.firm_id AND b.company_id=NEW.company_id)
 AND NEW.created_by=accounting.actor_user() AND NEW.firm_id=accounting.actor_firm() AND accounting.book_review_allowed(NEW.book_id)
 AND EXISTS(SELECT 1 FROM accounting.subledger_items i JOIN accounting.vouchers v ON v.id=NEW.voucher_id
 WHERE i.id=NEW.item_id AND i.book_id=NEW.book_id AND i.firm_id=NEW.firm_id AND i.company_id=NEW.company_id
 AND v.book_id=NEW.book_id AND NEW.allocated_on=v.voucher_date AND i.original_date<=NEW.allocated_on
 AND NEW.amount_cents>0 AND NEW.amount_cents+COALESCE((SELECT SUM(a.amount_cents) FROM accounting.subledger_allocations a
 WHERE a.item_id=i.id AND NOT EXISTS(SELECT 1 FROM accounting.subledger_allocation_reversals r WHERE r.allocation_id=a.id)),0)<=i.amount_cents
 AND NEW.amount_cents+COALESCE((SELECT SUM(a.amount_cents) FROM accounting.subledger_allocations a JOIN accounting.subledger_items j ON j.id=a.item_id
 WHERE a.voucher_id=NEW.voucher_id AND j.control_account_id=i.control_account_id AND ((j.aux_json)::jsonb=(i.aux_json)::jsonb)
 AND (j.kind IN('receivable','advance_paid'))=(i.kind IN('receivable','advance_paid'))
 AND NOT EXISTS(SELECT 1 FROM accounting.subledger_allocation_reversals r WHERE r.allocation_id=a.id)),0)
 <=COALESCE((SELECT SUM(CASE WHEN i.kind IN('receivable','advance_paid') THEN l.credit_cents ELSE l.debit_cents END)
 FROM accounting.voucher_lines l WHERE l.voucher_id=NEW.voucher_id AND l.account_id=i.control_account_id AND ((l.aux_json)::jsonb=(i.aux_json)::jsonb)),0))) INTO ok;
 ELSIF TG_TABLE_NAME='subledger_allocation_reversals' THEN
 SELECT (EXISTS(SELECT 1 FROM accounting.books b WHERE b.id=NEW.book_id AND b.firm_id=NEW.firm_id AND b.company_id=NEW.company_id)
 AND NEW.created_by=accounting.actor_user() AND NEW.firm_id=accounting.actor_firm() AND accounting.book_review_allowed(NEW.book_id) AND EXISTS(SELECT 1 FROM accounting.subledger_allocations a
 WHERE a.id=NEW.allocation_id AND a.book_id=NEW.book_id AND a.firm_id=NEW.firm_id AND a.company_id=NEW.company_id)) INTO ok;
 ELSIF TG_TABLE_NAME='bank_accounts' THEN
 SELECT (EXISTS(SELECT 1 FROM accounting.books b WHERE b.id=NEW.book_id AND b.firm_id=NEW.firm_id AND b.company_id=NEW.company_id)
 AND NEW.created_by=accounting.actor_user() AND NEW.firm_id=accounting.actor_firm() AND accounting.book_review_allowed(NEW.book_id) AND NEW.ledger_account_id IS NOT NULL
 AND NOT EXISTS(SELECT 1 FROM accounting.bank_accounts a WHERE a.id<>NEW.id AND a.book_id=NEW.book_id AND a.active IS TRUE
 AND NEW.active IS TRUE AND a.ledger_account_id=NEW.ledger_account_id AND ((a.aux_json)::jsonb=(NEW.aux_json)::jsonb))) INTO ok;
 ELSIF TG_TABLE_NAME='bank_recon_adjustments' THEN
 SELECT (NEW.created_by=accounting.actor_user() AND accounting.book_review_allowed(NEW.book_id) AND EXISTS(SELECT 1 FROM accounting.books b WHERE b.id=NEW.book_id AND b.period=NEW.period)
 AND (NEW.amount_cents=0 OR NEW.bank_account_id IS NOT NULL)
 AND (NEW.bank_account_id IS NULL OR EXISTS(SELECT 1 FROM accounting.bank_accounts a WHERE a.id=NEW.bank_account_id AND a.book_id=NEW.book_id AND a.active IS TRUE))) INTO ok;
 ELSIF TG_TABLE_NAME='bank_adjustment_resolutions' THEN
 SELECT (EXISTS(SELECT 1 FROM accounting.books b WHERE b.id=NEW.book_id AND b.firm_id=NEW.firm_id AND b.company_id=NEW.company_id)
 AND NEW.created_by=accounting.actor_user() AND NEW.firm_id=accounting.actor_firm() AND accounting.book_review_allowed(NEW.book_id) AND EXISTS(SELECT 1 FROM accounting.bank_recon_adjustments a JOIN accounting.bank_statement_lines l ON l.id=NEW.bank_line_id
 WHERE a.id=NEW.adjustment_id AND a.book_id=NEW.book_id AND l.book_id=NEW.book_id AND a.bank_account_id=l.bank_account_id
 AND a.amount_cents<>0 AND a.amount_cents=l.amount_cents AND l.status='matched' AND l.matched_voucher_line_id IS NOT NULL AND a.created_by<>NEW.created_by)) INTO ok;
 ELSIF TG_TABLE_NAME='bank_statement_lines' THEN
 SELECT (EXISTS(SELECT 1 FROM accounting.books b WHERE b.id=NEW.book_id AND b.period=NEW.period AND substr(NEW.line_date,1,7)=b.period)
 AND (NEW.bank_account_id IS NULL OR EXISTS(SELECT 1 FROM accounting.bank_accounts a WHERE a.id=NEW.bank_account_id AND a.book_id=NEW.book_id))
 AND (NEW.status<>'matched' OR EXISTS(SELECT 1 FROM accounting.voucher_lines l JOIN accounting.vouchers v ON v.id=l.voucher_id
 WHERE l.id=NEW.matched_voucher_line_id AND v.book_id=NEW.book_id AND l.debit_cents-l.credit_cents=NEW.amount_cents
 AND (NEW.bank_account_id IS NULL OR EXISTS(SELECT 1 FROM accounting.bank_accounts a WHERE a.id=NEW.bank_account_id AND a.ledger_account_id=l.account_id AND ((a.aux_json)::jsonb=(l.aux_json)::jsonb)))
 AND NOT EXISTS(SELECT 1 FROM accounting.bank_statement_lines other_line WHERE other_line.id<>NEW.id AND other_line.matched_voucher_line_id=NEW.matched_voucher_line_id)))) INTO ok;
 END IF; IF ok IS DISTINCT FROM TRUE THEN RAISE EXCEPTION 'C01 fact scope, auxiliary bucket or capacity mismatch' USING ERRCODE='23514'; END IF; RETURN NEW; END $$;
REVOKE ALL ON FUNCTION accounting.c01_exact_fact_guard() FROM PUBLIC,accounting_app,accounting_worker,accounting_system;
CREATE TRIGGER c01_exact_fact_guard BEFORE INSERT ON accounting.subledger_allocations FOR EACH ROW EXECUTE FUNCTION accounting.c01_exact_fact_guard();
CREATE TRIGGER c01_exact_fact_guard BEFORE INSERT ON accounting.subledger_allocation_reversals FOR EACH ROW EXECUTE FUNCTION accounting.c01_exact_fact_guard();
CREATE TRIGGER c01_exact_fact_guard BEFORE INSERT OR UPDATE ON accounting.bank_accounts FOR EACH ROW EXECUTE FUNCTION accounting.c01_exact_fact_guard();
CREATE TRIGGER c01_exact_fact_guard BEFORE INSERT ON accounting.bank_recon_adjustments FOR EACH ROW EXECUTE FUNCTION accounting.c01_exact_fact_guard();
CREATE TRIGGER c01_exact_fact_guard BEFORE INSERT ON accounting.bank_adjustment_resolutions FOR EACH ROW EXECUTE FUNCTION accounting.c01_exact_fact_guard();
CREATE TRIGGER c01_exact_fact_guard BEFORE INSERT OR UPDATE ON accounting.bank_statement_lines FOR EACH ROW EXECUTE FUNCTION accounting.c01_exact_fact_guard();
ALTER TABLE accounting.bank_adjustment_resolutions ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.bank_adjustment_resolutions FORCE ROW LEVEL SECURITY;
REVOKE ALL ON accounting.bank_adjustment_resolutions FROM PUBLIC,accounting_app,accounting_worker,accounting_system,accounting_projection;
GRANT SELECT,INSERT ON accounting.bank_adjustment_resolutions TO accounting_app;
CREATE POLICY c01_resolution_read ON accounting.bank_adjustment_resolutions FOR SELECT TO accounting_app USING(accounting.book_allowed(book_id));
CREATE POLICY c01_resolution_write ON accounting.bank_adjustment_resolutions FOR INSERT TO accounting_app WITH CHECK(accounting.book_review_allowed(book_id) AND created_by=accounting.actor_user() AND firm_id=accounting.actor_firm());
CREATE TRIGGER c01_resolution_company BEFORE INSERT ON accounting.bank_adjustment_resolutions FOR EACH ROW EXECUTE FUNCTION accounting.company_write_table_guard();
CREATE TRIGGER c01_resolution_closed BEFORE INSERT ON accounting.bank_adjustment_resolutions FOR EACH ROW EXECUTE FUNCTION accounting.close_dependency_write_guard('book');
CREATE TRIGGER c01_resolution_revision AFTER INSERT ON accounting.bank_adjustment_resolutions FOR EACH ROW EXECUTE FUNCTION accounting.close_dependency_bump('book');
CREATE TRIGGER c01_resolution_immutable BEFORE UPDATE OR DELETE ON accounting.bank_adjustment_resolutions FOR EACH ROW EXECUTE FUNCTION accounting.delivery_immutable();
CREATE TRIGGER c01_adjustment_immutable BEFORE UPDATE OR DELETE ON accounting.bank_recon_adjustments FOR EACH ROW EXECUTE FUNCTION accounting.delivery_immutable();
