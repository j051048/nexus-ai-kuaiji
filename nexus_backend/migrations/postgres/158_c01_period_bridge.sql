-- compatibility: incremental
-- Forward repair: avoid PL/pgSQL record/SQL alias ambiguity and bind legacy bank matches to cash/bank lines.
CREATE OR REPLACE FUNCTION accounting.c01_exact_fact_guard() RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER
 SET search_path=pg_catalog,accounting,pg_temp AS $$ DECLARE scope_book RECORD; ok BOOLEAN; BEGIN
 SELECT firm_id,company_id INTO scope_book FROM accounting.books WHERE id=NEW.book_id;
 IF NOT FOUND THEN RAISE EXCEPTION 'Invalid C01 scope' USING ERRCODE='23514'; END IF;
 PERFORM accounting.lock_company_write(scope_book.firm_id,scope_book.company_id);
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
 AND ((NEW.bank_account_id IS NULL AND (l.code_snapshot LIKE '1001%' OR l.code_snapshot LIKE '1002%')) OR EXISTS(SELECT 1 FROM accounting.bank_accounts a WHERE a.id=NEW.bank_account_id AND a.ledger_account_id=l.account_id AND ((a.aux_json)::jsonb=(l.aux_json)::jsonb)))
 AND NOT EXISTS(SELECT 1 FROM accounting.bank_statement_lines other_line WHERE other_line.id<>NEW.id AND other_line.matched_voucher_line_id=NEW.matched_voucher_line_id)))) INTO ok;
 END IF; IF ok IS DISTINCT FROM TRUE THEN RAISE EXCEPTION 'C01 fact scope, auxiliary bucket or capacity mismatch' USING ERRCODE='23514'; END IF; RETURN NEW; END $$;
-- compatibility: incremental
-- Reviewed stable identity and immutable, complete cross-period residual import.
CREATE TABLE accounting.ledger_bank_identities(id TEXT PRIMARY KEY,book_id TEXT NOT NULL REFERENCES accounting.books(id),firm_id TEXT NOT NULL,company_id TEXT NOT NULL,ledger_id TEXT NOT NULL REFERENCES accounting.ledgers(id),name TEXT NOT NULL,reason TEXT NOT NULL,created_by TEXT NOT NULL REFERENCES accounting.users(id),created_at TEXT NOT NULL);
CREATE TABLE accounting.bank_identity_bindings(id TEXT PRIMARY KEY,book_id TEXT NOT NULL REFERENCES accounting.books(id),firm_id TEXT NOT NULL,company_id TEXT NOT NULL,ledger_id TEXT NOT NULL REFERENCES accounting.ledgers(id),bank_account_id TEXT NOT NULL REFERENCES accounting.bank_accounts(id),stable_bank_id TEXT NOT NULL REFERENCES accounting.ledger_bank_identities(id),idempotency_key TEXT NOT NULL,request_fingerprint TEXT NOT NULL,reason TEXT NOT NULL,created_by TEXT NOT NULL REFERENCES accounting.users(id),created_at TEXT NOT NULL,UNIQUE(book_id,idempotency_key));
CREATE TABLE accounting.bank_identity_reviews(id TEXT PRIMARY KEY,book_id TEXT NOT NULL REFERENCES accounting.books(id),firm_id TEXT NOT NULL,company_id TEXT NOT NULL,binding_id TEXT NOT NULL UNIQUE REFERENCES accounting.bank_identity_bindings(id),decision TEXT NOT NULL CHECK(decision IN('approved','rejected')),reason TEXT NOT NULL,created_by TEXT NOT NULL REFERENCES accounting.users(id),created_at TEXT NOT NULL);
CREATE TABLE accounting.subledger_period_bridges(id TEXT PRIMARY KEY,book_id TEXT NOT NULL REFERENCES accounting.books(id),firm_id TEXT NOT NULL,company_id TEXT NOT NULL,ledger_id TEXT NOT NULL REFERENCES accounting.ledgers(id),source_book_id TEXT NOT NULL REFERENCES accounting.books(id),source_lock_id TEXT NOT NULL REFERENCES accounting.internal_period_locks(id),source_binding_id TEXT NOT NULL REFERENCES accounting.opening_bindings(id),target_binding_id TEXT NOT NULL REFERENCES accounting.opening_bindings(id),idempotency_key TEXT NOT NULL,request_fingerprint TEXT NOT NULL,reason TEXT NOT NULL,created_by TEXT NOT NULL REFERENCES accounting.users(id),created_at TEXT NOT NULL,UNIQUE(book_id));
CREATE TABLE accounting.subledger_bridge_items(id TEXT PRIMARY KEY,book_id TEXT NOT NULL REFERENCES accounting.books(id),firm_id TEXT NOT NULL,company_id TEXT NOT NULL,bridge_id TEXT NOT NULL REFERENCES accounting.subledger_period_bridges(id),source_item_id TEXT NOT NULL REFERENCES accounting.subledger_items(id),target_item_id TEXT NOT NULL UNIQUE REFERENCES accounting.subledger_items(id),remaining_cents BIGINT NOT NULL CHECK(remaining_cents>0),created_by TEXT NOT NULL REFERENCES accounting.users(id),created_at TEXT NOT NULL,UNIQUE(bridge_id,source_item_id));
CREATE TABLE accounting.subledger_bridge_completions(id TEXT PRIMARY KEY,book_id TEXT NOT NULL REFERENCES accounting.books(id),firm_id TEXT NOT NULL,company_id TEXT NOT NULL,bridge_id TEXT NOT NULL UNIQUE REFERENCES accounting.subledger_period_bridges(id),created_by TEXT NOT NULL REFERENCES accounting.users(id),created_at TEXT NOT NULL);
CREATE VIEW accounting.c01_opening_buckets WITH (security_invoker=true) AS SELECT b.book_id,l.account_id,(l.aux_json)::jsonb::text AS aux_key,SUM(l.opening_cents) AS cents
 FROM accounting.opening_bindings b JOIN accounting.opening_lines l ON l.package_version_id=b.package_version_id WHERE b.status='active' GROUP BY b.book_id,l.account_id,(l.aux_json)::jsonb::text;
