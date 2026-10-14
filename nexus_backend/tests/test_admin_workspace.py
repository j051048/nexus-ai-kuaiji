"""Organization lifecycle, scoped authorization and HTTP contracts on SQLite."""

import sqlite3

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from app.accounting.admin_models import AdminCompanyStatus, AdminCompanyUpdate
from app.accounting.admin_router import build_admin_router
from app.accounting.admin_service import AdminService
from app.accounting.domain import AccountingError, Actor
from app.accounting.models import (
    BookCreate,
    BookMemberGrant,
    CompanyCreate,
    CompanyMemberGrant,
    DepartmentCreate,
    FirmCreate,
)
from test_directory import case, import_request
from test_business import business, create, submitted


def lifecycle(case, status, version):
    return AdminService(case.db).change_status(
        case.actor,
        case.company["id"],
        AdminCompanyStatus(status=status, expected_version=version, reason="试点企业状态变更"),
    )


def company_admin(case):
    user = case.user("company-admin")
    case.directory.grant_company_member(
        case.actor, case.company["id"], CompanyMemberGrant(email=user.email, role="admin")
    )
    return Actor(user.user_id, case.actor.firm_id)


def test_firm_admin_lists_all_its_companies_but_never_another_firm(case):
    service = AdminService(case.db)
    case.directory.create_company(case.actor, CompanyCreate(name="另一个客户"))
    outsider = case.user("other-owner")
    other = case.directory.create_firm(outsider.user_id, FirmCreate(name="其他事务所"))
    private = case.directory.create_company(
        Actor(outsider.user_id, other["id"]), CompanyCreate(name="其他事务所客户")
    )
    result = service.list_companies(case.actor, limit=1)
    assert result["total"] == 2 and len(result["companies"]) == 1
    assert result["companies"][0]["can_change_status"] is True
    assert all(c["id"] != private["id"] for c in service.list_companies(case.actor)["companies"])
    with pytest.raises(AccountingError) as error:
        service.get_company(case.actor, private["id"])
    assert error.value.status == 404


def test_company_admin_only_manages_own_active_company(case):
    admin = company_admin(case)
    service = AdminService(case.db)
    other = case.directory.create_company(case.actor, CompanyCreate(name="乙公司"))
    page = service.list_companies(admin)
    assert page["total"] == 1 and page["companies"][0]["id"] == case.company["id"]
    assert not page["companies"][0]["can_change_status"]
    assert case.directory.me(admin.user_id)["permissions"]["manage_admin"]
    service.update_company(
        admin,
        case.company["id"],
        AdminCompanyUpdate(name="甲公司", tax_id="TEST01", reason="更正名称", expected_version=1),
    )
    with pytest.raises(AccountingError):
        service.get_company(admin, other["id"])
    with pytest.raises(AccountingError):
        service.change_status(
            admin,
            case.company["id"],
            AdminCompanyStatus(status="suspended", reason="非法操作", expected_version=2),
        )
    lifecycle(case, "suspended", 2)
    with pytest.raises(AccountingError):
        service.get_company(admin, case.company["id"])


def test_lifecycle_preserves_evidence_and_blocks_ledger_until_restored(case):
    case.service.import_sources(case.actor, case.book["id"], import_request())
    case.service.suggestions(case.actor, case.book["id"])
    service = AdminService(case.db)
    for status, version in [("suspended", 1), ("archived", 2)]:
        result = lifecycle(case, status, version)
        assert result["status"] == status and result["version"] == version + 1
        assert service.list_companies(case.actor, status=status)["total"] == 1
        detail = service.get_company(case.actor, case.company["id"])
        assert detail["books"][0]["id"] == case.book["id"]
        with pytest.raises(AccountingError):
            case.service.get_book(case.actor, case.book["id"])
        with pytest.raises(AccountingError):
            case.service.import_sources(case.actor, case.book["id"], import_request())
        with case.db.transaction() as conn:
            assert (
                conn.execute(
                    "SELECT COUNT(*) FROM sources WHERE book_id=?", (case.book["id"],)
                ).fetchone()[0]
                == 1
            )
    lifecycle(case, "active", 3)
    assert len(case.service.get_book(case.actor, case.book["id"])["sources"]) == 1
    assert case.service.get_book(case.actor, case.book["id"])["drafts"][0]["status"] == "draft"


