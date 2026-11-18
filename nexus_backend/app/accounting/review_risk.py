"""Deterministic review-risk projection.

The grade is a display and sorting signal, never a review approval. Only
verifiable facts already computed by the accounting guards are used; no model
confidence number is accepted or invented.
"""

from __future__ import annotations

from typing import Iterable

REVIEW_RISK_RULE_VERSION = "review-risk-v1"


def grade_review_risk(
    *,
    issues: Iterable[str],
    period_open: bool,
    self_applicant: bool,
    business_template: str,
) -> dict:
    reasons = []
    for issue in issues:
        text = str(issue).strip()
        if text and text not in reasons:
            reasons.append(text)
    if not period_open:
        reasons.append("会计期间已关闭")
    if self_applicant:
        reasons.append("申请人与当前复核人相同")

    if reasons:
        grade = "high"
    elif business_template != "bank":
        grade = "medium"
        reasons.append("业务事项来源，需核对申请事由与原始附件")
    else:
        grade = "low"

    return {
        "grade": grade,
        "reasons": reasons,
        "rule_version": REVIEW_RISK_RULE_VERSION,
    }
