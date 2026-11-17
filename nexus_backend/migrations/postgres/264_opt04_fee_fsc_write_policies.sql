-- OPT04-A: read visibility must not be the write capability.  These tables
-- previously used one ALL policy with company_visible in WITH CHECK.

DROP POLICY fee_receivable_items_scope ON accounting.fee_receivable_items;
CREATE POLICY fee_items_read ON accounting.fee_receivable_items
  FOR SELECT TO PUBLIC
  USING (EXISTS (
    SELECT 1 FROM accounting.books b
    WHERE b.id = fee_receivable_items.book_id
      AND accounting.company_visible(b.company_id)
  ));
CREATE POLICY fee_items_insert ON accounting.fee_receivable_items
  FOR INSERT TO PUBLIC
  WITH CHECK (EXISTS (
    SELECT 1 FROM accounting.books b
    WHERE b.id = fee_receivable_items.book_id
      AND accounting.company_visible(b.company_id)
  ) AND accounting.ai_book_reviewer(book_id));
CREATE POLICY fee_items_update ON accounting.fee_receivable_items
  FOR UPDATE TO PUBLIC
  USING (EXISTS (
    SELECT 1 FROM accounting.books b
    WHERE b.id = fee_receivable_items.book_id
      AND accounting.company_visible(b.company_id)
  ) AND accounting.ai_book_reviewer(book_id))
  WITH CHECK (EXISTS (
    SELECT 1 FROM accounting.books b
    WHERE b.id = fee_receivable_items.book_id
      AND accounting.company_visible(b.company_id)
  ) AND accounting.ai_book_reviewer(book_id));

DROP POLICY fee_allocation_company ON accounting.fee_receivable_allocations;
CREATE POLICY fee_allocation_read ON accounting.fee_receivable_allocations
  FOR SELECT TO PUBLIC USING (accounting.company_visible(company_id));
CREATE POLICY fee_allocation_insert ON accounting.fee_receivable_allocations
  FOR INSERT TO PUBLIC
  WITH CHECK (accounting.company_visible(company_id) AND accounting.ai_book_reviewer(book_id));
CREATE POLICY fee_allocation_update ON accounting.fee_receivable_allocations
  FOR UPDATE TO PUBLIC
  USING (accounting.company_visible(company_id) AND accounting.ai_book_reviewer(book_id))
  WITH CHECK (accounting.company_visible(company_id) AND accounting.ai_book_reviewer(book_id));

DROP POLICY fee_refund_company ON accounting.fee_receivable_refunds;
CREATE POLICY fee_refund_read ON accounting.fee_receivable_refunds
  FOR SELECT TO PUBLIC USING (accounting.company_visible(company_id));
CREATE POLICY fee_refund_insert ON accounting.fee_receivable_refunds
  FOR INSERT TO PUBLIC
  WITH CHECK (accounting.company_visible(company_id) AND accounting.ai_book_reviewer(book_id));
CREATE POLICY fee_refund_update ON accounting.fee_receivable_refunds
  FOR UPDATE TO PUBLIC
  USING (accounting.company_visible(company_id) AND accounting.ai_book_reviewer(book_id))
  WITH CHECK (accounting.company_visible(company_id) AND accounting.ai_book_reviewer(book_id));

DROP POLICY fsc_company_profiles_company ON accounting.fsc_company_profiles;
CREATE POLICY fsc_profiles_read ON accounting.fsc_company_profiles
  FOR SELECT TO PUBLIC
  USING (accounting.company_visible(company_id) AND firm_id = accounting.actor_firm());
CREATE POLICY fsc_profiles_insert ON accounting.fsc_company_profiles
  FOR INSERT TO PUBLIC
  WITH CHECK (accounting.company_visible(company_id)
    AND firm_id = accounting.actor_firm()
    AND accounting.company_manager(company_id));
CREATE POLICY fsc_profiles_update ON accounting.fsc_company_profiles
  FOR UPDATE TO PUBLIC
  USING (accounting.company_visible(company_id)
    AND firm_id = accounting.actor_firm()
    AND accounting.company_manager(company_id))
  WITH CHECK (accounting.company_visible(company_id)
    AND firm_id = accounting.actor_firm()
    AND accounting.company_manager(company_id));
