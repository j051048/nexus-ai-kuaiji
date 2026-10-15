"""Bound assistant reads without weakening live authorization or frozen evidence."""

from contextlib import contextmanager
from uuid import uuid4

import pytest

from app.accounting.domain import AccountingError
from app.accounting.models import BookCreate, BookMemberGrant
from app.assistant.actions import ActionPrepare, AssistantActionService
from app.assistant.models import ConversationCreate
from app.assistant.service import AssistantService
from app.jobs.service import QueueService
from test_intelligence_workflow import ai_case, create_event, extract, handoff  # noqa: F401


@contextmanager
def statements(db):
    original = db.transaction
    queries = []

    class Connection:
        def __init__(self, delegate):
            self.delegate = delegate

        def execute(self, sql, args=()):
            queries.append(sql)
            return self.delegate.execute(sql, args)

        def __getattr__(self, key):
            return getattr(self.delegate, key)

    @contextmanager
    def transaction(*args, **kwargs):
        with original(*args, **kwargs) as conn:
            yield Connection(conn)

    db.transaction = transaction
    try:
        yield queries
    finally:
        db.transaction = original


def service_and_thread(case, *, events=(), books=(), draft_id=None, intelligence=None):
    service = AssistantService(case.db, case.ledger, case.business, intelligence=intelligence)
    thread = service.create(
        case.actors["accountant"],
        ConversationCreate(
            company_id=case.company["id"],
            book_ids=list(books),
            event_ids=list(events),
            draft_id=draft_id,
            idempotency_key=uuid4().hex,
        ),
    )["conversation"]
    return service, thread


def test_detail_round_trips_preserve_current_facts_and_evidence(ai_case):
    c = ai_case
    event = create_event(c)
    extract(c, event)
    handed = handoff(c, event)
    with c.db.transaction(actor=c.actors["accountant"]) as conn:
        mapping = conn.execute(
            "SELECT * FROM business_mappings WHERE event_id=?", (event["id"],)
        ).fetchone()
    service, thread = service_and_thread(
        c,
        events=[event["id"]],
        books=[c.book["id"]],
        draft_id=mapping["draft_id"],
        intelligence=c.intelligence,
    )
    with statements(c.db) as queries:
        detail = service.detail(c.actors["accountant"], thread["id"])
    context = detail["context"]
    assert context["events"][0]["documents"][0]["gross_amount"] == "0.30"
    assert context["draft"]["status"] == "draft"
    assert context["draft"]["id"] == mapping["draft_id"]
    assert context["events"][0]["status"] == "handed_off"
    assert len(context["evidence"]) == 4
    # Before this change the same fixture required 195 application SQL calls.
    # Keep dynamic assessment/draft guards instead of dropping their checks.
    assert len(queries) <= 115
    print("assistant_detail_queries", len(queries))
    assert handed


def test_plan_history_round_trips_do_not_grow_with_unexecuted_plans(ai_case):
    c = ai_case
    service, thread = service_and_thread(c, books=[c.book["id"]])
    queue = QueueService(c.db, c.intelligence)
    actions = AssistantActionService(c.db, service, queue, c.ledger)
    for _ in range(20):
        actions.prepare(
            c.actors["accountant"],
            thread["id"],
            ActionPrepare(
                kind="bank_drafts", reason="合成测试查询开销", idempotency_key=uuid4().hex
            ),
        )
    with statements(c.db) as queries:
        result = actions.list(c.actors["accountant"], thread["id"])
    assert len(result["plans"]) == 20
    assert all(plan["state"] == "empty" for plan in result["plans"])
    assert len(queries) <= 5 + int(c.db.is_postgres)
    print("assistant_plan_history_queries", len(queries))


def test_book_summaries_are_batched_across_explicit_scope(ai_case):
    c = ai_case
    service, small = service_and_thread(c, books=[c.book["id"]])
    with statements(c.db) as one_queries:
        one = service.detail(c.actors["accountant"], small["id"])
    books = [c.book["id"]]
    for index in range(11):
        book = c.ledger.create_book(
            c.manager,
            BookCreate(
                name=f"合成批量摘要账套 {index}", period="2026-10", company_id=c.company["id"]
            ),
        )
        c.directory.grant_book_member(
            c.manager, book["id"], c.actors["accountant"].user_id, BookMemberGrant(role="viewer")
        )
        books.append(book["id"])
    service, large = service_and_thread(c, books=books)
    with statements(c.db) as all_queries:
        all_books = service.detail(c.actors["accountant"], large["id"])
    assert len(all_queries) == len(one_queries)
    assert len(all_books["context"]["books"]) == 12
    assert (
        next(row for row in all_books["context"]["books"] if row["id"] == c.book["id"])
        == one["context"]["books"][0]
    )
    assert all(row["source_gross_total"] == "0.00" for row in all_books["context"]["books"])
    assert c.other_book["id"] not in str(all_books)


def test_wrong_company_book_scope_and_stopped_company_are_denied(ai_case):
    c = ai_case
    service, thread = service_and_thread(c, books=[c.book["id"]])
    with pytest.raises(AccountingError) as wrong_company:
        service.create(
            c.manager,
            ConversationCreate(
                company_id=c.company["id"],
                book_ids=[c.other_book["id"]],
                idempotency_key=uuid4().hex,
            ),
        )
    assert wrong_company.value.status == 404
    with c.db.transaction(write=True, actor=c.manager) as conn:
        conn.execute(
            "UPDATE firm_clients SET status='suspended' WHERE firm_id=? AND company_id=?",
            (c.manager.firm_id, c.company["id"]),
        )
    for read in (service.detail, service.messages):
        with pytest.raises(AccountingError) as denied:
            read(c.actors["accountant"], thread["id"])
        assert denied.value.status == 404


def test_warm_detail_cannot_survive_book_revocation(ai_case):
    c = ai_case
    service, thread = service_and_thread(c, books=[c.book["id"]])
    assert service.detail(c.actors["accountant"], thread["id"])["context"]["books"]
    with c.db.transaction(write=True, actor=c.manager) as conn:
        conn.execute(
            "DELETE FROM book_access WHERE book_id=? AND user_id=?",
            (c.book["id"], c.actors["accountant"].user_id),
        )
    for read in (service.detail, service.messages):
        with pytest.raises(AccountingError) as denied:
            read(c.actors["accountant"], thread["id"])
        assert denied.value.status == 404
