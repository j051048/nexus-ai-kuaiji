"""Authorization predicates are applied before totals and pagination."""
from app.jobs.visibility import event_scope


def book_read_scope(actor, alias="b"):
    sql = f"""({alias}.firm_id=? AND EXISTS(SELECT 1 FROM firm_clients fc WHERE fc.firm_id={alias}.firm_id AND fc.company_id={alias}.company_id AND fc.status='active') AND (
      EXISTS(SELECT 1 FROM memberships fm WHERE fm.firm_id={alias}.firm_id AND fm.user_id=? AND fm.role IN ('owner','admin'))
      OR EXISTS(SELECT 1 FROM book_access ba WHERE ba.book_id={alias}.id AND ba.firm_id={alias}.firm_id AND ba.user_id=? AND (
       EXISTS(SELECT 1 FROM memberships fm WHERE fm.firm_id={alias}.firm_id AND fm.user_id=?)
       OR EXISTS(SELECT 1 FROM company_memberships cm WHERE cm.company_id={alias}.company_id AND cm.user_id=? AND cm.role IN ('owner','admin','finance'))))))"""
    return sql, [actor.firm_id, *([actor.user_id] * 4)]


def conversation_scope(db, actor, alias="c"):
    if db.is_postgres:
        return f"accounting.assistant_conversation_allowed({alias}.id)", []
    # Every reference remains visible. A single revoked reference hides the whole
    # thread, including questions and earlier answers that quote that reference.
    books, book_args = book_read_scope(actor)
    events, event_args = event_scope(actor)
    sql = f"""{alias}.firm_id=? AND {alias}.created_by=? AND EXISTS(SELECT 1 FROM users u WHERE u.id=? AND u.active=1)
      AND EXISTS(SELECT 1 FROM firm_clients fc WHERE fc.firm_id={alias}.firm_id AND fc.company_id={alias}.company_id AND fc.status='active')
      AND (EXISTS(SELECT 1 FROM memberships fm WHERE fm.firm_id={alias}.firm_id AND fm.user_id=? AND fm.role IN ('owner','admin'))
        OR EXISTS(SELECT 1 FROM company_memberships cm WHERE cm.company_id={alias}.company_id AND cm.user_id=?)
        OR (EXISTS(SELECT 1 FROM memberships fm WHERE fm.firm_id={alias}.firm_id AND fm.user_id=?) AND EXISTS(SELECT 1 FROM book_access ba JOIN books ab ON ab.id=ba.book_id AND ab.firm_id=ba.firm_id WHERE ab.company_id={alias}.company_id AND ab.firm_id={alias}.firm_id AND ba.user_id=?)))
      AND NOT EXISTS(SELECT 1 FROM json_each({alias}.book_ids_json) ref WHERE NOT EXISTS(SELECT 1 FROM books b WHERE b.id=ref.value AND b.company_id={alias}.company_id AND {books}))
      AND NOT EXISTS(SELECT 1 FROM json_each({alias}.event_ids_json) ref WHERE NOT EXISTS(SELECT 1 FROM business_events e WHERE e.id=ref.value AND e.company_id={alias}.company_id AND {events}))
      AND ({alias}.draft_id IS NULL OR EXISTS(SELECT 1 FROM drafts d JOIN json_each({alias}.book_ids_json) ref ON d.book_id=ref.value WHERE d.id={alias}.draft_id))"""
    return sql, [actor.firm_id, *([actor.user_id] * 6), *book_args, *event_args]
