ALTER TABLE fee_receivable_allocations ADD COLUMN reversed_at TEXT;
ALTER TABLE fee_receivable_allocations ADD COLUMN reversed_by TEXT;
ALTER TABLE fee_receivable_allocations ADD COLUMN reversal_reason TEXT;
