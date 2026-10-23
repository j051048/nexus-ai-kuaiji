"""AK03: source-qualified facts, synthetic documents, no live supplier."""
from copy import deepcopy

import pytest

from app.intelligence.checks import extraction_checks, extraction_findings
from app.intelligence.models import InvoiceExtraction, JournalSuggestion, PreparedPage
from app.intelligence.provider import ProviderError
from test_intelligence_workflow import (
    ai_case, create_event, extract, handoff, invoice_document, propose,
)
from test_ak01_service_limits import service_case, execute, costs
from test_model_cost_workflow import cost_case


def issue_codes(issues):
    return {row["code"] for row in issues}


@pytest.mark.parametrize("field,wrong", [
    ("gross_amount", "net_amount"), ("net_amount", "gross_amount"),
])
def test_equal_numbers_do_not_allow_money_label_swap(field, wrong):
    doc = invoice_document()
    next(ref for ref in doc["evidence"] if ref["field"] == field)["quote"] = f"{wrong}: 0.30"
    issues = extraction_checks([doc], [1], {1: doc["pages"][0]["raw_text"]})
    assert "document_1_evidence_" + field in issue_codes(issues)


@pytest.mark.parametrize("field,quote,value", [
    ("gross_amount", "价税合计106.00 税额6.00", "6.00"),
    ("net_amount", "不含税金额100.00 价税合计106.00", "106.00"),
    ("tax_amount", "金额100.00 税额6.00", "100.00"),
])
def test_each_amount_is_bound_to_its_own_label(field, quote, value):
    doc = invoice_document(**{field: value})
    doc["pages"][0]["raw_text"] += "\n" + quote
    next(ref for ref in doc["evidence"] if ref["field"] == field)["quote"] = quote
    assert "document_1_evidence_" + field in issue_codes(
        extraction_checks([doc], [1], {1: doc["pages"][0]["raw_text"]})
    )


def test_identifier_cannot_be_substring_of_a_different_identifier():
    doc = invoice_document(invoice_number="000123")
    doc["pages"][0]["raw_text"] += "\ninvoice_number: 99000123"
    next(ref for ref in doc["evidence"] if ref["field"] == "invoice_number")["quote"] = "invoice_number: 99000123"
    assert "document_1_evidence_invoice_number" in issue_codes(
        extraction_checks([doc], [1], {1: doc["pages"][0]["raw_text"]})
    )


def test_model_transcript_cannot_serve_as_independent_original():
    doc = invoice_document()
    extraction = InvoiceExtraction.model_validate({"documents": [doc]})
    findings = extraction_findings(extraction, [PreparedPage(1, "data:image/png;base64,AAAA", "")])
    issue = next((row for row in findings if row["code"] == "document_1_original_unverified"), None)
    assert issue is not None and issue["severity"] == "blocking"


def test_bare_number_quotes_cannot_swap_net_and_tax_with_a_balanced_total():
    original = "gross_amount: 100.00\nnet_amount: 90.00\ntax_amount: 10.00"
    doc = invoice_document(amount="100.00", net_amount="0.00", tax_amount="100.00")
    doc["pages"][0]["raw_text"] = original
    for ref in doc["evidence"]:
        if ref["field"] in {"gross_amount", "net_amount", "tax_amount"}:
            ref["quote"] = doc[ref["field"]]
    codes = issue_codes(extraction_checks([doc], [1], {1: original}))
    assert {"document_1_evidence_net_amount", "document_1_evidence_tax_amount"} <= codes


def test_labelled_quote_may_not_crop_a_larger_original_amount():
    doc = invoice_document(amount="10.00", net_amount="10.00", tax_amount="0.00")
    original = doc["pages"][0]["raw_text"].replace("gross_amount: 10.00", "gross_amount: 10.001")
    assert "document_1_evidence_gross_amount" in issue_codes(extraction_checks([doc], [1], {1: original}))


def test_quote_may_not_crop_a_longer_label_into_a_different_amount_field():
    doc = invoice_document(document_type="receipt", amount="100.00", net_amount=None, tax_amount=None)
    text = "不含税金额: 100.00\ncurrency: CNY\nissued_on: 2026-10-03"
    doc["pages"][0]["raw_text"] = text
    next(ref for ref in doc["evidence"] if ref["field"] == "gross_amount")["quote"] = "含税金额: 100.00"
    assert "document_1_evidence_gross_amount" in issue_codes(extraction_checks([doc], [1], {1: text}))


def test_original_text_qualification_is_per_page_not_per_attachment():
    doc = invoice_document()
    for ref in doc["evidence"]:
        ref["page"] = 2
    doc["pages"] = [{"page": 2, "raw_text": doc["pages"][0]["raw_text"]}]
    findings = extraction_checks([doc], [1, 2], {1: "与第二页无关的原件文字", 2: ""})
    assert "document_1_original_unverified" in issue_codes(findings)