def test_version_conflict_and_audit_failure_roll_back_status(case, monkeypatch):
    service = AdminService(case.db)
    lifecycle(case, "suspended", 1)
    with pytest.raises(AccountingError) as error:
        lifecycle(case, "active", 1)
    assert error.value.status == 409
    monkeypatch.setattr(
        case.directory.__class__,
        "_audit",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("synthetic audit failure")),
    )
    with pytest.raises(RuntimeError):
        lifecycle(case, "active", 2)
    assert service.get_company(case.actor, case.company["id"])["company"]["status"] == "suspended"
    with case.db.transaction() as conn:
        assert (
            conn.execute(
                "SELECT status FROM firm_clients WHERE company_id=?", (case.company["id"],)
            ).fetchone()[0]
            == "suspended"
        )


def test_audit_lists_before_after_and_enforces_company_scope(case):
    admin = company_admin(case)
    other = case.directory.create_company(case.actor, CompanyCreate(name="乙公司"))
    service = AdminService(case.db)
    service.update_company(
        case.actor,
        case.company["id"],
        AdminCompanyUpdate(name="甲公司新名称", expected_version=1, reason="营业执照更名"),
    )
    events = service.audit(admin)["events"]
    assert all(event["company_id"] == case.company["id"] for event in events)
    change = next(event for event in events if event["action"] == "company.update")
    assert change["details"]["before"]["name"] == "试点客户企业"
    assert change["details"]["after"]["name"] == "甲公司新名称"
    assert change["details"]["reason"] == "营业执照更名"
    with pytest.raises(AccountingError):
        service.audit(admin, company_id=other["id"])
    assert (
        service.audit(case.actor, company_id=case.company["id"], action="company.update", limit=1)[
            "total"
        ]
        == 1
    )


def test_employee_cannot_discover_admin_data(case):
    employee = case.user("employee")
    case.directory.grant_company_member(
        case.actor, case.company["id"], CompanyMemberGrant(email=employee.email, role="employee")
    )
    actor = Actor(employee.user_id, case.actor.firm_id)
    assert not case.directory.me(employee.user_id)["permissions"]["manage_admin"]
    for call in [
        lambda: AdminService(case.db).list_companies(actor),
        lambda: AdminService(case.db).get_company(actor, case.company["id"]),
        lambda: AdminService(case.db).audit(actor),
    ]:
        with pytest.raises(AccountingError) as error:
            call()
        assert error.value.status == 403


def test_company_admin_cannot_elevate_own_book_grant(case):
    admin = company_admin(case)
    case.directory.grant_book_member(
        case.actor, case.book["id"], admin.user_id, BookMemberGrant(role="viewer")
    )
    with pytest.raises(AccountingError):
        case.directory.grant_book_member(
            admin, case.book["id"], admin.user_id, BookMemberGrant(role="reviewer")
        )
    with pytest.raises(AccountingError):
        case.directory.revoke_book_member(admin, case.book["id"], admin.user_id)


def test_remove_company_member_preserves_other_company_and_global_account(case):
    member = case.user("shared-person")
    other = case.directory.create_company(case.actor, CompanyCreate(name="乙公司"))
    book_b = case.service.create_book(
        case.actor, BookCreate(name="乙公司账套", company_id=other["id"], period="2026-10")
    )
    for company, book in [(case.company, case.book), (other, book_b)]:
        case.directory.grant_company_member(
            case.actor, company["id"], CompanyMemberGrant(email=member.email, role="finance")
        )
        case.directory.grant_book_member(
            case.actor, book["id"], member.user_id, BookMemberGrant(role="editor")
        )
    department = case.directory.create_department(
        case.actor, case.company["id"], DepartmentCreate(name="业务部")
    )
    with case.db.transaction(write=True) as conn:
        conn.execute(
            "INSERT INTO department_approvers VALUES(?,?,?,?)",
            (case.actor.firm_id, case.company["id"], department["id"], member.user_id),
        )
    case.directory.revoke_company_member(case.actor, case.company["id"], member.user_id)
    with case.db.transaction() as conn:
        assert (
            conn.execute("SELECT active FROM users WHERE id=?", (member.user_id,)).fetchone()[0]
            == 1
        )
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM department_approvers WHERE user_id=?", (member.user_id,)
            ).fetchone()[0]
            == 0
        )
    actor = Actor(member.user_id, case.actor.firm_id)
    with pytest.raises(AccountingError):
        case.service.get_book(actor, case.book["id"])
    assert case.service.get_book(actor, book_b["id"])["book"]["permissions"]["edit"]


