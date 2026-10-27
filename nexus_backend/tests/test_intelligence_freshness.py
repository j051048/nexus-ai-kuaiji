from period_fixtures import restore_period_state
"""Published suggestions and human decisions remain tied to the current evidence."""
import pytest

from app.accounting.domain import AccountingError
from app.accounting.models import ApprovalRequest, DraftUpdate
from app.intelligence.provider import ProviderError
from test_intelligence_workflow import ai_case, create_event, extract, handoff, draft, propose, invoice_document


def test_other_applicants_invoice_is_detected_only_with_authorized_visibility(ai_case):
    first = create_event(ai_case)
    second = create_event(ai_case, actor="peer")
    extract(ai_case, first)
    extract(ai_case, second, actor="peer", key="other-applicant-ocr")
    own = ai_case.intelligence.detail(ai_case.actors["employee"], first["id"])
    assert not any(item["code"] == "duplicate_invoice_other_event" for item in own["findings"])
    finance = ai_case.intelligence.reconcile(ai_case.actors["finance"], first["id"])
    duplicate = next(item for item in finance["findings"] if item["code"] == "duplicate_invoice_other_event")
    assert duplicate["severity"] == "blocking" and duplicate["payload"]["match_count"] == 1
    assert second["id"] not in str(duplicate)


def test_new_extraction_invalidates_unapplied_suggestion_even_if_amounts_identical(ai_case):
    event = create_event(ai_case)
    extract(ai_case, event)
    receipt = handoff(ai_case, event)
    suggestion = propose(ai_case, receipt["draft_id"])
    current = ai_case.business.get_event(ai_case.actors["employee"], event["id"])
    extract(ai_case, current, key="second-identical-extraction")
    with pytest.raises(AccountingError, match="失效"):
        ai_case.intelligence.apply(ai_case.actors["accountant"], suggestion["id"], 1)
    assert draft(ai_case, receipt["draft_id"])["version"] == 1


@pytest.mark.parametrize("change", ["edit", "revoked"])
def test_late_suggestion_after_edit_or_book_revocation_is_discarded(ai_case, change):
    event = create_event(ai_case)
    extract(ai_case, event)
    receipt = handoff(ai_case, event)
    if change == "edit":
        before = draft(ai_case, receipt["draft_id"])
        ai_case.provider.proposal_hook = lambda: ai_case.ledger.update_draft(ai_case.actors["accountant"], ai_case.book["id"], before["id"], DraftUpdate(expected_version=1, description="会计已先手工修改", lines=before["lines"]))
    else:
        ai_case.provider.proposal_hook = lambda: ai_case.directory.revoke_book_member(ai_case.manager, ai_case.book["id"], "accountant")
    with pytest.raises(AccountingError):
        propose(ai_case, receipt["draft_id"])
    with ai_case.db.transaction() as conn:
        result = conn.execute("SELECT status,result_json FROM ai_runs WHERE kind='proposal'").fetchone()
    assert result["status"] == "stale" and result["result_json"] is None


def test_closed_period_and_frozen_source_cannot_send_employee_attachment(ai_case):
    event = create_event(ai_case)
    extract(ai_case, event)
    handoff(ai_case, event)
    current = ai_case.business.get_event(ai_case.actors["employee"], event["id"])
    restore_period_state(ai_case.ledger.db, ai_case.book["id"])
    with pytest.raises(AccountingError):
        extract(ai_case, current, key="closed-period-extract")
    assert ai_case.provider.extract_calls == 1
    assert ai_case.intelligence.detail(ai_case.actors["employee"], event["id"])["permissions"]["process"] is False


def test_terminal_run_cannot_be_overwritten_by_a_late_response(ai_case):
    event = create_event(ai_case)
    def abandon_pending():
        with ai_case.db.transaction() as conn:
            run = conn.execute("SELECT id FROM ai_runs WHERE event_id=? AND status='running'", (event["id"],)).fetchone()
        ai_case.intelligence._abandon(ai_case.actors["employee"], run["id"])
    ai_case.provider.extract_hook = abandon_pending
    with pytest.raises(AccountingError, match="失效"):
        extract(ai_case, event)
    with ai_case.db.transaction() as conn:
        result = conn.execute("SELECT status,result_json FROM ai_runs WHERE event_id=?", (event["id"],)).fetchone()
    assert result["status"] == "stale" and result["result_json"] is None


