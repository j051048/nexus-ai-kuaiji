"""Affiliation reads under the runtime role, including the pre-firm /me read."""

import pytest

from app.accounting.directory import DirectoryService
from app.accounting.domain import Actor, AccountingError
from test_business_postgres import ADMIN_URL, APP_URL, business_case, business_postgres  # noqa: F401

pytestmark = pytest.mark.skipif(not ADMIN_URL or not APP_URL, reason="isolated PG required")


def test_pg_identity_unit_read_binds_firm_and_restores_caller_scope(business_case):
    c = business_case
    directory = DirectoryService(c.db)
    with c.db.transaction(actor=Actor(c.employee.user_id, "")) as conn:
        value = directory.me_in_connection(conn, c.employee.user_id, c.owner.firm_id)
        assert [row["id"] for row in value["company_memberships"]] == [c.company]
        assert value["company_memberships"][0]["role"] == "employee"
        assert conn.execute("SELECT current_setting('app.firm_id',true)").fetchone()[0] == ""
    assert directory.me(c.owner.user_id, c.owner.firm_id)["company_memberships"] == []
    with pytest.raises(AccountingError):
        directory.me(c.employee.user_id, "unrelated-firm")


def test_pg_identity_unit_revocation_does_not_retain_previous_company(business_case):
    c = business_case
    directory = DirectoryService(c.db)
    assert [row["id"] for row in directory.me(c.employee.user_id)["company_memberships"]] == [
        c.company
    ]
    with c.psycopg.connect(ADMIN_URL) as conn:
        conn.execute(
            "DELETE FROM accounting.company_memberships WHERE company_id=%s AND user_id=%s",
            (c.company, c.employee.user_id),
        )
    assert directory.me(c.employee.user_id)["company_memberships"] == []
