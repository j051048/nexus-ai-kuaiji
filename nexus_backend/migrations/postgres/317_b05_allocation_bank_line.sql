-- compatibility: incremental
-- Bind a fee receivable allocation to the exact bank statement line it settles,
-- so the chain voucher -> allocation -> bank line -> receipt is traceable.
ALTER TABLE accounting.fee_receivable_allocations
  ADD COLUMN IF NOT EXISTS bank_statement_line_id TEXT
  REFERENCES accounting.bank_statement_lines(id);

CREATE INDEX IF NOT EXISTS fee_alloc_bank_line_idx
  ON accounting.fee_receivable_allocations(bank_statement_line_id)
  WHERE bank_statement_line_id IS NOT NULL;
