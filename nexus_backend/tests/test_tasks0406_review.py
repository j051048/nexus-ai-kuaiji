"""Summary must classify frozen review evidence, not today's policy."""

from app.accounting.models import ApprovalRequest
from app.accounting.review_policy import ReviewPolicyService
from app.accounting.review_policy_models import ReviewPolicyChange
from test_review_policy_service import business, pending  # noqa: F401


def test_review_summary_separates_single_operator_and_never_relabels_history(business):
    c = business
    policy = ReviewPolicyService(c.db)
    first = pending(c, "independent-example")
    c.ledger.approve(
        c.actors["accountant"],
        c.book["id"],
        first["id"],
        ApprovalRequest(expected_version=first["version"]),
    )
    policy.configure(
        c.manager,
        c.book["id"],
        ReviewPolicyChange(
            expected_version=0, mode="single_operator", reason="虚构单人办理，不标为独立复核"
        ),
    )
    second = pending(c, "single-example")
    c.ledger.approve(
        c.manager,
        c.book["id"],
        second["id"],
        ApprovalRequest(
            expected_version=second["version"],
            expected_policy_version=1,
            review_reason="原件核对后单人办理",
        ),
    )
    summary = policy.get(c.manager, c.book["id"])["review_summary"]
    assert summary["independent"] == 1 and summary["single_operator"] == 1
    assert summary["unverified"] == 0 and summary["total_reviewed"] == 2
    assert summary["single_operator_ratio"] == "0.5000"
    policy.configure(
        c.manager,
        c.book["id"],
        ReviewPolicyChange(expected_version=1, mode="independent", reason="恢复双人复核"),
    )
    assert policy.get(c.manager, c.book["id"])["review_summary"] == summary


def test_empty_review_population_is_unknown_not_zero_rate(business):
    s = ReviewPolicyService(business.db).get(business.manager, business.book["id"])[
        "review_summary"
    ]
    assert s["total_reviewed"] == 0 and s["single_operator_ratio"] is None
