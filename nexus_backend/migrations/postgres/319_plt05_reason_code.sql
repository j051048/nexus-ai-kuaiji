-- compatibility: incremental
-- PLT05-B: structured reason codes for bridge return row dispositions.
-- Historical rows are marked "legacy"; they are not back-filled with a code the
-- operator never chose.
ALTER TABLE accounting.bridge_return_row_handlers
  ADD COLUMN IF NOT EXISTS reason_code TEXT NOT NULL DEFAULT 'legacy';

ALTER TABLE accounting.bridge_return_row_handlers
  DROP CONSTRAINT IF EXISTS bridge_return_handlers_reason_code_check;

ALTER TABLE accounting.bridge_return_row_handlers
  ADD CONSTRAINT bridge_return_handlers_reason_code_check
  CHECK (reason_code IN (
    'amount_mismatch','account_mismatch','date_mismatch','voucher_missing','other','legacy'
  ));
