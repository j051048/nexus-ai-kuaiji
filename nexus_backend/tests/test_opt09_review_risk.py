from app.accounting.review_risk import REVIEW_RISK_RULE_VERSION, grade_review_risk


def test_bank_draft_without_findings_is_low_risk():
    result = grade_review_risk(
        issues=[],
        period_open=True,
        self_applicant=False,
        business_template="bank",
    )
    assert result == {
        "grade": "low",
        "reasons": [],
        "rule_version": REVIEW_RISK_RULE_VERSION,
    }


def test_any_finding_keeps_draft_out_of_low_risk():
    for issue in (
        "缺少凭证证据引用，请补充资料后审核",
        "借贷不平衡",
        "科目 6602 不存在或已停用",
        "已关联银行流水存在尚未关闭的异常或补件，须先处理后复核业务凭证",
    ):
        result = grade_review_risk(
            issues=[issue],
            period_open=True,
            self_applicant=False,
            business_template="bank",
        )
        assert result["grade"] == "high"
        assert issue in result["reasons"]


def test_closed_period_and_self_application_are_high_risk():
    closed = grade_review_risk(
        issues=[],
        period_open=False,
        self_applicant=False,
        business_template="bank",
    )
    assert closed["grade"] == "high"
    assert "会计期间已关闭" in closed["reasons"]

    self_review = grade_review_risk(
        issues=[],
        period_open=True,
        self_applicant=True,
        business_template="bank",
    )
    assert self_review["grade"] == "high"
    assert "申请人与当前复核人相同" in self_review["reasons"]


def test_business_event_without_findings_is_medium_with_reason():
    result = grade_review_risk(
        issues=[],
        period_open=True,
        self_applicant=False,
        business_template="reimbursement",
    )
    assert result["grade"] == "medium"
    assert result["reasons"] == ["业务事项来源，需核对申请事由与原始附件"]


def test_duplicate_and_blank_reasons_are_removed_in_order():
    result = grade_review_risk(
        issues=["借贷不平衡", "借贷不平衡", "  "],
        period_open=False,
        self_applicant=True,
        business_template="bank",
    )
    assert result["grade"] == "high"
    assert result["reasons"] == ["借贷不平衡", "会计期间已关闭", "申请人与当前复核人相同"]
    assert result["rule_version"] == "review-risk-v1"
