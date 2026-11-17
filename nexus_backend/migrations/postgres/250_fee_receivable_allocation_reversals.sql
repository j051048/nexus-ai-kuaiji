ALTER TABLE accounting.fee_receivable_allocations ADD COLUMN reversed_at timestamptz;
ALTER TABLE accounting.fee_receivable_allocations ADD COLUMN reversed_by text;
ALTER TABLE accounting.fee_receivable_allocations ADD COLUMN reversal_reason text;
