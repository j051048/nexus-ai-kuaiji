-- compatibility: incremental
-- 银行流水落库：确认导入时把柜台/网银流水写成 bank_statement_lines，并用
-- external_ref 做「同一账套同一笔流水只落一次」的幂等键；source_id 指向同一次
-- 导入生成的正式来源行，供规费核销把「银行流水行」反查到「凭证来源」。
ALTER TABLE bank_statement_lines ADD COLUMN source_id TEXT REFERENCES sources(id);
ALTER TABLE bank_statement_lines ADD COLUMN external_ref TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS bank_statement_lines_external_ref
  ON bank_statement_lines(book_id, external_ref)
  WHERE external_ref IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS bank_statement_lines_source
  ON bank_statement_lines(book_id, source_id)
  WHERE source_id IS NOT NULL;
