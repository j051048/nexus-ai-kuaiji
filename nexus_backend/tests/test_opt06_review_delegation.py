"""OPT06-C: review delegation is time-bounded and independently approved."""

from datetime import datetime, timedelta, timezone

import pytest

from app.accounting.domain import AccountingError
from app.accounting.models import ApprovalRequest
from app.accounting.review_delegations import (
    ReviewDelegationRevoke,
    ReviewDelegationService,
    ReviewDelegationWrite,
)
from app.accounting.models import ImportRequest, SourceRow
from test_business import business  # noqa: F401


def stamp(delta=0):
    return (datetime.now(timezone.utc) + timedelta(days=delta)).isoformat()


def request(case, *, requester="accountant", to="finance", days=2):
    return ReviewDelegationService(case.db).create(
        case.actors[requester],
        case.book["id"],
        ReviewDelegationWrite(
            to_user_id=case.actors[to].user_id,
            starts_at=stamp(-1),
            ends_at=stamp(days),
            reason="复核人休假期间的受控替补安排",
        ),
    )


def pending_as(case, actor):
    case.ledger.import_sources(
        actor,
        case.book["id"],
        ImportRequest(
            source_name="替补自审虚构流水",
            rows=[
                SourceRow(
                    external_id="delegate-self-source",
                    date="2026-10-15",
                    description="办公用品",
                    counterparty="虚构供方",
                    amount="0.30",
                    direction="expense",
                    evidence_ref="虚构票据依据",
                )
            ],
        ),
    )
    case.ledger.suggestions(actor, case.book["id"])
    return next(
        d
        for d in case.ledger.get_book(case.manager, case.book["id"])["drafts"]
        if d["status"] == "draft"
    )


def activated(case, **changes):
    svc = ReviewDelegationService(case.db)
    row = request(case, **changes)
    return svc.approve(case.manager, case.book["id"], row["id"])


def test_delegate_can_review_only_inside_window_and_use_is_audited(business):
    row = activated(business)
    assert row["status"] == "active"
    draft = pending_as(business, business.manager)
    result = business.ledger.approve(
        business.actors["finance"],
        business.book["id"],
        draft["id"],
        ApprovalRequest(expected_version=draft["version"]),
    )
    assert result["status"] == "approved"
    with business.db.transaction(actor=business.manager) as conn:
        actions = {
            r[0]
            for r in conn.execute(
                "SELECT action FROM audit_events WHERE object_id IN (?,?)", (row["id"], draft["id"])
            )
        }
    assert {
        "review.delegation.request",
        "review.delegation.approve",
        "review.delegation.use",
    } <= actions


def test_expired_delegation_is_rejected_by_current_time_and_expiry_is_audited(business):
    row = activated(business, days=1)
    draft = pending_as(business, business.manager)
    ended = (datetime.fromisoformat(row["ends_at"]) + timedelta(seconds=1)).isoformat()
    ReviewDelegationService(business.db).expire_due(business.manager, at=ended)
    with pytest.raises(AccountingError, match="没有该账套"):
        business.ledger.approve(
            business.actors["finance"],
            business.book["id"],
            draft["id"],
            ApprovalRequest(expected_version=draft["version"]),
        )
    with business.db.transaction(actor=business.manager) as conn:
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM audit_events WHERE action='review.delegation.expire'"
            ).fetchone()[0]
            == 1
        )


def test_delegate_cannot_review_own_draft(business):
    activated(business)
    draft = pending_as(business, business.actors["finance"])
    with pytest.raises(AccountingError, match="不能独立复核本人"):
        business.ledger.approve(
            business.actors["finance"],
            business.book["id"],
            draft["id"],
            ApprovalRequest(expected_version=draft["version"]),
        )


def test_requester_cannot_approve_and_revoke_is_audited(business):
    svc = ReviewDelegationService(business.db)
    row = svc.create(
        business.manager,
        business.book["id"],
        ReviewDelegationWrite(
            to_user_id=business.actors["finance"].user_id,
            starts_at=stamp(-1),
            ends_at=stamp(2),
            reason="经理发起的受控复核替补安排",
        ),
    )
    with pytest.raises(AccountingError, match="发起人不能批准"):
        svc.approve(business.manager, business.book["id"], row["id"])
    # The fixture has one manager; revoking the pending request still exercises
    # the independent owner/requester path and leaves a durable audit row.
    revoked = svc.revoke(
        business.manager,
        business.book["id"],
        row["id"],
        ReviewDelegationRevoke(reason="业务安排取消，提前撤销替补"),
    )
    assert revoked["status"] == "revoked"
    with business.db.transaction(actor=business.manager) as conn:
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM audit_events WHERE action='review.delegation.revoke'"
            ).fetchone()[0]
            == 1
        )
