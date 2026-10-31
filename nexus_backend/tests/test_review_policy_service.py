"""Independent review contracts on actual local services; no provider calls."""

import pytest

from app.accounting.domain import AccountingError
from app.accounting.models import (
    ApprovalRequest,
    ImportRequest,
    SourceRow,
    DraftUpdate,
    ExportRequest,
)
from app.accounting.review_policy import ReviewPolicyService
from app.accounting.review_policy_models import (
    ReviewPolicyChange,
    ManualReviewApproval,
    ManualReviewPost,
    ManualReviewWithdrawal,
)
from app.posting.manual_review import ManualReviewService
from app.posting.models import ManualLine, ManualVoucher, OpeningBind
from app.posting.service import PostingService
from test_business import business  # noqa: F401
from test_posting_service import frozen_opening


@pytest.mark.parametrize(
    "setting", ["foreign_keys=OFF", "writable_schema=ON", "ignore_check_constraints=ON"]
)
def test_runtime_sql_cannot_disable_review_constraints(business, setting):
    import sqlite3

    with business.db.connect() as conn:
        with pytest.raises(sqlite3.DatabaseError):
            conn.execute("PRAGMA " + setting)


def test_policy_waiver_http_requires_existing_admin_step_up():
    from app.auth.admin_security import requires_admin_mfa

    assert requires_admin_mfa("/api/books/fictional-book/review-policy", "POST")
    assert not requires_admin_mfa("/api/books/fictional-book/review-policy", "GET")


