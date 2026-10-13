"""SQL scopes for pagination. PostgreSQL additionally enforces the same RLS."""


def book_edit_scope(actor, alias="b"):
    # AccessControl.permissions: managers, assigned firm editors, or assigned
    # company ledger members. A book assignment alone is never a principal.
    sql = f"""({alias}.firm_id=? AND EXISTS(
      SELECT 1 FROM firm_clients fc WHERE fc.firm_id={alias}.firm_id
      AND fc.company_id={alias}.company_id AND fc.status='active') AND (
      EXISTS(SELECT 1 FROM memberships fm WHERE fm.firm_id={alias}.firm_id AND fm.user_id=? AND fm.role IN ('owner','admin'))
      OR EXISTS(SELECT 1 FROM book_access ba WHERE ba.book_id={alias}.id AND ba.firm_id={alias}.firm_id AND ba.user_id=? AND ba.role IN ('editor','reviewer') AND (
        EXISTS(SELECT 1 FROM memberships fm WHERE fm.firm_id={alias}.firm_id AND fm.user_id=? AND fm.role IN ('owner','admin','operator','reviewer'))
        OR (NOT EXISTS(SELECT 1 FROM memberships fm WHERE fm.firm_id={alias}.firm_id AND fm.user_id=?)
          AND EXISTS(SELECT 1 FROM company_memberships cm WHERE cm.company_id={alias}.company_id AND cm.user_id=? AND cm.role IN ('owner','admin','finance')))
      ))))"""
    return sql, [actor.firm_id, *([actor.user_id] * 5)]


def event_scope(actor, alias="e"):
    sql = f"""({alias}.firm_id=? AND EXISTS(SELECT 1 FROM firm_clients fc
      WHERE fc.firm_id={alias}.firm_id AND fc.company_id={alias}.company_id AND fc.status='active') AND (
      EXISTS(SELECT 1 FROM company_memberships cm WHERE cm.company_id={alias}.company_id AND cm.user_id=? AND (
        {alias}.applicant_id=? OR cm.role='finance' OR EXISTS(SELECT 1 FROM department_approvers da
          WHERE da.firm_id={alias}.firm_id AND da.company_id={alias}.company_id AND da.department_id={alias}.department_id AND da.user_id=?)
      )) OR ({alias}.status='handed_off' AND EXISTS(
        SELECT 1 FROM business_mappings bm JOIN books b ON b.id=bm.book_id AND b.firm_id=bm.firm_id
        WHERE bm.event_id={alias}.id AND (
          EXISTS(SELECT 1 FROM memberships fm WHERE fm.firm_id=b.firm_id AND fm.user_id=? AND fm.role IN ('owner','admin'))
          OR EXISTS(SELECT 1 FROM book_access ba WHERE ba.book_id=b.id AND ba.firm_id=b.firm_id AND ba.user_id=? AND (
            EXISTS(SELECT 1 FROM memberships fm WHERE fm.firm_id=b.firm_id AND fm.user_id=?)
            OR EXISTS(SELECT 1 FROM company_memberships cm WHERE cm.company_id=b.company_id AND cm.user_id=? AND cm.role IN ('owner','admin','finance'))
          ))
        )
      ))))"""
    return sql, [actor.firm_id, *([actor.user_id] * 7)]
