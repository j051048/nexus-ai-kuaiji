"""Restricted PostgreSQL roles enforce administration even without HTTP guards."""

import pytest

from app.accounting.admin_models import AdminCompanyStatus, AdminCompanyUpdate
from app.accounting.admin_service import AdminService
from app.accounting.directory import DirectoryService
from app.accounting.domain import AccountingError
from app.accounting.models import BookMemberGrant, CompanyCreate
from test_postgres_integration import ADMIN_URL, APP_URL, case, peer, postgres, request

pytestmark = pytest.mark.skipif(
    not ADMIN_URL or not APP_URL, reason="需要本机隔离 PostgreSQL 测试连接"
)


def test_pg_admin_lifecycle_retains_metadata_but_native_ledger_reads_are_blocked(case):
    service = AdminService(case.db)
    case.service.import_sources(case.actor, case.book, request())
    assert service.list_companies(case.actor)["companies"][0]["book_count"] == 1
    suspended = service.change_status(
        case.actor,
        case.company,
        AdminCompanyStatus(status="suspended", expected_version=1, reason="暂停试点服务"),
    )
    assert suspended["version"] == 2
    metadata = service.get_company(case.actor, case.company)
    assert metadata["books"][0]["id"] == case.book
    assert metadata["pending_counts"] == {
        "business_events": 0,
        "cases": 0,
        "ocr_jobs": 0,
        "proposal_jobs": 0,
    }
    with case.db.transaction(actor=case.actor) as conn:
        assert conn.execute("SELECT * FROM sources WHERE book_id=?", (case.book,)).fetchall() == []
        assert conn.execute("SELECT * FROM books WHERE id=?", (case.book,)).fetchall() == []
    with pytest.raises(case.psycopg.errors.CheckViolation):
        with case.db.transaction(write=True, actor=case.actor) as conn:
            conn.execute(
                "UPDATE firm_clients SET status='active' WHERE company_id=?", (case.company,)
            )
    service.change_status(
        case.actor,
        case.company,
        AdminCompanyStatus(status="archived", expected_version=2, reason="归档"),
    )
    service.change_status(
        case.actor,
        case.company,
        AdminCompanyStatus(status="active", expected_version=3, reason="恢复"),
    )
    assert len(case.service.get_book(case.actor, case.book)["sources"]) == 1


def test_pg_company_admin_native_cannot_stop_company_or_elevate_self(case):
    admin = peer(case, company_role="admin", book_role="viewer")
    service = AdminService(case.db)
    service.update_company(
        admin,
        case.company,
        AdminCompanyUpdate(name="更名后的企业", expected_version=1, reason="资料更正"),
    )
    mutations = [
        ("UPDATE companies SET status='archived',version=version+1 WHERE id=?", (case.company,)),
        (
            "UPDATE company_memberships SET role='owner' WHERE company_id=? AND user_id=?",
            (case.company, admin.user_id),
        ),
        (
            "UPDATE book_access SET role='reviewer' WHERE book_id=? AND user_id=?",
            (case.book, admin.user_id),
        ),
    ]
    for sql, params in mutations:
        with pytest.raises(case.psycopg.errors.InsufficientPrivilege):
            with case.db.transaction(write=True, actor=admin) as conn:
                conn.execute(sql, params)
    assert service.list_companies(admin)["companies"][0]["name"] == "更名后的企业"


def test_pg_company_admin_cannot_change_firm_staff_native_or_service(case):
    admin = peer(case, company_role="admin", book_role="viewer")
    staff = peer(case, company_role="finance", firm_role="reviewer", book_role="reviewer")
    with pytest.raises(case.psycopg.errors.InsufficientPrivilege):
        with case.db.transaction(write=True, actor=admin) as conn:
            conn.execute(
                "DELETE FROM book_access WHERE book_id=? AND user_id=?", (case.book, staff.user_id)
            )
    with pytest.raises(case.psycopg.errors.InsufficientPrivilege):
        with case.db.transaction(write=True, actor=admin) as conn:
            conn.execute(
                "UPDATE company_memberships SET role='admin' WHERE company_id=? AND user_id=?",
                (case.company, staff.user_id),
            )
    with pytest.raises(AccountingError):
        DirectoryService(case.db).grant_book_member(
            admin, case.book, staff.user_id, BookMemberGrant(role="viewer")
        )


