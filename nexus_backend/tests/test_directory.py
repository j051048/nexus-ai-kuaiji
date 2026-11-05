from types import SimpleNamespace

import pytest

from app.accounting.database import Database
from app.accounting.directory import DirectoryService
from app.accounting.models import (
    BookCreate, CompanyCreate, FirmCreate, FirmMemberGrant, BookMemberGrant,
    CompanyMemberGrant, DepartmentCreate, ImportRequest, ApprovalRequest,
)
from app.accounting.service import AccountingService, Actor, AccountingError


@pytest.fixture
def case(tmp_path):
    database = Database(tmp_path / "directory.db")
    directory = DirectoryService(database)
    service = AccountingService(database)
    def user(name):
        identity = SimpleNamespace(user_id=name, display_name=name, email=f"{name}@example.test")
        directory.register_identity(identity)
        return identity
    owner = user("owner")
    firm = directory.create_firm(owner.user_id, FirmCreate(name="试点事务所"))
    actor = Actor(owner.user_id, firm["id"])
    company = directory.create_company(actor, CompanyCreate(name="试点客户企业"))
    book = service.create_book(actor, BookCreate(name="客户账套", period="2026-10", company_id=company["id"]))
    return SimpleNamespace(db=database, directory=directory, service=service, user=user, actor=actor, company=company, book=book)


def grant(case, user, firm_role="operator", book_role="editor"):
    case.directory.grant_firm_member(case.actor, FirmMemberGrant(email=user.email, role=firm_role))
    if book_role:
        case.directory.grant_book_member(case.actor, case.book["id"], user.user_id, BookMemberGrant(role=book_role))
    return Actor(user.user_id, case.actor.firm_id)


def import_request():
    return ImportRequest(source_name="权限验证", rows=[{"external_id": "x", "date": "2026-10-01", "description": "办公用品", "amount": "0.30", "direction": "expense", "evidence_ref": "测试发票"}])


def test_new_identity_has_no_inherited_permissions_and_firm_selection_is_verified(case):
    outsider = case.user("outsider")
    assert case.directory.me(outsider.user_id)["firms"] == []
    with pytest.raises(AccountingError) as error:
        case.directory.me(outsider.user_id, case.actor.firm_id)
    assert error.value.status == 403
    private_firm = case.directory.create_firm(outsider.user_id, FirmCreate(name="独立事务所"))
    assert case.directory.me(outsider.user_id)["current_firm_id"] == private_firm["id"]
    assert case.service.workbench(Actor(outsider.user_id, private_firm["id"]))["books"] == []
    with pytest.raises(AccountingError):
        case.directory.create_company(Actor(outsider.user_id, case.actor.firm_id), CompanyCreate(name="伪造企业"))


def test_member_identity_and_book_authorization_are_both_required(case):
    operator = case.user("operator")
    actor = grant(case, operator, book_role=None)
    assert case.service.workbench(actor)["books"] == []
    with pytest.raises(AccountingError) as error:
        case.service.import_sources(actor, case.book["id"], import_request())
    assert error.value.status == 404
    case.directory.grant_book_member(case.actor, case.book["id"], operator.user_id, BookMemberGrant(role="editor"))
    permissions = case.service.get_book(actor, case.book["id"])["book"]["permissions"]
    assert permissions == {"edit": True, "review": False, "export": False, "manage": False}


@pytest.mark.parametrize("firm_role,book_role,edit,review", [
    ("viewer", "editor", False, False), ("operator", "editor", True, False),
    ("reviewer", "viewer", False, False), ("reviewer", "editor", True, False), ("reviewer", "reviewer", True, True),
])
def test_effective_role_is_intersection(case, firm_role, book_role, edit, review):
    peer = case.user("peer")
    actor = grant(case, peer, firm_role, book_role)
    permission = case.service.get_book(actor, case.book["id"])["book"]["permissions"]
    assert permission["edit"] is edit and permission["review"] is review
    if not edit:
        with pytest.raises(AccountingError) as error:
            case.service.import_sources(actor, case.book["id"], import_request())
        assert error.value.status == 403


def test_operator_cannot_review_even_if_granted_book_reviewer(case):
    operator = case.user("operator")
    with pytest.raises(AccountingError) as denied_grant:
        grant(case, operator, "operator", "reviewer")
    assert denied_grant.value.status == 403
    actor = grant(case, operator, "operator", "editor")
    case.service.import_sources(actor, case.book["id"], import_request())
    case.service.suggestions(actor, case.book["id"])
    draft = case.service.get_book(actor, case.book["id"])["drafts"][0]
    with pytest.raises(AccountingError) as error:
        case.service.approve(actor, case.book["id"], draft["id"], ApprovalRequest(expected_version=1))
    assert error.value.status == 403


def test_book_revocation_and_firm_removal_are_immediate(case):
    reviewer = case.user("reviewer")
    actor = grant(case, reviewer, "reviewer", "reviewer")
    case.directory.revoke_book_member(case.actor, case.book["id"], reviewer.user_id)
    with pytest.raises(AccountingError):
        case.service.get_book(actor, case.book["id"])
    case.directory.grant_book_member(case.actor, case.book["id"], reviewer.user_id, BookMemberGrant(role="reviewer"))
    case.directory.revoke_firm_member(case.actor, reviewer.user_id)
    assert case.directory.me(reviewer.user_id)["firms"] == []
    grant(case, reviewer, "reviewer", book_role=None)
    assert case.service.workbench(actor)["books"] == []