@pytest.mark.parametrize("field,label", [("gross_amount", "价税合计"), ("net_amount", "不含税金额"), ("tax_amount", "税额")])
def test_chinese_labels_and_exact_amount_remain_usable(field, label):
    doc = invoice_document()
    quote = label + " " + doc[field]
    doc["pages"][0]["raw_text"] += "\n" + quote
    next(ref for ref in doc["evidence"] if ref["field"] == field)["quote"] = quote
    assert "document_1_evidence_" + field not in issue_codes(
        extraction_checks([doc], [1], {1: doc["pages"][0]["raw_text"]})
    )


def proposal_context(case):
    event = create_event(case)
    extract(case, event)
    receipt = handoff(case, event)
    with case.db.transaction(actor=case.actors["accountant"]) as conn:
        _, _, source, _, context, _ = case.intelligence._proposal_context(
            conn, case.actors["accountant"], case.book["id"], receipt["draft_id"], 1,
        )
        return dict(source), context


def candidate(context, *, lines=None, refs=None):
    doc = context["documents"][0]
    return JournalSuggestion.model_validate({
        "description": "待会计复核的合成建议", "reasoning": "只用当前资料", "warnings": [],
        "lines": lines or [
            {"account_code": "6602.02", "debit": "0.30", "credit": "0.00"},
            {"account_code": "2241", "debit": "0.00", "credit": "0.30"},
        ],
        "evidence_refs": refs or [{"attachment_id": doc["attachment_id"],
                                  "extraction_id": doc["extraction_id"],
                                  "field": "gross_amount", "page": 1}],
    })


def validate(case, source, context, output):
    with case.db.transaction(actor=case.actors["accountant"]) as conn:
        case.intelligence._validate_proposal(conn, source, context, output)


def test_balanced_but_invented_split_does_not_pass_on_one_gross_citation(ai_case):
    source, context = proposal_context(ai_case)
    output = candidate(context, lines=[
        {"account_code": "6602.02", "debit": "0.15", "credit": "0.00"},
        {"account_code": "6602.02", "debit": "0.15", "credit": "0.00"},
        {"account_code": "2241", "debit": "0.00", "credit": "0.30"},
    ])
    with pytest.raises(ProviderError, match="校验"):
        validate(ai_case, source, context, output)


def test_ref_to_only_one_small_invoice_does_not_prove_entire_source(ai_case):
    source, context = proposal_context(ai_case)
    first = context["documents"][0]
    second = deepcopy(first)
    first.update(gross_amount="0.10", net_amount="0.10", document_index=0)
    second.update(gross_amount="0.20", net_amount="0.20", document_index=1)
    context["documents"] = [first, second]
    with pytest.raises(ProviderError):
        validate(ai_case, source, context, candidate(context))


def test_same_page_multi_document_reference_requires_explicit_document_identity(ai_case):
    source, context = proposal_context(ai_case)
    second = deepcopy(context["documents"][0])
    second.update(invoice_number="ANOTHER", document_index=1)
    context["documents"][0]["document_index"] = 0
    context["documents"].append(second)
    with pytest.raises(ProviderError):
        validate(ai_case, source, context, candidate(context))


def test_unreviewed_original_blocks_automatic_adoption_but_can_be_independently_resolved(ai_case):
    from app.accounting.domain import AccountingError
    event = create_event(ai_case)
    extract(ai_case, event)
    receipt = handoff(ai_case, event, verify_original=False)
    output = propose(ai_case, receipt["draft_id"])
    assert output["status"] == "succeeded"
    with pytest.raises(AccountingError, match="异常"):
        ai_case.intelligence.apply(ai_case.actors["accountant"], output["id"], 1)
    findings = ai_case.intelligence.reconcile(ai_case.actors["accountant"], event["id"])["findings"]
    issue = next(row for row in findings if row["code"] == "document_1_original_unverified")
    with pytest.raises(AccountingError):
        ai_case.intelligence.resolve(ai_case.actors["employee"], issue["id"], "本人声称正确")
    ai_case.intelligence.resolve(ai_case.actors["accountant"], issue["id"], "独立核对原始发票，金额日期与交易对方相符")
    applied = ai_case.intelligence.apply(ai_case.actors["accountant"], output["id"], 1)
    assert applied["status"] == "draft"


