"""Primary workspace identity is independent of additive business capabilities."""

from types import SimpleNamespace

import pytest

from app.accounting.domain import AccountingError, Actor
from app.accounting.models import BookMemberGrant, CompanyMemberGrant, FirmMemberGrant
from app.business.models import SubmissionPolicyUpdate
from app.business.submission import SubmissionPolicyService
from test_business import business
from test_stage12_workspace import workspace


@pytest.mark.parametrize(
    "name, expected",
    [
        ("employee", "employee"),
        ("approver", "approver"),
        ("finance", "finance"),
        ("company_admin", "manager"),
    ],
)
def test_independent_company_jobs_have_distinct_primary_identity(business, name, expected):
    c = business
    profile = workspace(c).profile(c.actors[name])
    assert profile["role"] == expected
    assert profile["display_role"] == expected
    assert [company["id"] for company in profile["companies"]] == [c.company["id"]]
    assert profile["can_manage"] is (name == "company_admin")
    assert profile["can_finance"] is (name == "finance")
    assert profile["can_approve"] is (name == "approver")


@pytest.mark.parametrize("company_role", ["owner", "admin"])
@pytest.mark.parametrize("book_role", ["viewer", "editor", "reviewer"])
def test_company_management_stays_primary_with_submission_approval_and_book_access(
    business, company_role, book_role
):
    c, actor = business, business.actors["company_admin"]
    c.directory.grant_company_member(
        c.manager,
        c.company["id"],
        CompanyMemberGrant(
            email="company_admin@example.test",
            role=company_role,
            department_id=c.department["id"],
        ),
    )
    c.service.grant_department_approver(
        c.manager, c.company["id"], c.department["id"], actor.user_id
    )
    if book_role == "reviewer":
        with pytest.raises(AccountingError):
            c.directory.grant_book_member(c.manager, c.book["id"], actor.user_id, BookMemberGrant(role=book_role))
        book_role = "editor"
    c.directory.grant_book_member(
        c.manager, c.book["id"], actor.user_id, BookMemberGrant(role=book_role)
    )
    profile = workspace(c).profile(actor)
    assert (profile["role"], profile["display_role"]) == ("manager", "manager")
    assert profile["can_submit"] and profile["can_approve"] and profile["can_accounting"]
    assert profile["can_manage"] and not profile["can_finance"]
    # Presentation must not turn an enterprise administrator into a book reviewer.
    assert profile["can_review"] is False
    with c.db.transaction(actor=actor) as conn:
        permissions = c.ledger.access.book(conn, actor, c.book["id"])["permissions"]
    assert permissions["review"] is False and permissions["export"] is False


def test_direct_submission_does_not_replace_enterprise_management_identity(business):
    c, actor = business, business.actors["company_admin"]
    SubmissionPolicyService(c.db).update(
        c.manager,
        c.company["id"],
        SubmissionPolicyUpdate(
            mode="direct", expected_version=0, reason="虚构企业授权管理员直接移交资料"
        ),
    )
    profile = workspace(c).profile(actor)
    assert profile["can_submit"] is True
    assert profile["role"] == profile["display_role"] == "manager"


def test_finance_and_approver_stay_primary_when_extra_capabilities_are_granted(business):
    c, service = business, workspace(business)
    finance = c.actors["finance"]
    c.service.grant_department_approver(
        c.manager, c.company["id"], c.department["id"], finance.user_id
    )
    with pytest.raises(AccountingError):
        c.directory.grant_book_member(c.manager, c.book["id"], finance.user_id, BookMemberGrant(role="reviewer"))
    profile = service.profile(finance)
    assert profile["role"] == profile["display_role"] == "finance"
    assert profile["can_approve"] and not profile["can_review"] and profile["can_accounting"]
    approver = service.profile(c.actors["approver"])
    assert approver["role"] == approver["display_role"] == "approver"
    assert approver["can_submit"] and not approver["can_accounting"]


def test_company_manager_stays_primary_over_finance_in_a_second_company(business):
    c, actor = business, business.actors["company_admin"]
    c.directory.grant_company_member(
        c.manager,
        c.other["id"],
        CompanyMemberGrant(email="company_admin@example.test", role="finance"),
    )
    profile = workspace(c).profile(actor)
    assert profile["can_manage"] and profile["can_finance"]
    assert profile["role"] == profile["display_role"] == "manager"


