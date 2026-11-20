-- compatibility: incremental
-- 修复 304 迁移 RLS 子查询列遮蔽：显式限定外层表名，确保 import_id 对应的
-- book_id 与 company_id 与当前行严格一致，防止跨账套/跨企业错挂回执与处置。
SET LOCAL search_path=accounting,pg_catalog;

DROP POLICY IF EXISTS bridge_return_evidence_book_read ON accounting.bridge_return_evidence;
DROP POLICY IF EXISTS bridge_return_evidence_book_write ON accounting.bridge_return_evidence;

CREATE POLICY bridge_return_evidence_book_read ON accounting.bridge_return_evidence
  FOR SELECT TO accounting_app
  USING (
    accounting.book_allowed(bridge_return_evidence.book_id)
    AND EXISTS (
      SELECT 1 FROM accounting.bridge_return_imports i
      WHERE i.id = bridge_return_evidence.import_id
        AND i.book_id = bridge_return_evidence.book_id
        AND i.company_id = bridge_return_evidence.company_id
        AND i.firm_id = accounting.actor_firm()
    )
  );

CREATE POLICY bridge_return_evidence_book_write ON accounting.bridge_return_evidence
  FOR INSERT TO accounting_app
  WITH CHECK (
    accounting.book_allowed(bridge_return_evidence.book_id, true)
    AND EXISTS (
      SELECT 1 FROM accounting.bridge_return_imports i
      WHERE i.id = bridge_return_evidence.import_id
        AND i.book_id = bridge_return_evidence.book_id
        AND i.company_id = bridge_return_evidence.company_id
        AND i.firm_id = accounting.actor_firm()
    )
  );

DROP POLICY IF EXISTS bridge_return_handlers_book_read ON accounting.bridge_return_row_handlers;
DROP POLICY IF EXISTS bridge_return_handlers_book_write ON accounting.bridge_return_row_handlers;

CREATE POLICY bridge_return_handlers_book_read ON accounting.bridge_return_row_handlers
  FOR SELECT TO accounting_app
  USING (
    accounting.book_allowed(bridge_return_row_handlers.book_id)
    AND EXISTS (
      SELECT 1 FROM accounting.bridge_return_imports i
      WHERE i.id = bridge_return_row_handlers.import_id
        AND i.book_id = bridge_return_row_handlers.book_id
        AND i.company_id = bridge_return_row_handlers.company_id
        AND i.firm_id = accounting.actor_firm()
    )
  );

CREATE POLICY bridge_return_handlers_book_write ON accounting.bridge_return_row_handlers
  FOR INSERT TO accounting_app
  WITH CHECK (
    accounting.book_allowed(bridge_return_row_handlers.book_id, true)
    AND EXISTS (
      SELECT 1 FROM accounting.bridge_return_imports i
      WHERE i.id = bridge_return_row_handlers.import_id
        AND i.book_id = bridge_return_row_handlers.book_id
        AND i.company_id = bridge_return_row_handlers.company_id
        AND i.firm_id = accounting.actor_firm()
    )
  );
