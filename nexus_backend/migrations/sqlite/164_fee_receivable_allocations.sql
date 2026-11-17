CREATE TABLE fee_receivable_allocations(
 id TEXT PRIMARY KEY,
 book_id TEXT NOT NULL,
 firm_id TEXT NOT NULL,
 company_id TEXT NOT NULL,
 receivable_id TEXT NOT NULL,
 bank_source_id TEXT NOT NULL,
 amount_cents INTEGER NOT NULL CHECK(amount_cents>0),
 bank_draft_version INTEGER NOT NULL CHECK(bank_draft_version>0),
 bank_amount_cents INTEGER NOT NULL CHECK(bank_amount_cents>0),
 created_by TEXT NOT NULL,
 created_at TEXT NOT NULL,
 idempotency_key TEXT NOT NULL,
 request_fingerprint TEXT NOT NULL,
 UNIQUE(id,book_id),
 UNIQUE(created_by,idempotency_key),
 FOREIGN KEY(bank_source_id,book_id) REFERENCES sources(id,book_id)
);
CREATE INDEX idx_fee_allocation_receivable ON fee_receivable_allocations(receivable_id,book_id);
CREATE INDEX idx_fee_allocation_bank ON fee_receivable_allocations(bank_source_id,book_id);
