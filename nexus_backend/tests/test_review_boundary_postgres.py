"""Database-level review boundary tests on the restricted PostgreSQL role.

These tests intentionally bypass the Python service. They prove that a direct
SQL writer cannot turn an unbalanced draft or a draft in a closed period into an
approved/exported record. They require the dedicated test DSNs supplied by CI.
"""

import pytest

from test_business_postgres import ADMIN_URL, APP_URL, business_case, full_event, handoff

pytestmark = pytest.mark.skipif(
    not ADMIN_URL or not APP_URL, reason="需要专用 PostgreSQL 管理员和受限运行测试连接"
)


def test_direct_sql_unbalanced_approval_is_rejected(business_case):
    case = business_case
    event_id = full_event(case)
    _, draft_id = handoff(case, event_id)
    with case.psycopg.connect(ADMIN_URL) as conn:
        conn.execute(
            "INSERT INTO accounting.draft_lines(draft_id,book_id,ordinal,account_code,"
            "debit_cents,credit_cents) VALUES(%s,%s,99,'1002',1,0)",
            (draft_id, case.book),
        )
    with pytest.raises(case.psycopg.Error) as rejected:
        with case.db.transaction(write=True, actor=case.owner) as conn:
            conn.execute(
                "UPDATE accounting.drafts SET status='approved',approved_by=%s,"
                "approved_at='2026-10-18T00:00:00+00:00' WHERE id=%s",
                (case.owner.user_id, draft_id),
            )
    assert rejected.value.sqlstate == "23514"
    with case.db.transaction(actor=case.owner) as conn:
        assert (
            conn.execute("SELECT status FROM accounting.drafts WHERE id=%s", (draft_id,)).fetchone()[0]
            == "draft"
        )


def test_direct_sql_closed_period_approval_is_rejected(business_case):
    case = business_case
    event_id = full_event(case)
    _, draft_id = handoff(case, event_id)
    with case.psycopg.connect(ADMIN_URL) as conn:
        conn.execute(
            "UPDATE accounting.periods SET status='closed' WHERE book_id=%s", (case.book,)
        )
    with pytest.raises(case.psycopg.Error) as rejected:
        with case.db.transaction(write=True, actor=case.owner) as conn:
            conn.execute(
                "UPDATE accounting.drafts SET status='approved',approved_by=%s,"
                "approved_at='2026-10-18T00:00:00+00:00' WHERE id=%s",
                (case.owner.user_id, draft_id),
            )
    assert rejected.value.sqlstate == "23514"
    with case.db.transaction(actor=case.owner) as conn:
        assert (
            conn.execute("SELECT status FROM accounting.drafts WHERE id=%s", (draft_id,)).fetchone()[0]
            == "draft"
        )


def test_direct_sql_export_without_review_is_rejected(business_case):
    case = business_case
    event_id = full_event(case)
    _, draft_id = handoff(case, event_id)
    with pytest.raises(case.psycopg.Error) as rejected:
        with case.db.transaction(write=True, actor=case.owner) as conn:
            conn.execute(
                "UPDATE accounting.drafts SET status='exported',"
                "exported_at='2026-10-18T00:00:00+00:00' WHERE id=%s",
                (draft_id,),
            )
    assert rejected.value.sqlstate == "23514"


def test_direct_sql_cross_company_approval_cannot_see_or_change_draft(business_case):
    case = business_case
    event_id = full_event(case)
    _, draft_id = handoff(case, event_id)
    with case.db.transaction(write=True, actor=case.unassigned) as conn:
        changed = conn.execute(
            "UPDATE accounting.drafts SET status='approved',approved_by=%s,"
            "approved_at='2026-10-18T00:00:00+00:00' WHERE id=%s",
            (case.unassigned.user_id, draft_id),
        ).rowcount
    assert changed == 0
    with case.db.transaction(actor=case.owner) as conn:
        assert (
            conn.execute("SELECT status FROM accounting.drafts WHERE id=%s", (draft_id,)).fetchone()[0]
            == "draft"
        )
