-- PLT05-C 补强：回执原件与逐笔处置只能由本账套（book）可读/可写的人访问，
-- 不能因为同所、同企业而看到其他账套的回执内容。
SET LOCAL search_path=accounting,pg_catalog;

DROP POLICY IF EXISTS bridge_return_evidence_firm ON accounting.bridge_return_evidence;
DROP POLICY IF EXISTS bridge_return_evidence_book_read ON accounting.bridge_return_evidence;
DROP POLICY IF EXISTS bridge_return_evidence_book_write ON accounting.bridge_return_evidence;

CREATE POLICY bridge_return_evidence_book_read ON accounting.bridge_return_evidence
  FOR SELECT TO accounting_app
  USING (
    accounting.book_allowed(book_id)
    AND EXISTS (
      SELECT 1 FROM accounting.bridge_return_imports i
      WHERE i.id = import_id
        AND i.book_id = book_id
        AND i.company_id = company_id
        AND i.firm_id = accounting.actor_firm()
    )
  );

CREATE POLICY bridge_return_evidence_book_write ON accounting.bridge_return_evidence
  FOR INSERT TO accounting_app
  WITH CHECK (
    accounting.book_allowed(book_id, true)
    AND EXISTS (
      SELECT 1 FROM accounting.bridge_return_imports i
      WHERE i.id = import_id
        AND i.book_id = book_id
        AND i.company_id = company_id
        AND i.firm_id = accounting.actor_firm()
    )
  );

DROP POLICY IF EXISTS bridge_return_handlers_firm ON accounting.bridge_return_row_handlers;
DROP POLICY IF EXISTS bridge_return_handlers_book_read ON accounting.bridge_return_row_handlers;
DROP POLICY IF EXISTS bridge_return_handlers_book_write ON accounting.bridge_return_row_handlers;

CREATE POLICY bridge_return_handlers_book_read ON accounting.bridge_return_row_handlers
  FOR SELECT TO accounting_app
  USING (
    accounting.book_allowed(book_id)
    AND EXISTS (
      SELECT 1 FROM accounting.bridge_return_imports i
      WHERE i.id = import_id
        AND i.book_id = book_id
        AND i.company_id = company_id
        AND i.firm_id = accounting.actor_firm()
    )
  );

CREATE POLICY bridge_return_handlers_book_write ON accounting.bridge_return_row_handlers
  FOR INSERT TO accounting_app
  WITH CHECK (
    accounting.book_allowed(book_id, true)
    AND EXISTS (
      SELECT 1 FROM accounting.bridge_return_imports i
      WHERE i.id = import_id
        AND i.book_id = book_id
        AND i.company_id = company_id
        AND i.firm_id = accounting.actor_firm()
    )
  );
