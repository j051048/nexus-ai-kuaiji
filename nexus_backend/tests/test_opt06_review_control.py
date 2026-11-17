"""OPT06-A: firm-level ban on single-operator review."""

import sqlite3

import pytest

from app.accounting.domain import AccountingError
from app.accounting.models import ApprovalRequest
from app.accounting.review_policy import FirmReviewControlService, ReviewPolicyService
from app.accounting.review_policy_models import FirmReviewControlWrite, ReviewPolicyChange
from test_business import business  # noqa: F401
from test_review_policy_service import pending


def disable(case, *, mfa=True, disabled=True, expected=0):
    return FirmReviewControlService(case.db).configure(
        case.manager,
        FirmReviewControlWrite(
            expected_version=expected,
            single_operator_disabled=disabled,
            reason="客户集团要求所有账套保持独立复核，不允许单人办理",
        ),
        mfa_verified=mfa,
    )


def test_mfa_and_rejection_audit_are_required(business):
    with pytest.raises(AccountingError, match="多因素"):
        disable(business, mfa=False)
    with business.db.transaction(actor=business.manager) as conn:
        assert conn.execute("SELECT COUNT(*) FROM firm_review_controls").fetchone()[0] == 0

    assert disable(business)["single_operator_disabled"] is True
    with pytest.raises(AccountingError, match="禁止单人"):
        ReviewPolicyService(business.db).configure(
            business.manager,
            business.book["id"],
            ReviewPolicyChange(
                expected_version=0,
                mode="single_operator",
                reason="客户集团要求单人办理本次账套",
            ),
        )
    with business.db.transaction(actor=business.manager) as conn:
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM audit_events WHERE action='review.policy.rejected'"
            ).fetchone()[0]
            == 1
        )
        assert conn.execute("SELECT COUNT(*) FROM ledger_review_policies").fetchone()[0] == 0


def test_existing_single_operator_book_cannot_self_review_after_ban(business):
    policy = ReviewPolicyService(business.db)
    policy.configure(
        business.manager,
        business.book["id"],
        ReviewPolicyChange(
            expected_version=0,
            mode="single_operator",
            reason="历史单人办理政策仍需保留证据",
        ),
    )
    draft = pending(business)
    disable(business, expected=0)
    with pytest.raises(AccountingError, match="禁止单人"):
        business.ledger.approve(
            business.manager,
            business.book["id"],
            draft["id"],
            ApprovalRequest(expected_version=draft["version"]),
        )


def test_direct_database_insert_is_rejected_when_firm_control_enabled(business):
    disable(business)
    policy = ReviewPolicyService(business.db).policy_in_transaction(
        business.db.connect(), business.book["id"]
    )
    with pytest.raises(sqlite3.IntegrityError, match="single_operator disabled"):
        with business.db.transaction(write=True, actor=business.manager) as conn:
            conn.execute(
                "INSERT INTO ledger_review_policies(ledger_id,version,firm_id,company_id,mode,reason,confirmed_by,created_at) "
                "VALUES(?,?,?,?,'single_operator','直接写库绕过失败',?,?)",
                (
                    policy["ledger_id"],
                    policy["version"] + 1,
                    business.manager.firm_id,
                    business.book["company_id"],
                    business.manager.user_id,
                    "2026-11-17T00:00:00+00:00",
                ),
            )


def enable_separation(case, *, expected=0, enabled=True):
    return FirmReviewControlService(case.db).configure(
        case.manager,
        FirmReviewControlWrite(
            expected_version=expected,
            single_operator_disabled=False,
            management_review_separation=enabled,
            reason="集团要求管理岗位不得成为账务复核人",
        ),
        mfa_verified=True,
    )


def test_separation_requires_a_non_manager_reviewer_for_every_book(business):
    from app.accounting.access import AccessControl
    from app.accounting.models import BookMemberGrant

    with pytest.raises(AccountingError, match="没有非管理员复核人") as error:
        enable_separation(business)
    assert business.other_book["name"] in str(error.value)

    business.directory.grant_book_member(
        business.manager,
        business.other_book["id"],
        business.actors["accountant"].user_id,
        BookMemberGrant(role="reviewer"),
    )
    control = enable_separation(business)
    assert control["management_review_separation"] is True

    access = AccessControl()
    with pytest.raises(AccountingError, match="复核"):
        access.book(business.db.connect(), business.manager, business.book["id"], "review")
    read = access.book(business.db.connect(), business.manager, business.book["id"], "read")
    assert read["id"] == business.book["id"]
    reviewer = access.book(
        business.db.connect(), business.actors["accountant"], business.book["id"], "review"
    )
    assert reviewer["id"] == business.book["id"]

def test_manager_cannot_grant_self_reviewer_under_separation(business):
    from app.accounting.models import BookMemberGrant

    business.directory.grant_book_member(
        business.manager,
        business.other_book["id"],
        business.actors["accountant"].user_id,
        BookMemberGrant(role="reviewer"),
    )
    enable_separation(business)
    with pytest.raises(AccountingError, match="管理员不能授予"):
        business.directory.grant_book_member(
            business.manager,
            business.book["id"],
            business.manager.user_id,
            BookMemberGrant(role="reviewer"),
        )
