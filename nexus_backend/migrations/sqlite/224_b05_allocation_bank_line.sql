-- compatibility: incremental
-- Bind a fee receivable allocation to the exact bank statement line it settles.
ALTER TABLE fee_receivable_allocations
  ADD COLUMN bank_statement_line_id TEXT REFERENCES bank_statement_lines(id);
CREATE INDEX fee_alloc_bank_line_idx
  ON fee_receivable_allocations(bank_statement_line_id)
  WHERE bank_statement_line_id IS NOT NULL;
