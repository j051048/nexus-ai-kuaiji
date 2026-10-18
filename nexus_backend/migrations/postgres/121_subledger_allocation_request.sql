-- compatibility: incremental
-- R03/F06: request-level idempotency for a multi-item allocation. Detail rows
-- carry a per-item suffix so the legacy table unique key remains valid.
CREATE TABLE IF NOT EXISTS accounting.subledger_allocation_requests(
 id TEXT PRIMARY KEY, firm_id TEXT NOT NULL REFERENCES accounting.firms(id),
 company_id TEXT NOT NULL REFERENCES accounting.companies(id),
 book_id TEXT NOT NULL REFERENCES accounting.books(id),
 voucher_id TEXT NOT NULL REFERENCES accounting.vouchers(id),
 idempotency_key TEXT NOT NULL, request_fingerprint TEXT NOT NULL,
 total_cents BIGINT NOT NULL CHECK(total_cents>0),
 created_by TEXT NOT NULL REFERENCES accounting.users(id), created_at TEXT NOT NULL,
 UNIQUE(firm_id,idempotency_key)
);
ALTER TABLE accounting.subledger_allocation_requests ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.subledger_allocation_requests FORCE ROW LEVEL SECURITY;
REVOKE ALL ON accounting.subledger_allocation_requests FROM PUBLIC,accounting_app,accounting_worker;
GRANT SELECT,INSERT ON accounting.subledger_allocation_requests TO accounting_app;
CREATE POLICY subledger_allocation_requests_read ON accounting.subledger_allocation_requests
 FOR SELECT TO accounting_app USING(accounting.book_allowed(book_id));
CREATE POLICY subledger_allocation_requests_insert ON accounting.subledger_allocation_requests
 FOR INSERT TO accounting_app WITH CHECK(accounting.book_allowed(book_id,true));
CREATE TRIGGER batch2_company_write_gate BEFORE INSERT OR UPDATE OR DELETE
 ON accounting.subledger_allocation_requests FOR EACH ROW
 EXECUTE FUNCTION accounting.company_write_table_guard();
CREATE TRIGGER batch2_statement_write_gate BEFORE INSERT OR UPDATE OR DELETE
 ON accounting.subledger_allocation_requests FOR EACH STATEMENT
 EXECUTE FUNCTION accounting.compatibility_statement_gate();