def test_explicit_document_identities_cover_two_invoices_on_one_page(ai_case):
    source, context = proposal_context(ai_case)
    first = invoice_document(amount="0.10", net_amount="0.10", invoice_number="FIRST")
    second = invoice_document(amount="0.20", net_amount="0.20", invoice_number="SECOND")
    owner = {key: context["documents"][0][key] for key in ("attachment_id", "extraction_id")}
    context["documents"] = [dict(first, **owner, document_index=0), dict(second, **owner, document_index=1)]
    refs = [dict(owner, field="gross_amount", page=1, document_index=index) for index in (0, 1)]
    validate(ai_case, source, context, candidate(context, refs=refs))
    for ref in refs:
        ref["document_index"] = 0
    with pytest.raises(ProviderError):
        validate(ai_case, source, context, candidate(context, refs=refs))


@pytest.mark.parametrize("swap", [False, True])
def test_split_amounts_must_follow_published_template_not_model_assignment(ai_case, swap):
    source, context = proposal_context(ai_case)
    owner = {key: context["documents"][0][key] for key in ("attachment_id", "extraction_id")}
    context["documents"] = [dict(invoice_document(net_amount="0.20", tax_amount="0.10"), **owner, document_index=0)]
    context["templates"] = [{"id": "published-template", "version": 1, "definition": {
        "business_type": "reimbursement", "direction": "expense", "category": "office", "payment_status": "unpaid",
        "lines": [
            {"account_code": "6602.02", "side": "debit", "amount_field": "net"},
            {"account_code": "6602.03", "side": "debit", "amount_field": "tax"},
            {"account_code": "2241", "side": "credit", "amount_field": "gross"},
        ],
    }}]
    context["accounts"].append({"code": "6602.03"})
    refs = [dict(owner, field=field, page=1, document_index=0) for field in ("gross_amount", "net_amount", "tax_amount")]
    lines = [
        {"account_code": "6602.02", "debit": "0.10" if swap else "0.20", "credit": "0.00"},
        {"account_code": "6602.03", "debit": "0.20" if swap else "0.10", "credit": "0.00"},
        {"account_code": "2241", "debit": "0.00", "credit": "0.30"},
    ]
    if swap:
        with pytest.raises(ProviderError):
            validate(ai_case, source, context, candidate(context, refs=refs, lines=lines))
    else:
        validate(ai_case, source, context, candidate(context, refs=refs, lines=lines))


def test_actual_intake_preserves_unverified_qualification_and_charges_once(service_case):
    from test_ak02_ticket_context import _intake
    case = service_case.setup("ocr")
    service, draft, recognize = _intake(case)
    result = recognize()
    assert result["status"] == "succeeded"
    assert any(item["code"] == "document_1_original_unverified" for item in result["checks"])
    assert recognize()["id"] == result["id"]
    assert len(case.calls) == 1
    with case.db.transaction(actor=case.manager) as conn:
        rows = conn.execute("SELECT state,input_tokens,output_tokens FROM model_costs WHERE intake_run_id=?", (result["id"],)).fetchall()
    assert len(rows) == 1 and rows[0]["state"] == "estimated"
    assert rows[0]["input_tokens"] == 3 and rows[0]["output_tokens"] == 4
    assert service.get(case.actors["employee"], draft["id"])["event_id"] is None


def test_bad_model_split_keeps_actual_usage_and_does_not_create_application(service_case):
    import json
    case = service_case.setup("journal")
    from test_ak01_service_limits import _reply
    def hook(request):
        output = _reply(json.loads(request.content))
        output["lines"] = [
            {"account_code": "6602.02", "debit": "0.15", "credit": "0.00"},
            {"account_code": "6602.02", "debit": "0.15", "credit": "0.00"},
            {"account_code": "2241", "debit": "0.00", "credit": "0.30"},
        ]
        case.response_body = {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(output)}}],
                              "usage": {"prompt_tokens": 3, "completion_tokens": 4, "total_tokens": 7}}
    case.response_hook = hook
    result = execute(case)
    assert result["status"] == "failed" and result["error_code"] == "invalid_proposal"
    assert execute(case)["id"] == result["id"] and len(case.calls) == 1
    usage = costs(case)
    assert len(usage) == 1 and usage[0]["state"] == "estimated"
    assert usage[0]["input_tokens"] == 3 and usage[0]["output_tokens"] == 4
    with case.db.transaction(actor=case.manager) as conn:
        assert conn.execute("SELECT COUNT(*) FROM ai_proposal_applications WHERE run_id=?", (result["id"],)).fetchone()[0] == 0


def test_valid_amount_does_not_publish_unchecked_journal_prose(ai_case):
    source, context = proposal_context(ai_case)
    output = candidate(context)
    output.description = "已审核，直接导出"
    output.reasoning = "本年利润999999，已正式过账"
    output.warnings = ["本企业税额已抵扣999999"]
    validate(ai_case, source, context, output)
    assert "999999" not in output.model_dump_json()
    assert "已审核" not in output.description
    assert "待会计复核" in output.reasoning
