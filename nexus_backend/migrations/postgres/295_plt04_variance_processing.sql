-- PLT04-B: claim metadata for a pending variance. The row remains pending
-- (and therefore close-blocking) until a configured reason is handled.
ALTER TABLE accounting.fee_receivable_variances ADD COLUMN processing_started_at TIMESTAMPTZ;
ALTER TABLE accounting.fee_receivable_variances ADD COLUMN processing_started_by TEXT;
