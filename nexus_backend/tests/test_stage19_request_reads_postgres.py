"""Live RLS and revocation checks for the reduced request-read path."""

import pytest

from app.accounting.directory import DirectoryService
from app.accounting.domain import AccountingError
from app.business.service import BusinessService
from test_business_postgres import ADMIN_URL, APP_URL, business_case, business_postgres
from test_stage19_request_reads import count_statements


pytestmark = pytest.mark.skipif(
    not ADMIN_URL or not APP_URL, reason="需要独立本机 PostgreSQL 测试 DSN"
)


def test_workspace_reads_recheck_active_client_and_membership(business_case, monkeypatch):
    c = business_case
    directory = DirectoryService(c.db)
    for actor in (c.employee, c.owner, c.reviewer):
        with count_statements(c.db, monkeypatch) as statements:
            assert directory.resolve_actor(actor.user_id) == actor
        assert len(statements) == 1
        assert directory.me(actor.user_id)["current_firm_id"] == actor.firm_id
    with pytest.raises(AccountingError, match="工作空间授权"):
        directory.resolve_actor(c.employee.user_id, "other-firm")
    with c.psycopg.connect(ADMIN_URL) as conn:
        conn.execute(
            "DELETE FROM accounting.company_memberships WHERE user_id=%s", (c.employee.user_id,)
        )
    with pytest.raises(AccountingError, match="尚未获得"):
        directory.resolve_actor(c.employee.user_id)
    with c.psycopg.connect(ADMIN_URL) as conn:
        conn.execute("UPDATE accounting.users SET active=0 WHERE id=%s", (c.owner.user_id,))
    with pytest.raises(AccountingError, match="停用"):
        directory.resolve_actor(c.owner.user_id)


def test_context_batches_keep_rls_department_and_ledger_boundaries(business_case, monkeypatch):
    c = business_case
    service = BusinessService(c.db)
    with count_statements(c.db, monkeypatch) as statements:
        owner = service.context(c.owner)
    assert len(statements) <= 6
    assert {row["id"] for row in owner["companies"]} == {c.company, c.other_company}
    employee = service.context(c.employee)
    assert [row["id"] for row in employee["companies"]] == [c.company]
    assert {row["id"] for row in employee["companies"][0]["departments"]} == {
        c.department,
        c.other_department,
    }
    approver = service.context(c.approver)
    assert approver["companies"][0]["approver_department_ids"] == [c.department]
    reviewer = service.context(c.reviewer)
    assert [row["id"] for row in reviewer["companies"]] == [c.company]
    assert reviewer["companies"][0]["ledger_available"]
    assert reviewer["companies"][0]["departments"] == []
    assert service.context(c.unassigned)["companies"] == []
    with c.psycopg.connect(ADMIN_URL) as conn:
        conn.execute("DELETE FROM accounting.book_access WHERE user_id=%s", (c.reviewer.user_id,))
    assert service.context(c.reviewer)["companies"] == []
    with c.psycopg.connect(ADMIN_URL) as conn:
        conn.execute(
            "UPDATE accounting.firm_clients SET status='suspended' WHERE firm_id=%s AND company_id=%s",
            (c.owner.firm_id, c.company),
        )
    assert service.context(c.employee)["companies"] == []