@pytest.mark.parametrize(
    "firm_role, expected_display",
    [
        ("owner", "firm_owner"),
        ("admin", "admin"),
        ("operator", "accountant"),
        ("reviewer", "reviewer"),
    ],
)
def test_firm_job_remains_primary_over_company_management(business, firm_role, expected_display):
    c, service = business, workspace(business)
    if firm_role == "owner":
        actor, email = c.manager, "owner@example.test"
    else:
        identity = SimpleNamespace(
            user_id="firm-job", display_name="事务所同事", email="firm-job@example.test"
        )
        c.directory.register_identity(identity)
        c.directory.grant_firm_member(
            c.manager, FirmMemberGrant(email=identity.email, role=firm_role)
        )
        actor, email = Actor(identity.user_id, c.manager.firm_id), identity.email
        c.directory.grant_book_member(
            c.manager, c.book["id"], actor.user_id, BookMemberGrant(role="editor" if firm_role == "operator" else "reviewer")
        )
    c.directory.grant_company_member(
        c.manager,
        c.company["id"],
        CompanyMemberGrant(email=email, role="admin", department_id=c.department["id"]),
    )
    profile = service.profile(actor)
    assert profile["role"] == "accountant"
    assert profile["display_role"] == expected_display
    assert profile["can_review"] is (firm_role != "operator")
    assert profile["can_firm_operations"] is (firm_role in {"owner", "admin"})


def test_aggregate_manager_identity_does_not_grant_management_in_another_company(business):
    c, actor = business, business.actors["company_admin"]
    c.directory.grant_company_member(
        c.manager,
        c.other["id"],
        CompanyMemberGrant(email="company_admin@example.test", role="employee"),
    )
    service = workspace(c)
    profile = service.profile(actor)
    assert profile["role"] == profile["display_role"] == "manager"
    assert {company["id"] for company in profile["companies"]} == {c.company["id"], c.other["id"]}
    assert profile["can_accounting"] is False
    with pytest.raises(AccountingError) as blocked:
        c.directory.list_company_members(actor, c.other["id"])
    assert blocked.value.status == 403
    with pytest.raises(AccountingError) as hidden:
        c.ledger.get_book(actor, c.other_book["id"])
    assert hidden.value.status == 404
    c.directory.revoke_company_member(c.manager, c.company["id"], actor.user_id)
    refreshed = service.profile(actor)
    assert refreshed["role"] == refreshed["display_role"] == "viewer"
    assert refreshed["can_manage"] is False
    assert [company["id"] for company in refreshed["companies"]] == [c.other["id"]]


def test_identity_uses_live_grants_and_revocation_removes_additive_access(business):
    c, actor = business, business.actors["company_admin"]
    service = workspace(c)
    c.directory.grant_book_member(
        c.manager, c.book["id"], actor.user_id, BookMemberGrant(role="editor")
    )
    assert service.profile(actor)["role"] == "manager"
    c.directory.revoke_company_member(c.manager, c.company["id"], actor.user_id)
    profile = service.profile(actor)
    assert profile["role"] == profile["display_role"] == "viewer"
    assert profile["companies"] == [] and profile["book_count"] == 0
    assert not any(
        profile[name]
        for name in (
            "can_manage",
            "can_submit",
            "can_approve",
            "can_finance",
            "can_review",
            "can_accounting",
        )
    )
    with pytest.raises(AccountingError) as blocked:
        c.directory.list_company_members(actor, c.company["id"])
    assert blocked.value.status == 403
    with c.db.transaction(write=True, actor=c.manager) as conn:
        conn.execute("UPDATE users SET active=0 WHERE id=?", (actor.user_id,))
    with pytest.raises(AccountingError) as stopped:
        service.profile(actor)
    assert stopped.value.status == 403


def test_ordinary_employee_profile_never_leaks_another_company_or_review_right(business):
    c, actor = business, business.actors["employee"]
    profile = workspace(c).profile(actor)
    assert profile["role"] == "employee"
    assert c.other["id"] not in str(profile) and c.other_book["id"] not in str(profile)
    with pytest.raises(AccountingError) as hidden:
        c.ledger.get_book(actor, c.book["id"])
    assert hidden.value.status == 404
    with pytest.raises(AccountingError) as forbidden:
        c.service.list_events(actor, scope="company")
    assert forbidden.value.status == 403
