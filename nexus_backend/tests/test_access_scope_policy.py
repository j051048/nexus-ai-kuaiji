"""Agency review and explicitly scoped access; isolated fixtures only."""

import pytest

from app.accounting.access import AccessControl
from app.accounting.domain import AccountingError, Actor
from app.accounting.models import BookMemberGrant, FirmMemberGrant, BookCreate
from test_business import business  # noqa: F401


def test_new_access_mutations_require_step_up():
    from app.auth.admin_security import requires_admin_mfa

    for path in (
        "/api/firm/access-assignments",
        "/api/firm/access-assignments/grant-id/revoke",
        "/api/books/book-id/contact-access",
        "/api/books/book-id/contact-access/revoke",
    ):
        assert requires_admin_mfa(path, "POST")
        assert not requires_admin_mfa(path, "GET")
    assert not requires_admin_mfa(
        "/api/books/book-id/delivery/monthly-package/package-id/acknowledge", "POST"
    )


def test_company_finance_is_not_formal_agency_reviewer():
    permissions = AccessControl.permissions(None, "reviewer", "finance")
    assert permissions["read"] and permissions["edit"]
    assert not permissions["review"] and not permissions["export"]


def test_omitted_scope_does_not_mean_all_firm():
    from pydantic import ValidationError
    from app.accounting.access_policy import StaffAssignment

    with pytest.raises(ValidationError):
        StaffAssignment(user_id="viewer", template="auditor", reason="漏传客户范围不得扩大权限")


def test_company_admin_cannot_grant_formal_reviewer(business):
    c = business
    with pytest.raises(AccountingError, match="事务所|复核"):
        c.directory.grant_book_member(
            c.actors["company_admin"], c.book["id"], "finance", BookMemberGrant(role="reviewer")
        )


def policy(c):
    from app.accounting.access_policy import AccessPolicyService

    return AccessPolicyService(c.db)


def assignment(c, **values):
    from app.accounting.access_policy import StaffAssignment

    return StaffAssignment(
        user_id="audit-viewer",
        template="auditor",
        company_id=c.company["id"],
        reason="虚构客户限期审计",
        **values,
    )


def viewer(c):
    from types import SimpleNamespace

    c.directory.register_identity(
        SimpleNamespace(
            user_id="audit-viewer", display_name="虚构审计员", email="audit-viewer@example.test"
        )
    )
    c.directory.grant_firm_member(
        c.manager, FirmMemberGrant(email="audit-viewer@example.test", role="viewer")
    )
    return Actor("audit-viewer", c.manager.firm_id)


def test_explicit_company_scope_and_revocation(business):
    c = business
    actor = viewer(c)
    with pytest.raises(AccountingError):
        c.ledger.get_book(actor, c.book["id"])

    grant = policy(c).assign(c.manager, assignment(c))
    assert c.ledger.get_book(actor, c.book["id"])["book"]["permissions"]["review"] is False
    assert c.ledger.workbench(actor)["pagination"]["total"] == 1
    with pytest.raises(AccountingError):
        c.ledger.get_book(actor, c.other_book["id"])
    policy(c).revoke(c.manager, grant["id"], "审计结束撤销授权")
    assert c.ledger.workbench(actor)["pagination"]["total"] == 0
    with pytest.raises(AccountingError):
        c.ledger.get_book(actor, c.book["id"])


def test_repeating_same_assignment_does_not_leave_hidden_extra_grants(business):
    c = business
    viewer(c)
    first = policy(c).assign(c.manager, assignment(c))
    second = policy(c).assign(c.manager, assignment(c))
    assert first["id"] == second["id"]
    assert len(policy(c).overview(c.manager)["assignments"]) == 1


@pytest.mark.parametrize(
    "window", [{"valid_to": "2000-01-01T00:00:00Z"}, {"valid_from": "2099-01-01T00:00:00Z"}]
)
def test_inactive_time_window_grants_nothing(business, window):
    c = business
    actor = viewer(c)
    policy(c).assign(c.manager, assignment(c, **window))
    assert c.ledger.workbench(actor)["pagination"]["total"] == 0


