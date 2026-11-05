"""Restricted PG role evidence for message privacy and early preparation."""

from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest

from app.accounting.directory import DirectoryService
from app.accounting.domain import AccountingError
from app.business.service import BusinessService
from app.firm_tasks.service import FirmTasksService
from period_fixtures import restore_period_state
from test_business_postgres import business_case, business_postgres, pytestmark


def add_message(c, recipient=None, company=None):
    identifier = uuid4().hex
    with c.db.transaction(write=True, actor=c.owner) as conn:
        conn.execute(
            "INSERT INTO client_messages(id,firm_id,company_id,recipient_user_id,kind,subject,body,created_by,created_at) "
            "VALUES(?,?,?,?,'return','虚构补件','仅本地验收',?,'2026-11-03T00:00:00+00:00')",
            (identifier, c.owner.firm_id, company or c.company, recipient, c.owner.user_id),
        )
    return identifier


def test_pg_message_recipient_receipts_and_native_sql_boundaries(business_case):
    c = business_case
    service = FirmTasksService(c.db, BusinessService(c.db))
    own, peer, other = (
        add_message(c, c.employee.user_id),
        add_message(c, c.colleague.user_id),
        add_message(c, company=c.other_company),
    )
    with c.db.transaction(actor=c.employee) as conn:
        assert {r["id"] for r in conn.execute("SELECT id FROM client_messages")} == {own}
    for identifier in (peer, other):
        with pytest.raises(AccountingError):
            service.mark_message_read(c.employee, identifier)
    service.mark_message_read(c.employee, own)
    assert service.messages(c.employee)["unread"] == 0
    with pytest.raises(c.psycopg.Error):
        with c.db.transaction(write=True, actor=c.employee) as conn:
            conn.execute(
                "INSERT INTO client_message_reads VALUES(?,?,?,?,?)",
                (peer, c.owner.firm_id, c.company, c.employee.user_id, "forged"),
            )
    with c.db.transaction(write=True, actor=c.employee) as conn:
        assert (
            conn.execute("UPDATE client_messages SET body='forged' WHERE id=?", (own,)).rowcount
            == 0
        )


def test_pg_broadcast_concurrent_reads_and_revocation(business_case):
    c = business_case
    service = FirmTasksService(c.db, BusinessService(c.db))
    identifier = add_message(c)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(lambda _: service.mark_message_read(c.employee, identifier), range(2))
        )
    assert all(row["read"] for row in results)
    assert service.messages(c.employee)["unread"] == 0
    assert service.messages(c.colleague)["unread"] == 1
    with c.db.transaction(actor=c.employee) as conn:
        assert (
            conn.execute(
                "SELECT count(*) FROM client_message_reads WHERE message_id=?", (identifier,)
            ).fetchone()[0]
            == 1
        )
    DirectoryService(c.db).revoke_company_member(c.owner, c.company, c.employee.user_id)
    assert service.messages(c.employee)["messages"] == []
    with c.db.transaction(actor=c.employee) as conn:
        assert conn.execute("SELECT * FROM client_message_reads").fetchall() == []
    with pytest.raises(AccountingError):
        service.mark_message_read(c.employee, identifier)


def test_pg_preparation_employee_projection_and_revalidation(business_case):
    c = business_case
    service = BusinessService(c.db)
    assert service.preparation(c.employee, c.company, "2026-10-03")["status"] == "ready"
    assert service.preparation(c.employee, c.company, "2026-11-03")["status"] == "period_missing"
    with c.db.transaction(actor=c.employee) as conn:
        assert conn.execute("SELECT id FROM books").fetchall() == []
    with pytest.raises(c.psycopg.Error):
        with c.db.transaction(actor=c.employee) as conn:
            conn.execute(
                "SELECT accounting.business_preparation_status(?,?)", (c.other_company, "2026-10")
            )
    assert service.preparation(c.finance, c.company, "2026-10-03")["status"] == "ready"
    DirectoryService(c.db).revoke_book_member(c.owner, c.book, c.finance.user_id)
    assert (
        service.preparation(c.finance, c.company, "2026-10-03")["status"] == "assignment_required"
    )
    restore_period_state(c.db, c.book, "closed")
    assert service.preparation(c.employee, c.company, "2026-10-03")["status"] == "period_locked"
    DirectoryService(c.db).revoke_company_member(c.owner, c.company, c.employee.user_id)
    with pytest.raises(AccountingError):
        service.preparation(c.employee, c.company, "2026-10-03")
