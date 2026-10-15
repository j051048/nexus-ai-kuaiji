"""The home capability projection keeps PostgreSQL FORCE RLS and revocation."""

import pytest

from app.accounting.domain import AccountingError
from test_business_postgres import ADMIN_URL, APP_URL, business_case, business_postgres
from test_stage12_workspace_postgres import workspace
from test_stage19_request_reads import count_statements
from test_stage20_home import previous_profile

pytestmark = pytest.mark.skipif(
    not ADMIN_URL or not APP_URL, reason="需要独立本机 PostgreSQL 测试 DSN"
)


def test_home_profile_matches_existing_scopes_under_rls(business_case, monkeypatch):
    c, service = business_case, workspace(business_case)
    for actor in (c.owner, c.employee, c.approver, c.finance, c.reviewer, c.viewer, c.unassigned):
        expected = previous_profile(service, actor)
        with count_statements(c.db, monkeypatch) as statements:
            value = service.profile(actor)
        assert value == expected
        assert len(statements) == 1
        assert not any("draft_lines" in query or "FROM drafts" in query for query in statements)
    assert [row["id"] for row in service.profile(c.employee)["companies"]] == [c.company]
    assert service.profile(c.reviewer)["can_operations"] is False
    assert service.profile(c.finance)["can_operations"] is True


@pytest.mark.parametrize("change", ["membership", "book", "client", "company", "user"])
def test_home_profile_revocation_is_not_cached(business_case, change):
    c, service = business_case, workspace(business_case)
    actor = c.reviewer if change == "book" else c.finance
    assert service.profile(actor)["can_accounting"]
    with c.psycopg.connect(ADMIN_URL) as conn:
        if change == "membership":
            conn.execute(
                "DELETE FROM accounting.company_memberships WHERE user_id=%s", (actor.user_id,)
            )
        elif change == "book":
            conn.execute("DELETE FROM accounting.book_access WHERE user_id=%s", (actor.user_id,))
        elif change == "client":
            conn.execute(
                "UPDATE accounting.firm_clients SET status='suspended' WHERE company_id=%s",
                (c.company,),
            )
        elif change == "company":
            conn.execute(
                "UPDATE accounting.companies SET status='archived',version=version+1 WHERE id=%s",
                (c.company,),
            )
        else:
            conn.execute("UPDATE accounting.users SET active=0 WHERE id=%s", (actor.user_id,))
    if change == "user":
        with pytest.raises(AccountingError, match="停用"):
            service.profile(actor)
    else:
        value = service.profile(actor)
        assert value["companies"] == []
        assert not any(
            value[key] for key in ("can_accounting", "can_review", "can_submit", "can_operations")
        )
