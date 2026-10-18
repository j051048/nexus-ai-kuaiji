-- compatibility: incremental
-- R03/F07: rebuild the allocation table without UNIQUE(item_id,voucher_id).
-- Reversals remain append-only; a reversed allocation may be re-allocated.
CREATE TABLE subledger_allocations_new(
 id TEXT PRIMARY KEY, firm_id TEXT NOT NULL, company_id TEXT NOT NULL, book_id TEXT NOT NULL,
 item_id TEXT NOT NULL REFERENCES subledger_items(id),
 voucher_id TEXT NOT NULL REFERENCES vouchers(id),
 amount_cents INTEGER NOT NULL CHECK(amount_cents>0), allocated_on TEXT NOT NULL,
 idempotency_key TEXT NOT NULL, request_fingerprint TEXT NOT NULL,
 created_by TEXT NOT NULL REFERENCES users(id), created_at TEXT NOT NULL,
 UNIQUE(firm_id,idempotency_key)
);
INSERT INTO subledger_allocations_new SELECT
 id,firm_id,company_id,book_id,item_id,voucher_id,amount_cents,allocated_on,
 idempotency_key,request_fingerprint,created_by,created_at FROM subledger_allocations;
DROP TABLE subledger_allocations;
ALTER TABLE subledger_allocations_new RENAME TO subledger_allocations;
