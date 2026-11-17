-- OPT01-A: fee allocation/refund money must be able to exceed 32-bit cents.
ALTER TABLE accounting.fee_receivable_allocations
  ALTER COLUMN amount_cents TYPE BIGINT,
  ALTER COLUMN bank_amount_cents TYPE BIGINT;
ALTER TABLE accounting.fee_receivable_refunds
  ALTER COLUMN amount_cents TYPE BIGINT,
  ALTER COLUMN bank_amount_cents TYPE BIGINT;