def test_employee_membership_gives_workspace_not_ledger_and_finance_needs_grant(case):
    employee = case.user("employee")
    actor = Actor(employee.user_id, case.actor.firm_id)
    case.directory.grant_company_member(case.actor, case.company["id"], CompanyMemberGrant(email=employee.email, role="employee"))
    assert case.directory.me(employee.user_id)["firms"][0]["role"] == "client_employee"
    assert case.service.workbench(actor)["books"] == []
    with pytest.raises(AccountingError):
        case.directory.grant_book_member(case.actor, case.book["id"], employee.user_id, BookMemberGrant(role="viewer"))
    case.directory.grant_company_member(case.actor, case.company["id"], CompanyMemberGrant(email=employee.email, role="finance"))
    assert case.service.workbench(actor)["books"] == []
    with pytest.raises(AccountingError) as denied_reviewer:
        case.directory.grant_book_member(case.actor, case.book["id"], employee.user_id, BookMemberGrant(role="reviewer"))
    assert denied_reviewer.value.status == 403
    case.directory.grant_book_member(case.actor, case.book["id"], employee.user_id, BookMemberGrant(role="editor"))
    permissions = case.service.get_book(actor, case.book["id"])["book"]["permissions"]
    assert permissions["edit"] and not permissions["review"]


def test_departments_cannot_be_assigned_across_companies(case):
    another = case.directory.create_company(case.actor, CompanyCreate(name="其他企业"))
    department = case.directory.create_department(case.actor, another["id"], DepartmentCreate(name="其他财务部"))
    employee = case.user("employee")
    with pytest.raises(AccountingError) as error:
        case.directory.grant_company_member(case.actor, case.company["id"], CompanyMemberGrant(email=employee.email, role="employee", department_id=department["id"]))
    assert error.value.status == 422


def test_ordinary_accounts_cannot_self_promote_and_owner_is_protected(case):
    peer = case.user("peer")
    actor = grant(case, peer, "viewer", "viewer")
    with pytest.raises(AccountingError) as error:
        case.directory.grant_firm_member(actor, FirmMemberGrant(email=peer.email, role="admin"))
    assert error.value.status == 403
    with pytest.raises(AccountingError):
        case.directory.revoke_firm_member(case.actor, case.actor.user_id)
    with pytest.raises(AccountingError):
        case.directory.grant_firm_member(case.actor, FirmMemberGrant(email="owner@example.test", role="viewer"))


def test_company_admin_cannot_change_or_remove_firm_staff_assignment(case):
    admin = case.user("clientadmin")
    case.directory.grant_company_member(case.actor, case.company["id"], CompanyMemberGrant(email=admin.email, role="admin"))
    case.directory.grant_book_member(case.actor, case.book["id"], admin.user_id, BookMemberGrant(role="viewer"))
    actor = Actor(admin.user_id, case.actor.firm_id)
    staff = case.user("staff")
    grant(case, staff, "operator", "editor")
    case.directory.grant_company_member(case.actor, case.company["id"], CompanyMemberGrant(email=staff.email, role="finance"))
    with pytest.raises(AccountingError):
        case.directory.grant_book_member(actor, case.book["id"], staff.user_id, BookMemberGrant(role="reviewer"))
    with pytest.raises(AccountingError):
        case.directory.revoke_book_member(actor, case.book["id"], staff.user_id)
    with pytest.raises(AccountingError):
        case.directory.grant_company_member(actor, case.company["id"], CompanyMemberGrant(email=staff.email, role="admin"))
    with pytest.raises(AccountingError):
        case.directory.revoke_company_member(actor, case.company["id"], staff.user_id)
    assert case.service.get_book(Actor(staff.user_id, case.actor.firm_id), case.book["id"])["book"]["permissions"]["edit"]


def test_permission_change_audit_is_transactional_and_inactive_user_stays_inactive(case, monkeypatch):
    peer = case.user("peer")
    original_audit = case.directory._audit
    monkeypatch.setattr(case.directory, "_audit", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("audit failed")))
    with pytest.raises(RuntimeError):
        case.directory.grant_firm_member(case.actor, FirmMemberGrant(email=peer.email, role="admin"))
    assert case.directory.me(peer.user_id)["firms"] == []
    monkeypatch.setattr(case.directory, "_audit", original_audit)
    with case.db.transaction(write=True) as conn:
        conn.execute("UPDATE users SET active=0 WHERE id=?", (peer.user_id,))
    with pytest.raises(AccountingError):
        case.directory.register_identity(peer)


@pytest.mark.parametrize("target,role", [("clientadmin", "owner"), ("clientadmin", "finance"), ("another", "owner")])
def test_company_admin_cannot_self_promote_or_assign_company_owner(case, target, role):
    admin, another = case.user("clientadmin"), case.user("another")
    case.directory.grant_company_member(case.actor, case.company["id"], CompanyMemberGrant(email=admin.email, role="admin"))
    with pytest.raises(AccountingError) as error:
        case.directory.grant_company_member(Actor(admin.user_id, case.actor.firm_id), case.company["id"], CompanyMemberGrant(email=f"{target}@example.test", role=role))
    assert error.value.status == 403
