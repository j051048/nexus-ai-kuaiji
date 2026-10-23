"""Company guard restoration must retain projection and runtime boundaries."""

import os
from uuid import uuid4

import pytest

from app.accounting.database import Database
from test_ak05_unified_postgres import require_isolated_target
from test_business_postgres import ADMIN_URL, APP_URL, business_case, business_postgres

pytestmark = pytest.mark.skipif(
    os.getenv("ACCOUNTING_AK05_ISOLATED_TARGET") != "1",
    reason="requires explicit disposable AK05 PG",
)


@pytest.mark.parametrize("mismatch", [None, "company", "firm"])
def test_projection_write_uses_exact_declared_scope(business_case, mismatch):
    case = business_case
    url = os.environ["ACCOUNTING_PROJECTION_DATABASE_URL"]
    projection = Database(
        url, default_role="accounting_projection", validation_role="accounting_projection"
    )
    company = case.other_company if mismatch == "company" else case.company
    firm = "other-synthetic-firm-" + uuid4().hex if mismatch == "firm" else case.owner.firm_id

    def write():
        with projection.transaction(
            write=True, write_scope="company", firm_id=case.owner.firm_id, company_id=case.company
        ) as conn:
            conn.execute(
                "INSERT INTO workspace_task_projections(firm_id,company_id,kind,ref,state,title,updated_at) VALUES(?,?,'alert',?,'open','AK05 scope test','2026-10-23T00:00:00+00:00')",
                (firm, company, uuid4().hex),
            )

    try:
        if mismatch:
            with pytest.raises(case.psycopg.errors.InsufficientPrivilege):
                write()
        else:
            write()
    finally:
        projection.close()


def test_test_logins_are_restricted_and_roles_not_login(business_case):
    c = business_case
    with c.psycopg.connect(ADMIN_URL) as conn:
        roles = conn.execute(
            "SELECT rolname,rolsuper,rolbypassrls,rolcanlogin,rolcreatedb,rolcreaterole FROM pg_roles WHERE rolname IN ('ak05_runtime','ak05_worker','ak05_projection','accounting_app','accounting_worker','accounting_projection') ORDER BY rolname"
        ).fetchall()
    assert len(roles) == 6
    for name, superuser, bypass, login, createdb, createrole in roles:
        assert not (superuser or bypass or createdb or createrole)
        assert login == name.startswith("ak05_")
