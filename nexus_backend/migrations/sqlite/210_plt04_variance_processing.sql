-- PLT04-B: a handled variance can be claimed for investigation without
-- changing its pending accounting state (close remains blocked).
ALTER TABLE fee_receivable_variances ADD COLUMN processing_started_at TEXT;
ALTER TABLE fee_receivable_variances ADD COLUMN processing_started_by TEXT;