def test_pg_owner_native_removal_and_self_membership_update_are_protected(case):
    manager = peer(case, firm_role="admin")
    with pytest.raises(case.psycopg.errors.CheckViolation):
        with case.db.transaction(write=True, actor=manager) as conn:
            conn.execute(
                "DELETE FROM memberships WHERE firm_id=? AND user_id=?",
                (case.actor.firm_id, case.actor.user_id),
            )
    with pytest.raises(case.psycopg.errors.InsufficientPrivilege):
        with case.db.transaction(write=True, actor=manager) as conn:
            conn.execute(
                "UPDATE memberships SET role='owner' WHERE firm_id=? AND user_id=?",
                (manager.firm_id, manager.user_id),
            )


def test_pg_cross_firm_metadata_and_audit_are_not_disclosed(case):
    outsider = peer(case, firm_role="owner", other_firm=True)
    admin = AdminService(case.db)
    assert admin.list_companies(outsider)["total"] == 0
    with pytest.raises(AccountingError):
        admin.get_company(outsider, case.company)
    with pytest.raises(case.psycopg.errors.InsufficientPrivilege):
        with case.db.transaction(actor=outsider) as conn:
            conn.execute("SELECT accounting.admin_company_metadata(?)", (case.company,)).fetchone()
    with case.db.transaction(actor=outsider) as conn:
        assert not conn.execute(
            "SELECT * FROM audit_events WHERE company_id=?", (case.company,)
        ).fetchall()
    with pytest.raises(case.psycopg.errors.CheckViolation):
        with case.db.transaction(write=True, actor=outsider) as conn:
            conn.execute(
                "INSERT INTO audit_events(firm_id,user_id,action,object_id,details,created_at,company_id) VALUES(?,?,'company.update',?,'{}','2026-10-14',?)",
                (outsider.firm_id, outsider.user_id, case.company, case.company),
            )


def test_pg_company_admin_audit_scope_and_nonactive_access(case):
    admin = peer(case, company_role="admin")
    other = DirectoryService(case.db).create_company(case.actor, CompanyCreate(name="同所另一企业"))
    service = AdminService(case.db)
    before = service.audit(admin)
    # New organizations now have their own audited AI default. Retain that
    # evidence while measuring the one explicit company update below.
    assert all(event["company_id"] == case.company for event in before["events"])
    service.update_company(
        case.actor,
        case.company,
        AdminCompanyUpdate(name="目标企业", expected_version=1, reason="更名"),
    )
    assert service.audit(admin)["total"] == before["total"] + 1
    assert service.audit(admin)["events"][0]["details"]["after"]["name"] == "目标企业"
    with case.db.transaction(actor=admin) as conn:
        assert not conn.execute(
            "SELECT * FROM audit_events WHERE company_id=?", (other["id"],)
        ).fetchall()
    service.change_status(
        case.actor,
        case.company,
        AdminCompanyStatus(status="suspended", expected_version=2, reason="暂停"),
    )
    with pytest.raises(AccountingError):
        service.get_company(admin, case.company)


def test_pg_unique_service_relationship_and_version_guard(case):
    another = peer(case, firm_role="owner", other_firm=True)
    with pytest.raises(case.psycopg.errors.UniqueViolation):
        with case.psycopg.connect(ADMIN_URL) as conn:
            conn.execute(
                "INSERT INTO accounting.firm_clients(firm_id,company_id) VALUES(%s,%s)",
                (another.firm_id, case.company),
            )
    with pytest.raises(case.psycopg.errors.CheckViolation):
        with case.db.transaction(write=True, actor=case.actor) as conn:
            conn.execute("UPDATE companies SET name='旧版本绕过' WHERE id=?", (case.company,))


@pytest.mark.parametrize("mutation", ["delete", "downgrade"])
def test_pg_native_member_removal_or_downgrade_cleans_only_its_book_grants(case, mutation):
    member = peer(case, company_role="finance", book_role="editor")
    with case.db.transaction(write=True, actor=case.actor) as conn:
        if mutation == "delete":
            conn.execute(
                "DELETE FROM company_memberships WHERE company_id=? AND user_id=?",
                (case.company, member.user_id),
            )
        else:
            conn.execute(
                "UPDATE company_memberships SET role='employee' WHERE company_id=? AND user_id=?",
                (case.company, member.user_id),
            )
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM book_access WHERE book_id=? AND user_id=?",
                (case.book, member.user_id),
            ).fetchone()[0]
            == 0
        )
        assert (
            conn.execute("SELECT active FROM users WHERE id=?", (member.user_id,)).fetchone()[0]
            == 1
        )