def test_applied_warning_survives_new_extraction_and_manual_draft_save(ai_case):
    event = create_event(ai_case)
    first_extraction = extract(ai_case, event)
    receipt = handoff(ai_case, event)
    ai_case.provider.proposal_mutator = lambda output: output.update(
        warnings=["请会计核对业务用途"]
    )
    suggestion = propose(ai_case, receipt["draft_id"])
    # Untrusted model prose is intentionally replaced with this fixed warning.
    # Track its proposal identity, not the model's original wording.
    safe_warning = "模型提示存在待人工核对事项，请核对原件与业务资料"
    assert suggestion["result"]["warnings"] == [safe_warning]
    warning_code = f"proposal_warning_{suggestion['id']}_0"
    warning = next(
        item
        for item in ai_case.intelligence.detail(
            ai_case.actors["accountant"], event["id"]
        )["findings"]
        if item["code"] == warning_code
    )
    assert warning["payload"]["proposal_id"] == suggestion["id"]
    assert warning["payload"]["message"] == "模型提示需人工核对：" + safe_warning
    assert warning["severity"] == "blocking" and warning["resolved"] is False
    with pytest.raises(AccountingError):
        ai_case.intelligence.apply(
            ai_case.actors["accountant"], suggestion["id"], suggestion["input_version"]
        )
    resolved = ai_case.intelligence.resolve(
        ai_case.actors["accountant"], warning["id"], "已核对用途及原件"
    )
    assert resolved["resolved"] is True
    applied = ai_case.intelligence.apply(
        ai_case.actors["accountant"], suggestion["id"], suggestion["input_version"]
    )
    current = ai_case.business.get_event(ai_case.actors["employee"], event["id"])
    refreshed_extraction = extract(ai_case, current, key="changed-warning-context")
    assert refreshed_extraction["status"] == "succeeded"
    assert refreshed_extraction["id"] != first_extraction["id"]
    refreshed_warning = next(
        item
        for item in ai_case.intelligence.detail(
            ai_case.actors["accountant"], event["id"]
        )["findings"]
        if item["code"] == warning_code
    )
    # The employee's OCR run cannot persist book-only warnings. The accountant
    # still sees a live derived finding, without reusing the old resolved ID.
    assert refreshed_warning["id"] != warning["id"]
    assert refreshed_warning["payload"] == warning["payload"]
    assert refreshed_warning["resolved"] is False
    assert refreshed_warning["resolution_reason"] is None
    updated = ai_case.ledger.update_draft(
        ai_case.actors["accountant"],
        ai_case.book["id"],
        applied["id"],
        DraftUpdate(
            expected_version=applied["version"],
            description=applied["description"],
            lines=applied["lines"],
        ),
    )
    expected_issue = "票据核对未完成：" + warning["payload"]["message"]
    assert expected_issue in updated["issues"]
    assert updated["version"] == applied["version"] + 1
    after_save = next(
        item
        for item in ai_case.intelligence.detail(
            ai_case.actors["accountant"], event["id"]
        )["findings"]
        if item["code"] == warning_code
    )
    assert after_save["id"] == refreshed_warning["id"]
    assert after_save["resolved"] is False
    # An independent reviewer must be blocked by this live warning too; a
    # maker's self-review restriction must not be what makes this test pass.
    with pytest.raises(AccountingError, match=expected_issue):
        ai_case.ledger.approve(
            ai_case.manager,
            ai_case.book["id"],
            applied["id"],
            ApprovalRequest(expected_version=updated["version"]),
        )
    assert draft(ai_case, applied["id"])["status"] == "draft"


def test_stale_finding_cannot_be_resolved_for_changed_evidence(ai_case):
    event = create_event(ai_case, amount="0.40")
    extract(ai_case, event)
    first = ai_case.intelligence.detail(ai_case.actors["finance"], event["id"])
    old = next(item for item in first["findings"] if item["code"] == "document_business_amount")
    ai_case.provider.document = invoice_document(amount="0.40", net_amount="0.40")
    extract(ai_case, event, key="corrected-receipt-evidence")
    with pytest.raises(AccountingError, match="当前资料"):
        ai_case.intelligence.resolve(ai_case.actors["finance"], old["id"], "旧金额差异已处理")


def test_daily_income_template_uses_seller_company_and_bank_debit(ai_case):
    event = create_event(ai_case, template="daily_cashflow", direction="income", payment_status="paid", counterparty="客户公司", category="other")
    ai_case.provider.document = invoice_document(seller_name="第一客户企业", seller_tax_id="913101TESTBUYER", buyer_name="客户公司", buyer_tax_id="CLIENT123")
    extract(ai_case, event)
    receipt = handoff(ai_case, event)
    def income_lines(output):
        output["lines"] = [{"account_code": "1002", "debit": "0.30", "credit": "0.00"}, {"account_code": "6001", "debit": "0.00", "credit": "0.30"}]
    ai_case.provider.proposal_mutator = income_lines
    suggestion = propose(ai_case, receipt["draft_id"])
    assert suggestion["status"] == "succeeded", suggestion
    applied = ai_case.intelligence.apply(ai_case.actors["accountant"], suggestion["id"], 1)
    assert applied["issues"] == [] and applied["lines"][0] == {"account_code": "1002", "debit": "0.30", "credit": "0.00", "auxiliary_values": {}}