CREATE VIEW accounting.c01_formal_buckets WITH (security_invoker=true) AS SELECT book_id,account_id,aux_key,SUM(cents) AS cents FROM (
 SELECT * FROM accounting.c01_opening_buckets UNION ALL SELECT v.book_id,l.account_id,(l.aux_json)::jsonb::text,l.debit_cents-l.credit_cents FROM accounting.voucher_lines l JOIN accounting.vouchers v ON v.id=l.voucher_id) facts GROUP BY book_id,account_id,aux_key;
CREATE VIEW accounting.c01_approved_banks WITH (security_invoker=true) AS SELECT b.*,a.ledger_account_id,(a.aux_json)::jsonb::text AS aux_key FROM accounting.bank_identity_bindings b JOIN accounting.bank_identity_reviews r ON r.binding_id=b.id AND r.decision='approved' JOIN accounting.bank_accounts a ON a.id=b.bank_account_id;
CREATE VIEW accounting.c01_item_residuals WITH (security_invoker=true) AS SELECT i.id,i.book_id,i.amount_cents-COALESCE((SELECT SUM(a.amount_cents) FROM accounting.subledger_allocations a WHERE a.item_id=i.id AND NOT EXISTS(SELECT 1 FROM accounting.subledger_allocation_reversals r WHERE r.allocation_id=a.id)),0) AS cents FROM accounting.subledger_items i WHERE i.status IN('open','partial','settled');
CREATE FUNCTION accounting.c01_prior_period(target TEXT) RETURNS TABLE(book_id TEXT,period TEXT) LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 SELECT p.book_id,p.period FROM accounting.ledger_periods p JOIN accounting.ledger_periods t ON t.ledger_id=p.ledger_id WHERE t.book_id=target AND accounting.book_allowed(target) AND p.period<t.period ORDER BY p.period DESC LIMIT 1 $$;
REVOKE ALL ON FUNCTION accounting.c01_prior_period(TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION accounting.c01_prior_period(TEXT) TO accounting_app;
GRANT SELECT ON accounting.c01_opening_buckets,accounting.c01_formal_buckets,accounting.c01_approved_banks,accounting.c01_item_residuals TO accounting_app;
CREATE FUNCTION accounting.c01_bridge_guard() RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$ DECLARE scope_book RECORD; ok BOOLEAN; BEGIN
 SELECT firm_id,company_id INTO scope_book FROM accounting.books WHERE id=NEW.book_id;
 IF NOT FOUND THEN RAISE EXCEPTION 'Invalid C01 bridge scope' USING ERRCODE='23514'; END IF;
 PERFORM accounting.lock_company_write(scope_book.firm_id,scope_book.company_id);
 IF TG_TABLE_NAME='ledger_bank_identities' THEN SELECT (NEW.created_by=accounting.actor_user() AND NEW.firm_id=accounting.actor_firm() AND accounting.book_review_allowed(NEW.book_id) AND EXISTS(SELECT 1 FROM accounting.books b WHERE b.id=NEW.book_id AND b.company_id=NEW.company_id AND b.firm_id=NEW.firm_id) AND (EXISTS(SELECT 1 FROM accounting.ledger_periods p WHERE p.book_id=NEW.book_id AND p.ledger_id=NEW.ledger_id))) INTO ok;
 ELSIF TG_TABLE_NAME='bank_identity_bindings' THEN SELECT (NEW.created_by=accounting.actor_user() AND NEW.firm_id=accounting.actor_firm() AND accounting.book_review_allowed(NEW.book_id) AND EXISTS(SELECT 1 FROM accounting.books b WHERE b.id=NEW.book_id AND b.company_id=NEW.company_id AND b.firm_id=NEW.firm_id) AND (EXISTS(SELECT 1 FROM accounting.ledger_periods p WHERE p.book_id=NEW.book_id AND p.ledger_id=NEW.ledger_id) AND EXISTS(SELECT 1 FROM accounting.ledger_bank_identities i JOIN accounting.bank_accounts a ON a.id=NEW.bank_account_id WHERE i.id=NEW.stable_bank_id AND i.ledger_id=NEW.ledger_id AND i.company_id=NEW.company_id AND a.book_id=NEW.book_id AND a.active IS TRUE))) INTO ok;
 ELSIF TG_TABLE_NAME='bank_identity_reviews' THEN SELECT (NEW.created_by=accounting.actor_user() AND NEW.firm_id=accounting.actor_firm() AND accounting.book_review_allowed(NEW.book_id) AND EXISTS(SELECT 1 FROM accounting.books b WHERE b.id=NEW.book_id AND b.company_id=NEW.company_id AND b.firm_id=NEW.firm_id) AND (EXISTS(SELECT 1 FROM accounting.bank_identity_bindings b WHERE b.id=NEW.binding_id AND b.book_id=NEW.book_id AND b.created_by<>NEW.created_by
 AND (NEW.decision='rejected' OR NOT EXISTS(SELECT 1 FROM accounting.c01_approved_banks other_bank WHERE other_bank.book_id=b.book_id AND (other_bank.bank_account_id=b.bank_account_id OR other_bank.stable_bank_id=b.stable_bank_id)))))) INTO ok;
 ELSIF TG_TABLE_NAME='subledger_period_bridges' THEN SELECT (NEW.created_by=accounting.actor_user() AND NEW.firm_id=accounting.actor_firm() AND accounting.book_review_allowed(NEW.book_id) AND EXISTS(SELECT 1 FROM accounting.books b WHERE b.id=NEW.book_id AND b.company_id=NEW.company_id AND b.firm_id=NEW.firm_id) AND (EXISTS(SELECT 1 FROM accounting.ledger_periods p WHERE p.book_id=NEW.book_id AND p.ledger_id=NEW.ledger_id) AND accounting.book_review_allowed(NEW.source_book_id) AND EXISTS(SELECT 1 FROM accounting.books s JOIN accounting.books t ON t.id=NEW.book_id
 JOIN accounting.ledger_periods p ON p.book_id=s.id AND p.ledger_id=NEW.ledger_id
 JOIN accounting.internal_period_locks k ON k.id=NEW.source_lock_id AND k.book_id=s.id
 JOIN accounting.opening_bindings old_binding ON old_binding.id=NEW.source_binding_id AND old_binding.book_id=s.id AND old_binding.status='active'
 JOIN accounting.opening_bindings new_binding ON new_binding.id=NEW.target_binding_id AND new_binding.book_id=t.id AND new_binding.status='active'
 JOIN accounting.opening_package_versions opening_version ON opening_version.id=new_binding.package_version_id AND opening_version.status='frozen' AND opening_version.config_fingerprint=new_binding.snapshot_fingerprint
 WHERE s.id=NEW.source_book_id AND s.firm_id=NEW.firm_id AND s.company_id=NEW.company_id AND t.period=to_char((s.period||'-01')::date+interval '1 month','YYYY-MM'))
 AND NOT EXISTS(SELECT 1 FROM accounting.vouchers WHERE book_id=NEW.book_id)
 AND NOT EXISTS(SELECT 1 FROM accounting.subledger_items WHERE book_id=NEW.book_id)
 AND NOT EXISTS(SELECT 1 FROM (SELECT account_id,aux_key,SUM(cents) AS difference FROM (
 SELECT account_id,aux_key,cents FROM accounting.c01_formal_buckets WHERE book_id=NEW.source_book_id UNION ALL
 SELECT account_id,aux_key,-cents FROM accounting.c01_opening_buckets WHERE book_id=NEW.book_id) balances GROUP BY account_id,aux_key) differences WHERE difference<>0)
 AND NOT EXISTS(SELECT 1 FROM accounting.bank_accounts a WHERE a.book_id IN(NEW.book_id,NEW.source_book_id) AND NOT EXISTS(SELECT 1 FROM accounting.c01_approved_banks b WHERE b.bank_account_id=a.id))
 AND NOT EXISTS(SELECT stable_bank_id,ledger_account_id,aux_key FROM accounting.c01_approved_banks WHERE book_id=NEW.source_book_id EXCEPT SELECT stable_bank_id,ledger_account_id,aux_key FROM accounting.c01_approved_banks WHERE book_id=NEW.book_id)
 AND NOT EXISTS(SELECT stable_bank_id,ledger_account_id,aux_key FROM accounting.c01_approved_banks WHERE book_id=NEW.book_id EXCEPT SELECT stable_bank_id,ledger_account_id,aux_key FROM accounting.c01_approved_banks WHERE book_id=NEW.source_book_id)
 AND NOT EXISTS(SELECT 1 FROM accounting.bank_statement_lines WHERE book_id=NEW.source_book_id AND (bank_account_id IS NULL OR status<>'matched'))
 AND NOT EXISTS(SELECT 1 FROM accounting.bank_recon_adjustments a WHERE a.book_id=NEW.source_book_id AND a.amount_cents<>0 AND NOT EXISTS(SELECT 1 FROM accounting.bank_adjustment_resolutions r WHERE r.adjustment_id=a.id)))) INTO ok;
 ELSIF TG_TABLE_NAME='subledger_bridge_items' THEN SELECT (NEW.created_by=accounting.actor_user() AND NEW.firm_id=accounting.actor_firm() AND accounting.book_review_allowed(NEW.book_id) AND EXISTS(SELECT 1 FROM accounting.books b WHERE b.id=NEW.book_id AND b.company_id=NEW.company_id AND b.firm_id=NEW.firm_id) AND (EXISTS(SELECT 1 FROM accounting.subledger_period_bridges b JOIN accounting.subledger_items s ON s.id=NEW.source_item_id AND s.book_id=b.source_book_id
 JOIN accounting.subledger_items t ON t.id=NEW.target_item_id AND t.book_id=b.book_id
 JOIN accounting.c01_item_residuals r ON r.id=s.id WHERE b.id=NEW.bridge_id AND b.book_id=NEW.book_id AND b.created_by=NEW.created_by
 AND NEW.remaining_cents=r.cents AND t.amount_cents=r.cents AND t.kind=s.kind AND t.counterparty_id=s.counterparty_id AND t.control_account_id=s.control_account_id
 AND t.control_account_version=s.control_account_version AND t.original_date=s.original_date AND (t.due_date IS NOT DISTINCT FROM s.due_date) AND (t.aux_json)::jsonb::text=(s.aux_json)::jsonb::text
 AND t.source_kind='opening' AND t.source_id=b.id AND t.source_version=1 AND t.created_by=NEW.created_by AND t.status='open'
 AND NOT EXISTS(SELECT 1 FROM accounting.subledger_bridge_completions c WHERE c.bridge_id=b.id)))) INTO ok;
 ELSIF TG_TABLE_NAME='subledger_bridge_completions' THEN SELECT (NEW.created_by=accounting.actor_user() AND NEW.firm_id=accounting.actor_firm() AND accounting.book_review_allowed(NEW.book_id) AND EXISTS(SELECT 1 FROM accounting.books b WHERE b.id=NEW.book_id AND b.company_id=NEW.company_id AND b.firm_id=NEW.firm_id) AND (EXISTS(SELECT 1 FROM accounting.subledger_period_bridges b WHERE b.id=NEW.bridge_id AND b.book_id=NEW.book_id AND b.created_by=NEW.created_by
 AND NOT EXISTS(SELECT 1 FROM accounting.c01_item_residuals r WHERE r.book_id=b.source_book_id AND (r.cents<0 OR (r.cents>0 AND NOT EXISTS(SELECT 1 FROM accounting.subledger_bridge_items i WHERE i.bridge_id=b.id AND i.source_item_id=r.id AND i.remaining_cents=r.cents))))
 AND (SELECT COUNT(*) FROM accounting.subledger_items WHERE book_id=b.book_id)=(SELECT COUNT(*) FROM accounting.subledger_bridge_items WHERE bridge_id=b.id)))) INTO ok;
 END IF; IF ok IS DISTINCT FROM TRUE THEN RAISE EXCEPTION 'Reviewed C01 identity or complete exact bridge required' USING ERRCODE='23514'; END IF; RETURN NEW; END $$;
REVOKE ALL ON FUNCTION accounting.c01_bridge_guard() FROM PUBLIC,accounting_app,accounting_worker,accounting_system;
ALTER TABLE accounting.ledger_bank_identities ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.ledger_bank_identities FORCE ROW LEVEL SECURITY;
REVOKE ALL ON accounting.ledger_bank_identities FROM PUBLIC,accounting_app,accounting_worker,accounting_system,accounting_projection;
GRANT SELECT,INSERT ON accounting.ledger_bank_identities TO accounting_app;
CREATE POLICY c01_bridge_read ON accounting.ledger_bank_identities FOR SELECT TO accounting_app USING(accounting.book_allowed(book_id));
CREATE POLICY c01_bridge_insert ON accounting.ledger_bank_identities FOR INSERT TO accounting_app WITH CHECK(accounting.book_review_allowed(book_id) AND created_by=accounting.actor_user() AND firm_id=accounting.actor_firm());
CREATE TRIGGER c01_bridge_guard BEFORE INSERT ON accounting.ledger_bank_identities FOR EACH ROW EXECUTE FUNCTION accounting.c01_bridge_guard();
CREATE TRIGGER c01_bridge_company BEFORE INSERT ON accounting.ledger_bank_identities FOR EACH ROW EXECUTE FUNCTION accounting.company_write_table_guard();
CREATE TRIGGER c01_bridge_closed BEFORE INSERT ON accounting.ledger_bank_identities FOR EACH ROW EXECUTE FUNCTION accounting.close_dependency_write_guard('book');
CREATE TRIGGER c01_bridge_revision AFTER INSERT ON accounting.ledger_bank_identities FOR EACH ROW EXECUTE FUNCTION accounting.close_dependency_bump('book');
CREATE TRIGGER c01_bridge_immutable BEFORE UPDATE OR DELETE ON accounting.ledger_bank_identities FOR EACH ROW EXECUTE FUNCTION accounting.delivery_immutable();
ALTER TABLE accounting.bank_identity_bindings ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.bank_identity_bindings FORCE ROW LEVEL SECURITY;
REVOKE ALL ON accounting.bank_identity_bindings FROM PUBLIC,accounting_app,accounting_worker,accounting_system,accounting_projection;
GRANT SELECT,INSERT ON accounting.bank_identity_bindings TO accounting_app;
CREATE POLICY c01_bridge_read ON accounting.bank_identity_bindings FOR SELECT TO accounting_app USING(accounting.book_allowed(book_id));
CREATE POLICY c01_bridge_insert ON accounting.bank_identity_bindings FOR INSERT TO accounting_app WITH CHECK(accounting.book_review_allowed(book_id) AND created_by=accounting.actor_user() AND firm_id=accounting.actor_firm());
CREATE TRIGGER c01_bridge_guard BEFORE INSERT ON accounting.bank_identity_bindings FOR EACH ROW EXECUTE FUNCTION accounting.c01_bridge_guard();
CREATE TRIGGER c01_bridge_company BEFORE INSERT ON accounting.bank_identity_bindings FOR EACH ROW EXECUTE FUNCTION accounting.company_write_table_guard();
CREATE TRIGGER c01_bridge_closed BEFORE INSERT ON accounting.bank_identity_bindings FOR EACH ROW EXECUTE FUNCTION accounting.close_dependency_write_guard('book');
CREATE TRIGGER c01_bridge_revision AFTER INSERT ON accounting.bank_identity_bindings FOR EACH ROW EXECUTE FUNCTION accounting.close_dependency_bump('book');
CREATE TRIGGER c01_bridge_immutable BEFORE UPDATE OR DELETE ON accounting.bank_identity_bindings FOR EACH ROW EXECUTE FUNCTION accounting.delivery_immutable();
ALTER TABLE accounting.bank_identity_reviews ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.bank_identity_reviews FORCE ROW LEVEL SECURITY;
REVOKE ALL ON accounting.bank_identity_reviews FROM PUBLIC,accounting_app,accounting_worker,accounting_system,accounting_projection;
GRANT SELECT,INSERT ON accounting.bank_identity_reviews TO accounting_app;
CREATE POLICY c01_bridge_read ON accounting.bank_identity_reviews FOR SELECT TO accounting_app USING(accounting.book_allowed(book_id));
CREATE POLICY c01_bridge_insert ON accounting.bank_identity_reviews FOR INSERT TO accounting_app WITH CHECK(accounting.book_review_allowed(book_id) AND created_by=accounting.actor_user() AND firm_id=accounting.actor_firm());
CREATE TRIGGER c01_bridge_guard BEFORE INSERT ON accounting.bank_identity_reviews FOR EACH ROW EXECUTE FUNCTION accounting.c01_bridge_guard();
CREATE TRIGGER c01_bridge_company BEFORE INSERT ON accounting.bank_identity_reviews FOR EACH ROW EXECUTE FUNCTION accounting.company_write_table_guard();
CREATE TRIGGER c01_bridge_closed BEFORE INSERT ON accounting.bank_identity_reviews FOR EACH ROW EXECUTE FUNCTION accounting.close_dependency_write_guard('book');
CREATE TRIGGER c01_bridge_revision AFTER INSERT ON accounting.bank_identity_reviews FOR EACH ROW EXECUTE FUNCTION accounting.close_dependency_bump('book');
CREATE TRIGGER c01_bridge_immutable BEFORE UPDATE OR DELETE ON accounting.bank_identity_reviews FOR EACH ROW EXECUTE FUNCTION accounting.delivery_immutable();
ALTER TABLE accounting.subledger_period_bridges ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.subledger_period_bridges FORCE ROW LEVEL SECURITY;
REVOKE ALL ON accounting.subledger_period_bridges FROM PUBLIC,accounting_app,accounting_worker,accounting_system,accounting_projection;
GRANT SELECT,INSERT ON accounting.subledger_period_bridges TO accounting_app;
CREATE POLICY c01_bridge_read ON accounting.subledger_period_bridges FOR SELECT TO accounting_app USING(accounting.book_allowed(book_id));
CREATE POLICY c01_bridge_insert ON accounting.subledger_period_bridges FOR INSERT TO accounting_app WITH CHECK(accounting.book_review_allowed(book_id) AND created_by=accounting.actor_user() AND firm_id=accounting.actor_firm());
CREATE TRIGGER c01_bridge_guard BEFORE INSERT ON accounting.subledger_period_bridges FOR EACH ROW EXECUTE FUNCTION accounting.c01_bridge_guard();
CREATE TRIGGER c01_bridge_company BEFORE INSERT ON accounting.subledger_period_bridges FOR EACH ROW EXECUTE FUNCTION accounting.company_write_table_guard();
CREATE TRIGGER c01_bridge_closed BEFORE INSERT ON accounting.subledger_period_bridges FOR EACH ROW EXECUTE FUNCTION accounting.close_dependency_write_guard('book');
CREATE TRIGGER c01_bridge_revision AFTER INSERT ON accounting.subledger_period_bridges FOR EACH ROW EXECUTE FUNCTION accounting.close_dependency_bump('book');
CREATE TRIGGER c01_bridge_immutable BEFORE UPDATE OR DELETE ON accounting.subledger_period_bridges FOR EACH ROW EXECUTE FUNCTION accounting.delivery_immutable();
ALTER TABLE accounting.subledger_bridge_items ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.subledger_bridge_items FORCE ROW LEVEL SECURITY;
REVOKE ALL ON accounting.subledger_bridge_items FROM PUBLIC,accounting_app,accounting_worker,accounting_system,accounting_projection;
GRANT SELECT,INSERT ON accounting.subledger_bridge_items TO accounting_app;
CREATE POLICY c01_bridge_read ON accounting.subledger_bridge_items FOR SELECT TO accounting_app USING(accounting.book_allowed(book_id));
CREATE POLICY c01_bridge_insert ON accounting.subledger_bridge_items FOR INSERT TO accounting_app WITH CHECK(accounting.book_review_allowed(book_id) AND created_by=accounting.actor_user() AND firm_id=accounting.actor_firm());
CREATE TRIGGER c01_bridge_guard BEFORE INSERT ON accounting.subledger_bridge_items FOR EACH ROW EXECUTE FUNCTION accounting.c01_bridge_guard();
CREATE TRIGGER c01_bridge_company BEFORE INSERT ON accounting.subledger_bridge_items FOR EACH ROW EXECUTE FUNCTION accounting.company_write_table_guard();
CREATE TRIGGER c01_bridge_closed BEFORE INSERT ON accounting.subledger_bridge_items FOR EACH ROW EXECUTE FUNCTION accounting.close_dependency_write_guard('book');
CREATE TRIGGER c01_bridge_revision AFTER INSERT ON accounting.subledger_bridge_items FOR EACH ROW EXECUTE FUNCTION accounting.close_dependency_bump('book');
CREATE TRIGGER c01_bridge_immutable BEFORE UPDATE OR DELETE ON accounting.subledger_bridge_items FOR EACH ROW EXECUTE FUNCTION accounting.delivery_immutable();
ALTER TABLE accounting.subledger_bridge_completions ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.subledger_bridge_completions FORCE ROW LEVEL SECURITY;
REVOKE ALL ON accounting.subledger_bridge_completions FROM PUBLIC,accounting_app,accounting_worker,accounting_system,accounting_projection;
GRANT SELECT,INSERT ON accounting.subledger_bridge_completions TO accounting_app;
CREATE POLICY c01_bridge_read ON accounting.subledger_bridge_completions FOR SELECT TO accounting_app USING(accounting.book_allowed(book_id));
CREATE POLICY c01_bridge_insert ON accounting.subledger_bridge_completions FOR INSERT TO accounting_app WITH CHECK(accounting.book_review_allowed(book_id) AND created_by=accounting.actor_user() AND firm_id=accounting.actor_firm());
CREATE TRIGGER c01_bridge_guard BEFORE INSERT ON accounting.subledger_bridge_completions FOR EACH ROW EXECUTE FUNCTION accounting.c01_bridge_guard();
CREATE TRIGGER c01_bridge_company BEFORE INSERT ON accounting.subledger_bridge_completions FOR EACH ROW EXECUTE FUNCTION accounting.company_write_table_guard();
CREATE TRIGGER c01_bridge_closed BEFORE INSERT ON accounting.subledger_bridge_completions FOR EACH ROW EXECUTE FUNCTION accounting.close_dependency_write_guard('book');
CREATE TRIGGER c01_bridge_revision AFTER INSERT ON accounting.subledger_bridge_completions FOR EACH ROW EXECUTE FUNCTION accounting.close_dependency_bump('book');
CREATE TRIGGER c01_bridge_immutable BEFORE UPDATE OR DELETE ON accounting.subledger_bridge_completions FOR EACH ROW EXECUTE FUNCTION accounting.delivery_immutable();
