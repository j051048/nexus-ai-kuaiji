"""Run the bounded assistant paths against actual FORCE RLS and native guards."""

import pytest

from app.accounting.domain import AccountingError
from app.assistant.models import ConversationCreate
from test_business_postgres import ADMIN_URL, APP_URL, business_case, business_postgres  # noqa: F401
from test_intelligence_workflow_postgres import pg_ai  # noqa: F401
from test_stage20_assistant_reads import (
    service_and_thread,
    test_detail_round_trips_preserve_current_facts_and_evidence as detail_checks,
    test_plan_history_round_trips_do_not_grow_with_unexecuted_plans as plan_checks,
)

pytestmark = pytest.mark.skipif(
    not ADMIN_URL or not APP_URL, reason="需要专用 PostgreSQL 管理员及受限运行测试连接"
)


def test_pg_detail_current_evidence_and_plan_page_remain_rls_scoped(pg_ai):
    detail_checks(pg_ai)


def test_pg_plan_page_batches_receipts_and_uses_one_database_clock(pg_ai):
    plan_checks(pg_ai)


def test_pg_batched_refs_cannot_cross_company_or_survive_revocation(pg_ai):
    c = pg_ai
    service, thread = service_and_thread(c, books=[c.book["id"]])
    assert service.detail(c.actors["accountant"], thread["id"])["context"]["books"]
    with pytest.raises(AccountingError) as wrong_company:
        service.create(
            c.manager,
            ConversationCreate(
                company_id=c.company["id"],
                book_ids=[c.other_book["id"]],
                idempotency_key="stage20-invalid-company-book",
            ),
        )
    assert wrong_company.value.status == 404
    with c.psycopg.connect(ADMIN_URL) as conn:
        conn.execute(
            "DELETE FROM accounting.book_access WHERE book_id=%s AND user_id=%s",
            (c.book["id"], c.actors["accountant"].user_id),
        )
    for read in (service.detail, service.messages):
        with pytest.raises(AccountingError) as denied:
            read(c.actors["accountant"], thread["id"])
        assert denied.value.status == 404