def pending(case, key="review-policy-bank"):
    case.ledger.import_sources(
        case.manager,
        case.book["id"],
        ImportRequest(
            source_name="复核政策虚构流水",
            rows=[
                SourceRow(
                    external_id=key,
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
    case.ledger.suggestions(case.manager, case.book["id"])
    return next(
        d
        for d in case.ledger.get_book(case.manager, case.book["id"])["drafts"]
        if d["status"] == "draft"
    )


def manual(case):
    opening = frozen_opening(case)
    posting = PostingService(case.db)
    posting.bind_opening(
        case.manager,
        OpeningBind(
            book_id=case.book["id"],
            package_version_id=opening["version_id"],
            reason="绑定复核测试期初",
        ),
    )
    request = ManualVoucher(
        command_key="review-policy-manual",
        book_id=case.book["id"],
        voucher_date="2026-10-16",
        description="虚构手工凭证",
        lines=[
            ManualLine(account_id=opening["expense"]["id"], account_version=1, debit="0.30"),
            ManualLine(account_id=opening["bank"]["id"], account_version=1, credit="0.30"),
        ],
    )
    return posting, request


def test_default_policy_rejects_maker_review(business):
    case = business
    draft = pending(case)
    with pytest.raises(AccountingError, match="制单|独立复核"):
        case.ledger.approve(
            case.manager,
            case.book["id"],
            draft["id"],
            ApprovalRequest(expected_version=draft["version"]),
        )
    assert case.ledger.get_draft(case.manager, case.book["id"], draft["id"])["status"] == "draft"


def test_default_policy_blocks_direct_manual_post(business):
    posting, request = manual(business)
    with pytest.raises(AccountingError, match="待审|复核|审核"):
        posting.post_manual(business.manager, request)
    with business.db.transaction(actor=business.manager) as conn:
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM vouchers WHERE book_id=?", (business.book["id"],)
            ).fetchone()[0]
            == 0
        )


def test_independent_review_receipt_survives_policy_change_and_exports(business):
    case = business
    draft = pending(case)
    approved = case.ledger.approve(
        case.actors["accountant"],
        case.book["id"],
        draft["id"],
        ApprovalRequest(expected_version=draft["version"]),
    )
    responsibility = approved["review_responsibility"]
    assert responsibility["independent"] is True
    assert responsibility["maker_user_id"] == case.manager.user_id
    assert responsibility["reviewer_user_id"] == "accountant"
    assert responsibility["reviewed_version"] == draft["version"]
    ReviewPolicyService(case.db).configure(
        case.manager,
        case.book["id"],
        ReviewPolicyChange(expected_version=0, mode="single_operator", reason="虚构单人办理政策"),
    )
    assert (
        case.ledger.get_draft(case.manager, case.book["id"], draft["id"])["review_responsibility"]
        == responsibility
    )
    exported = case.ledger.export(
        case.manager,
        case.book["id"],
        ExportRequest(draft_ids=[draft["id"]], idempotency_key="review-policy-export"),
    )
    assert exported["count"] == 1


def test_single_operator_requires_explicit_version_and_reason(business):
    case = business
    draft = pending(case)
    policy = ReviewPolicyService(case.db)
    policy.configure(
        case.manager,
        case.book["id"],
        ReviewPolicyChange(
            expected_version=0, mode="single_operator", reason="试点单人承担办理责任"
        ),
    )
    with pytest.raises(AccountingError, match="政策"):
        case.ledger.approve(
            case.manager,
            case.book["id"],
            draft["id"],
            ApprovalRequest(expected_version=draft["version"]),
        )
    with pytest.raises(AccountingError, match="理由"):
        case.ledger.approve(
            case.manager,
            case.book["id"],
            draft["id"],
            ApprovalRequest(expected_version=draft["version"], expected_policy_version=1),
        )
    result = case.ledger.approve(
        case.manager,
        case.book["id"],
        draft["id"],
        ApprovalRequest(
            expected_version=draft["version"],
            expected_policy_version=1,
            review_reason="逐项核对原件后单人办理",
        ),
    )
    receipt = result["review_responsibility"]
    assert receipt["independent"] is False
    assert receipt["label"] == "单人办理，非独立复核"
    assert receipt["maker_user_id"] == receipt["reviewer_user_id"] == case.manager.user_id


def test_latest_content_editor_becomes_maker_and_old_version_cannot_review(business):
    case = business
    draft = pending(case)
    edited = case.ledger.update_draft(
        case.actors["accountant"],
        case.book["id"],
        draft["id"],
        DraftUpdate(
            expected_version=draft["version"],
            description="虚构修改摘要",
            lines=draft["lines"],
            correction_reason="更正本轮摘要",
        ),
    )
    assert edited["version"] > draft["version"]
    assert edited["review_responsibility"]["maker_user_id"] == "accountant"
    with pytest.raises(AccountingError):
        case.ledger.approve(
            case.manager,
            case.book["id"],
            draft["id"],
            ApprovalRequest(expected_version=draft["version"]),
        )
    with pytest.raises(AccountingError, match="制单"):
        case.ledger.approve(
            case.actors["accountant"],
            case.book["id"],
            draft["id"],
            ApprovalRequest(expected_version=edited["version"]),
        )
    assert (
        case.ledger.approve(
            case.manager,
            case.book["id"],
            draft["id"],
            ApprovalRequest(expected_version=edited["version"]),
        )["status"]
        == "approved"
    )


def test_manual_independent_review_post_replay_and_tamper_rejection(business):
    case = business
    posting, payload = manual(case)
    service = ManualReviewService(case.db)
    prepared = service.prepare(case.manager, payload)["request"]
    assert prepared["prepared_by"] == case.manager.user_id
    with pytest.raises(AccountingError, match="制单"):
        service.approve(
            case.manager,
            prepared["id"],
            ManualReviewApproval(
                expected_version=1, expected_policy_version=0, reason="不得自审测试"
            ),
        )
    reviewed = service.approve(
        case.actors["accountant"],
        prepared["id"],
        ManualReviewApproval(
            expected_version=1, expected_policy_version=0, reason="逐行独立核对虚构原件"
        ),
    )["request"]
    assert reviewed["independent"] is True
    with pytest.raises(AccountingError, match="内容"):
        posting.post_manual(
            case.manager,
            payload.model_copy(
                update={"review_request_id": prepared["id"], "description": "未审核替换摘要"}
            ),
        )
    command = ManualReviewPost(
        expected_version=reviewed["version"], command_key=payload.command_key
    )
    first = service.post(case.manager, prepared["id"], command)
    second = service.post(case.manager, prepared["id"], command)
    assert first["voucher"]["id"] == second["voucher"]["id"]
    assert first["voucher"]["review_mode"] == "independent"
    listed = service.list_requests(case.manager, case.book["id"])
    assert listed["requests"][0]["posted_voucher_id"] == first["voucher"]["id"]
    assert listed["accounts"]


def test_company_manager_cannot_waive_independent_review(business):
    case = business
    policy = ReviewPolicyService(case.db)
    with pytest.raises(AccountingError):
        policy.configure(
            case.actors["company_admin"],
            case.book["id"],
            ReviewPolicyChange(
                expected_version=0, mode="single_operator", reason="不得由客户自行豁免"
            ),
        )
    assert policy.get(case.manager, case.book["id"])["policy"]["version"] == 0


def test_manual_withdrawal_preserves_source_and_cannot_be_reviewed_or_reworded(business):
    case = business
    _, payload = manual(case)
    service = ManualReviewService(case.db)
    prepared = service.prepare(case.manager, payload)["request"]
    command = ManualReviewWithdrawal(
        expected_version=prepared["version"], reason="金额录错，保留原件重新制单"
    )
    with pytest.raises(AccountingError, match="制单责任人"):
        service.withdraw(case.actors["accountant"], prepared["id"], command)
    first = service.withdraw(case.manager, prepared["id"], command)["request"]
    second = service.withdraw(case.manager, prepared["id"], command)["request"]
    assert first == second
    assert first["withdrawn"] is True
    assert first["content"] == prepared["content"]
    with pytest.raises(AccountingError, match="撤回"):
        service.withdraw(
            case.manager,
            prepared["id"],
            command.model_copy(update={"reason": "企图覆盖原来的撤回理由"}),
        )
    with pytest.raises(AccountingError, match="撤回"):
        service.approve(
            case.actors["accountant"],
            prepared["id"],
            ManualReviewApproval(
                expected_version=prepared["version"],
                expected_policy_version=0,
                reason="撤回后不能再审核",
            ),
        )
    with case.db.transaction(actor=case.manager) as conn:
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM manual_review_withdrawals WHERE request_id=?",
                (prepared["id"],),
            ).fetchone()[0]
            == 1
        )
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM audit_events WHERE object_id=? AND action='manual.withdraw'",
                (prepared["id"],),
            ).fetchone()[0]
            == 1
        )