def test_sqlite_native_lifecycle_guards_and_single_firm(case):
    lifecycle(case, "archived", 1)
    mutations = [
        ("UPDATE firm_clients SET status='active' WHERE company_id=?", (case.company["id"],)),
        ("UPDATE companies SET status='active' WHERE id=?", (case.company["id"],)),
        ("DELETE FROM companies WHERE id=?", (case.company["id"],)),
        (
            "DELETE FROM memberships WHERE firm_id=? AND user_id=?",
            (case.actor.firm_id, case.actor.user_id),
        ),
    ]
    for sql, params in mutations:
        with pytest.raises(sqlite3.IntegrityError):
            with case.db.transaction(write=True) as conn:
                conn.execute(sql, params)


def test_literal_search_does_not_treat_percent_as_wildcard(case):
    case.directory.create_company(case.actor, CompanyCreate(name="折扣100%公司"))
    assert AdminService(case.db).list_companies(case.actor, search="%")["total"] == 1


def test_admin_pending_counts_exclude_private_and_unassigned_department_events(business):
    service = AdminService(business.db)
    create(business)
    submitted(business, actor="peer")
    for actor in [business.manager, business.actors["company_admin"]]:
        assert (
            service.get_company(actor, business.company["id"])["pending_counts"]["business_events"]
            == 0
        )
    business.service.grant_department_approver(
        business.manager,
        business.company["id"],
        business.department["id"],
        business.actors["company_admin"].user_id,
    )
    assert (
        service.get_company(business.actors["company_admin"], business.company["id"])[
            "pending_counts"
        ]["business_events"]
        == 1
    )


def test_downgrade_does_not_silently_restore_old_book_permissions(case):
    member = case.user("downgraded-person")
    for role in ["finance", "employee", "finance"]:
        case.directory.grant_company_member(
            case.actor, case.company["id"], CompanyMemberGrant(email=member.email, role=role)
        )
        if role == "finance":
            if not hasattr(member, "was_granted"):
                case.directory.grant_book_member(
                    case.actor, case.book["id"], member.user_id, BookMemberGrant(role="editor")
                )
                member.was_granted = True
    with pytest.raises(AccountingError):
        case.service.get_book(Actor(member.user_id, case.actor.firm_id), case.book["id"])


def test_admin_http_contract_rejects_unknown_fields_invalid_status_and_stale_version(case):
    app = FastAPI()
    app.state.admin = AdminService(case.db)
    app.include_router(build_admin_router(lambda: case.actor))

    @app.exception_handler(AccountingError)
    async def accounting_error(_request, error):
        return JSONResponse({"detail": error.detail}, status_code=error.status)

    with TestClient(app) as client:
        assert client.get("/api/admin/companies?limit=1").json()["total"] == 1
        path = f"/api/admin/companies/{case.company['id']}"
        assert client.get(path).json()["company"]["version"] == 1
        assert (
            client.patch(
                path, json={"name": "  ", "reason": "理由", "expected_version": 1}
            ).status_code
            == 422
        )
        assert (
            client.post(
                path + "/status",
                json={"status": "deleted", "reason": "理由", "expected_version": 1},
            ).status_code
            == 422
        )
        assert (
            client.post(
                path + "/status",
                json={
                    "status": "archived",
                    "reason": "归档",
                    "expected_version": 1,
                    "firm_id": "forged",
                },
            ).status_code
            == 422
        )
        assert (
            client.post(
                path + "/status",
                json={"status": "archived", "reason": "归档", "expected_version": 1},
            ).status_code
            == 200
        )
        assert (
            client.patch(
                path, json={"name": "旧版本", "reason": "理由", "expected_version": 1}
            ).status_code
            == 409
        )
        assert client.get("/api/admin/audit?company_id=" + case.company["id"]).json()["total"] == 2
