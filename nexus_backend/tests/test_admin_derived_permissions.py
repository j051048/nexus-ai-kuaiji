"""Membership revocation must not resurrect historical authority after re-enrollment."""

from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.accounting.directory import DirectoryService
from app.accounting.domain import Actor
from app.accounting.models import (
    BookCreate,
    BookMemberGrant,
    CompanyCreate,
    CompanyMemberGrant,
    FirmMemberGrant,
)
from app.ledgers.models import ManagerGrant, PeriodCreate
from app.ledgers.service import LedgersService
from test_directory import case as sqlite_case
from test_postgres_integration import ADMIN_URL, APP_URL, case as pg_case, postgres


@pytest.fixture(params=["sqlite", "postgres"])
def scoped_case(request):
    if request.param == "postgres" and not (ADMIN_URL and APP_URL):
        pytest.skip("需要本机隔离 PostgreSQL 测试连接")
    original = request.getfixturevalue("pg_case" if request.param == "postgres" else "sqlite_case")
    return SimpleNamespace(
        db=original.db,
        service=original.service,
        actor=original.actor,
        directory=DirectoryService(original.db),
        ledgers=LedgersService(original.db),
        company=original.company if request.param == "postgres" else original.company["id"],
        book=original.book if request.param == "postgres" else original.book["id"],
    )


def person(c, company=None, role="finance", firm_role=None):
    uid = "derived-" + uuid4().hex
    identity = SimpleNamespace(
        user_id=uid, display_name="权限回归账号", email=uid + "@example.test"
    )
    c.directory.register_identity(identity)
    c.directory.grant_company_member(
        c.actor, company or c.company, CompanyMemberGrant(email=identity.email, role=role)
    )
    if firm_role:
        c.directory.grant_firm_member(
            c.actor, FirmMemberGrant(email=identity.email, role=firm_role)
        )
    return identity, Actor(uid, c.actor.firm_id)


def derived_grants(c, member, company=None, book=None):
    company, book = company or c.company, book or c.book
    c.directory.grant_book_member(c.actor, book, member.user_id, BookMemberGrant(role="reviewer"))
    c.ledgers.grant_manager(
        c.actor, book, ManagerGrant(user_id=member.user_id, reason="明确授予长期账套管理职责")
    )
    ledger = c.ledgers.overview(c.actor, book)["id"]
    project = uuid4().hex
    stamp = "2026-10-14T12:00:00+00:00"
    with c.db.transaction(write=True, actor=c.actor) as conn:
        conn.execute(
            "INSERT INTO migration_projects(id,firm_id,company_id,ledger_id,book_id,external_book_id,source_system_key,source_product,product_version,history_start,history_end,cutover_date,create_key,create_fingerprint,created_by,created_at,updated_at) VALUES(?,?,?,?,?,?,'tplus','T+','标准模板 v1','2026-01-01','2026-09-30','2026-10-01',?,?,?,?,?)",
            (
                project,
                c.actor.firm_id,
                company,
                ledger,
                book,
                "native-" + project,
                project,
                "a" * 64,
                c.actor.user_id,
                stamp,
                stamp,
            ),
        )
        for user in [c.actor.user_id, member.user_id]:
            conn.execute(
                "INSERT INTO migration_project_access VALUES(?,?,'manager',?,?,?)",
                (project, user, c.actor.user_id, "明确迁移管理责任", stamp),
            )
    return ledger, project


def assert_removed(c, uid, ledger, project, book=None):
    with c.db.transaction(actor=c.actor) as conn:
        for sql, params in [
            (
                "SELECT COUNT(*) FROM book_access WHERE book_id=? AND user_id=?",
                (book or c.book, uid),
            ),
            ("SELECT COUNT(*) FROM ledger_managers WHERE ledger_id=? AND user_id=?", (ledger, uid)),
            (
                "SELECT COUNT(*) FROM migration_project_access WHERE project_id=? AND user_id=?",
                (project, uid),
            ),
        ]:
            assert conn.execute(sql, params).fetchone()[0] == 0
        assert conn.execute("SELECT active FROM users WHERE id=?", (uid,)).fetchone()[0] == 1


