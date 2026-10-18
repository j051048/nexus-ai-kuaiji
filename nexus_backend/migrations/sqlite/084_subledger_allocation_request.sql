-- compatibility: incremental
-- R03/F06: request-level idempotency for multi-item allocations.
CREATE TABLE IF NOT EXISTS subledger_allocation_requests(
 id TEXT PRIMARY KEY, firm_id TEXT NOT NULL REFERENCES firms(id),
 company_id TEXT NOT NULL REFERENCES companies(id),
 book_id TEXT NOT NULL REFERENCES books(id),
 voucher_id TEXT NOT NULL REFERENCES vouchers(id),
 idempotency_key TEXT NOT NULL, request_fingerprint TEXT NOT NULL,
 total_cents INTEGER NOT NULL CHECK(total_cents>0),
 created_by TEXT NOT NULL REFERENCES users(id), created_at TEXT NOT NULL,
 UNIQUE(firm_id,idempotency_key)
);
