"""A manual review request is real pending work, not invisible to month close."""

from app.b04_close.models import ClosePreflight
from app.posting.models import ManualVoucher, ManualLine
from app.posting.manual_review import ManualReviewService
from app.accounting.review_policy_models import ManualReviewWithdrawal
from test_p0_close_dependencies import close_case  # noqa: F401
from test_business import business  # noqa: F401


def pending_manual(c):
    return ManualReviewService(c.db).prepare(
        c.manager,
        ManualVoucher(
            command_key="pending-manual-month-close",
            book_id=c.book["id"],
            voucher_date="2026-10-28",
            description="尚未确定处理的虚构费用",
            lines=[
                ManualLine(account_id=c.opening["expense"]["id"], account_version=1, debit="0.30"),
                ManualLine(account_id=c.opening["bank"]["id"], account_version=1, credit="0.30"),
            ],
        ),
    )["request"]


def test_unreviewed_manual_request_blocks_fresh_month_close(close_case):
    c = close_case
    pending_manual(c)
    result = c.close.preflight(
        c.manager,
        ClosePreflight(
            book_id=c.book["id"],
            period="2026-10",
            report_id=c.report["report"]["id"],
            idempotency_key="pending-manual-month-close-check",
            reason="手工待办须纳入月结预检",
        ),
    )
    assert result["close"]["status"] == "preflight_failed"
    assert any(
        r["code"] == "unposted_manual" and r["status"] == "blocked" for r in result["checks"]
    )


def test_withdrawn_manual_preserves_history_and_allows_fresh_month_close(close_case):
    c = close_case
    pending = pending_manual(c)
    ManualReviewService(c.db).withdraw(
        c.manager,
        pending["id"],
        ManualReviewWithdrawal(
            expected_version=pending["version"], reason="虚构录错费用，留痕撤回后重新检查"
        ),
    )
    result = c.close.preflight(
        c.manager,
        ClosePreflight(
            book_id=c.book["id"],
            period="2026-10",
            report_id=c.report["report"]["id"],
            idempotency_key="withdrawn-manual-month-close-check",
            reason="保留撤回历史并重新月结预检",
        ),
    )
    assert result["close"]["status"] == "preflight_passed"
    assert any(r["code"] == "unposted_manual" and r["status"] == "pass" for r in result["checks"])
    with c.db.transaction(actor=c.manager) as conn:
        assert (
            conn.execute(
                "SELECT status FROM manual_review_requests WHERE id=?", (pending["id"],)
            ).fetchone()[0]
            == "pending"
        )
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM manual_review_withdrawals WHERE request_id=?",
                (pending["id"],),
            ).fetchone()[0]
            == 1
        )