def test_explicit_company_finance_manager_can_create_period_without_granting_existing_books(
    scoped_case,
):
    c = scoped_case
    member, actor = person(c)
    derived_grants(c, member)
    next_period = c.ledgers.create_period(
        actor,
        c.book,
        PeriodCreate(
            period="2026-11",
            name="十一月",
            source_book_id=c.book,
            idempotency_key="finance-next-period",
        ),
    )
    assert c.service.get_book(actor, next_period["book_id"])["book"]["permissions"]["review"]
    assert c.ledgers.overview(actor, c.book)["can_manage"]


@pytest.mark.parametrize("mutation", ["delete", "downgrade", "native_delete", "native_downgrade"])
def test_company_admin_revocation_clears_derived_grants_only_in_its_company(scoped_case, mutation):
    c = scoped_case
    member, actor = person(c)
    _, admin = person(c, role="admin")
    ledger, project = derived_grants(c, member)
    other = c.directory.create_company(c.actor, CompanyCreate(name="保留授权的乙企业"))["id"]
    other_book = c.service.create_book(
        c.actor, BookCreate(name="乙企业账套", company_id=other, period="2026-10")
    )["id"]
    c.directory.grant_company_member(
        c.actor, other, CompanyMemberGrant(email=member.email, role="finance")
    )
    other_ledger, other_project = derived_grants(c, member, other, other_book)

    # This company administrator has no ledger/project grant of their own.
    if mutation.startswith("native_"):
        with c.db.transaction(write=True, actor=admin) as conn:
            if mutation == "native_delete":
                conn.execute(
                    "DELETE FROM company_memberships WHERE company_id=? AND user_id=?",
                    (c.company, member.user_id),
                )
            else:
                conn.execute(
                    "UPDATE company_memberships SET role='employee' WHERE company_id=? AND user_id=?",
                    (c.company, member.user_id),
                )
    elif mutation == "delete":
        c.directory.revoke_company_member(admin, c.company, member.user_id)
    else:
        c.directory.grant_company_member(
            admin, c.company, CompanyMemberGrant(email=member.email, role="employee")
        )
    assert_removed(c, member.user_id, ledger, project)
    c.directory.grant_company_member(
        c.actor, c.company, CompanyMemberGrant(email=member.email, role="finance")
    )
    c.directory.grant_book_member(c.actor, c.book, member.user_id, BookMemberGrant(role="reviewer"))
    assert not c.ledgers.overview(actor, c.book)["can_manage"]
    assert c.ledgers.overview(actor, other_book)["can_manage"]
    with c.db.transaction(actor=c.actor) as conn:
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM migration_project_access WHERE project_id=? AND user_id=?",
                (other_project, member.user_id),
            ).fetchone()[0]
            == 1
        )
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM ledger_managers WHERE ledger_id=? AND user_id=?",
                (other_ledger, member.user_id),
            ).fetchone()[0]
            == 1
        )


def test_firm_member_revocation_clears_derived_grants_even_with_separate_company_membership(
    scoped_case,
):
    c = scoped_case
    member, actor = person(c, firm_role="reviewer")
    ledger, project = derived_grants(c, member)
    c.directory.revoke_firm_member(c.actor, member.user_id)
    assert_removed(c, member.user_id, ledger, project)
    c.directory.grant_firm_member(c.actor, FirmMemberGrant(email=member.email, role="reviewer"))
    c.directory.grant_book_member(c.actor, c.book, member.user_id, BookMemberGrant(role="reviewer"))
    assert not c.ledgers.overview(actor, c.book)["can_manage"]


def test_removing_company_membership_preserves_independent_firm_authority(scoped_case):
    c = scoped_case
    member, actor = person(c, firm_role="reviewer")
    _, project = derived_grants(c, member)
    c.directory.revoke_company_member(c.actor, c.company, member.user_id)
    assert c.ledgers.overview(actor, c.book)["can_manage"]
    with c.db.transaction(actor=c.actor) as conn:
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM migration_project_access WHERE project_id=? AND user_id=?",
                (project, member.user_id),
            ).fetchone()[0]
            == 1
        )