def test_reviewed_manual_cannot_be_withdrawn(business):
    case = business
    _, payload = manual(case)
    service = ManualReviewService(case.db)
    prepared = service.prepare(case.manager, payload)["request"]
    reviewed = service.approve(
        case.actors["accountant"],
        prepared["id"],
        ManualReviewApproval(
            expected_version=prepared["version"],
            expected_policy_version=0,
            reason="逐项检查后独立审核",
        ),
    )["request"]
    with pytest.raises(AccountingError, match="冲销|调整"):
        service.withdraw(
            case.manager,
            prepared["id"],
            ManualReviewWithdrawal(
                expected_version=reviewed["version"], reason="已审不能通过撤回取消证据"
            ),
        )


def test_batch_applies_same_responsibility_and_policy_guards(business):
    from app.accounting.review import ReviewService
    from app.accounting.review_models import ReviewPreviewRequest, ReviewItem, ReviewConfirmRequest

    case = business
    draft = pending(case)
    service = ReviewService(case.db, case.ledger)
    payload = ReviewPreviewRequest(
        items=[
            ReviewItem(
                book_id=case.book["id"], draft_id=draft["id"], expected_version=draft["version"]
            )
        ]
    )
    own = service.preview(case.manager, payload)
    assert own["items"][0]["ready"] is False
    preview = service.preview(case.actors["accountant"], payload)
    assert preview["items"][0]["ready"] is True
    ReviewPolicyService(case.db).configure(
        case.manager,
        case.book["id"],
        ReviewPolicyChange(
            expected_version=0, mode="single_operator", reason="变更政策验证旧预览失效"
        ),
    )
    outcome = service.confirm(
        case.actors["accountant"],
        preview["id"],
        ReviewConfirmRequest(idempotency_key="batch-policy-stale"),
    )
    assert outcome["approved_count"] == 0


def test_onboarding_requires_distinct_people_unless_explicit_single_policy(business):
    from app.accounting.onboarding import FACTS_SQL

    case = business

    # Other company has only the firm's owner; no independent reviewer is assigned.
    def facts():
        with case.db.transaction(actor=case.manager) as conn:
            return dict(
                conn.execute(
                    FACTS_SQL, (case.other["id"], case.manager.firm_id, "2026-10")
                ).fetchone()
            )

    initial = facts()
    assert initial["books"] == 1 and initial["staffed_books"] == 0
    assert initial["single_operator_books"] == 0
    ReviewPolicyService(case.db).configure(
        case.manager,
        case.other_book["id"],
        ReviewPolicyChange(
            expected_version=0, mode="single_operator", reason="合成企业明确单人承担责任"
        ),
    )
    changed = facts()
    assert changed["staffed_books"] == changed["single_operator_books"] == 1


def test_single_operator_does_not_waive_business_applicant_conflict(business):
    from test_business import create, attach
    from app.accounting.models import CompanyMemberGrant, BookMemberGrant
    from app.business.models import HandoffRequest

    case = business
    case.directory.grant_company_member(
        case.manager,
        case.company["id"],
        CompanyMemberGrant(
            email="accountant@example.test", role="finance", department_id=case.department["id"]
        ),
    )
    from app.accounting.models import FirmMemberGrant
    case.directory.grant_firm_member(case.manager, FirmMemberGrant(email="finance@example.test", role="reviewer"))
    case.directory.grant_book_member(
        case.manager, case.book["id"], "finance", BookMemberGrant(role="reviewer")
    )
    event = attach(case, create(case, actor="finance"), actor="finance")
    event = case.service.submit(case.actors["finance"], event["id"], event["version"])
    event = case.service.approve_department(case.actors["approver"], event["id"], event["version"])
    ReviewPolicyService(case.db).configure(
        case.manager,
        case.book["id"],
        ReviewPolicyChange(
            expected_version=0, mode="single_operator", reason="仅记账单人政策不放宽业务审批"
        ),
    )
    with pytest.raises(AccountingError):
        case.service.approve_finance(case.actors["finance"], event["id"], event["version"])
    event = case.service.approve_finance(case.actors["accountant"], event["id"], event["version"])
    mapped = case.service.handoff(
        case.actors["accountant"],
        event["id"],
        HandoffRequest(
            expected_version=event["version"],
            book_id=case.book["id"],
            idempotency_key="single-policy-business-handoff",
        ),
    )
    draft = case.ledger.get_draft(case.actors["finance"], case.book["id"], mapped["draft_id"])
    with pytest.raises(AccountingError, match="申请人"):
        case.ledger.approve(
            case.actors["finance"],
            case.book["id"],
            draft["id"],
            ApprovalRequest(
                expected_version=draft["version"],
                expected_policy_version=1,
                review_reason="单人办理也不得自审报销",
            ),
        )