def test_all_firm_read_is_explicit_and_covers_new_books(business):
    from app.accounting.access_policy import StaffAssignment

    c = business
    actor = viewer(c)
    assert c.ledger.workbench(actor)["pagination"]["total"] == 0
    policy(c).assign(
        c.manager,
        StaffAssignment(
            user_id=actor.user_id, template="auditor", scope="firm", reason="全所质控只读授权"
        ),
    )
    assert c.ledger.workbench(actor)["pagination"]["total"] == 2
    new = c.ledger.create_book(
        c.manager, BookCreate(name="未来月份", company_id=c.company["id"], period="2026-11")
    )
    assert c.ledger.get_book(actor, new["id"])["book"]["permissions"] == {
        "edit": False,
        "review": False,
        "export": False,
        "manage": False,
    }


def test_scope_grant_cannot_promote_role_or_be_self_issued(business):
    from app.accounting.access_policy import StaffAssignment

    c = business
    actor = viewer(c)
    with pytest.raises(AccountingError):
        policy(c).assign(actor, assignment(c))
    with pytest.raises(AccountingError):
        policy(c).assign(
            c.manager,
            StaffAssignment(
                user_id=actor.user_id, template="reviewer", scope="firm", reason="不能靠模板提权"
            ),
        )


def test_department_report_does_not_grant_ledger_access(business):
    c = business
    result = policy(c).department_expenses(
        c.actors["approver"], c.company["id"], "2026-10", c.department["id"]
    )
    assert result["department_id"] == c.department["id"] and result["expense_cents"] == 0
    assert "lines" not in result and "counterparty" not in result
    with pytest.raises(AccountingError):
        policy(c).department_expenses(
            c.actors["employee"], c.company["id"], "2026-10", c.department["id"]
        )
    with pytest.raises(AccountingError):
        policy(c).department_expenses(
            c.actors["approver"], c.other["id"], "2026-10", c.department["id"]
        )
    with pytest.raises(AccountingError):
        c.ledger.get_book(c.actors["approver"], c.book["id"])


def test_department_total_excludes_other_departments_and_unallocated(business):
    from test_review_policy_service import manual
    from review_policy_fixtures import post_reviewed_manual
    from app.posting.models import ManualVoucher

    c = business
    posting, original = manual(c)
    for index, department in enumerate((c.department["id"], c.other_department["id"], None)):
        lines = [
            line.model_copy(
                update={"auxiliary_values": {"department": department} if department else {}}
            )
            if i == 0
            else line
            for i, line in enumerate(original.lines)
        ]
        post_reviewed_manual(
            posting,
            c.manager,
            ManualVoucher(
                **{
                    **original.model_dump(),
                    "command_key": f"department-expense-{index}",
                    "lines": lines,
                }
            ),
        )
    result = policy(c).department_expenses(
        c.actors["approver"], c.company["id"], "2026-10", c.department["id"]
    )
    assert result["expense_cents"] == 30
    c.service.revoke_department_approver(c.manager, c.company["id"], c.department["id"], "approver")
    with pytest.raises(AccountingError):
        policy(c).department_expenses(
            c.actors["approver"], c.company["id"], "2026-10", c.department["id"]
        )


def test_historical_receipt_without_firm_proof_is_visible_but_cannot_be_reused(business):
    import sqlite3
    from test_review_policy_service import pending
    from app.accounting.models import ApprovalRequest, ExportRequest
    from app.accounting.review_policy import ReviewPolicyService

    c = business
    draft = pending(c)
    c.ledger.approve(
        c.actors["accountant"],
        c.book["id"],
        draft["id"],
        ApprovalRequest(expected_version=draft["version"]),
    )
    # Privileged fixture restoration models a pre-cutover receipt with no
    # verifiable firm authority. All runtime guards are restored before use.
    with sqlite3.connect(c.db.path) as restore:
        ddl = restore.execute(
            "SELECT sql FROM sqlite_master WHERE name='authority_no_delete'"
        ).fetchone()[0]
        restore.execute("DROP TRIGGER authority_no_delete")
        restore.execute(
            "DELETE FROM review_authority_snapshots WHERE subject_kind='draft' AND subject_id=?",
            (draft["id"],),
        )
        restore.execute(ddl)
    current = c.ledger.get_draft(c.manager, c.book["id"], draft["id"])
    assert current["status"] == "approved"
    with c.db.transaction(actor=c.manager) as conn:
        with pytest.raises(AccountingError):
            ReviewPolicyService.approved_receipt(conn, {**current, "book_id": c.book["id"]})
    with pytest.raises(AccountingError):
        c.ledger.export(
            c.manager,
            c.book["id"],
            ExportRequest(draft_ids=[draft["id"]], idempotency_key="historical-no-authority"),
        )
