import pytest

from tools.ai_inference_lab.answers import (
    AnswerError,
    Claim,
    EvidenceFact,
    Suggestion,
    load_suggestion,
    to_offline_handoff,
    validate_suggestion,
)


def source(**changes):
    base = dict(
        company_id="c1", book_id="b1", fact_version=3,
        evidence=[EvidenceFact("gross_amount", "100.00", "c1", "b1", 3, "CNY", "e1")],
    )
    base.update(changes)
    return base


def claim(**changes):
    base = dict(
        field="gross_amount", value="100.00", kind="source_fact",
        company_id="c1", book_id="b1", evidence_version=3,
        currency="CNY", evidence_ids=("e1",),
    )
    base.update(changes)
    return Claim(**base)


def test_c01_missing_tax_and_payment_can_be_nonready_without_lines():
    result = Suggestion(
        status="missing_facts",
        missing_facts=["tax_amount", "payment_relation"],
        claims=[claim(field="gross_amount")],
    )
    assert validate_suggestion(result, **source()).lines == []
    assert to_offline_handoff(result)["kind"] == "offline_accountant_handoff"


def test_c02_contradictory_status_and_lines_rejected():
    with pytest.raises(AnswerError, match="非 ready"):
        validate_suggestion(
            Suggestion(status="missing_facts", lines=[{"account_code": "6602"}], missing_facts=["tax"]),
            **source(),
        )
    with pytest.raises(AnswerError, match="ready"):
        validate_suggestion(Suggestion(status="suggestion_ready"), **source())
    with pytest.raises(AnswerError, match="冲突"):
        validate_suggestion(Suggestion(status="conflicting_evidence", conflicts=[]), **source())


def test_c03_wrong_amount_field_currency_or_version_not_verified():
    for bad in (
        claim(value="99.00"),
        claim(field="tax_amount"),
        claim(currency="USD"),
        claim(evidence_version=2),
    ):
        with pytest.raises(AnswerError):
            validate_suggestion(
                Suggestion(status="suggestion_ready", lines=[{"account_code": "6602"}], claims=[bad]),
                **source(),
            )


def test_c04_same_number_in_other_column_is_not_proof():
    evidence = [EvidenceFact("total_amount", "100.00", "c1", "b1", 3, "CNY", "e1")]
    with pytest.raises(AnswerError, match="字段名"):
        validate_suggestion(
            Suggestion(status="suggestion_ready", lines=[{"account_code": "6602"}], claims=[claim(field="gross_amount")]),
            **source(evidence=evidence),
        )


def test_c05_source_gross_total_cannot_be_relabelled_as_revenue():
    evidence = [EvidenceFact("source_gross_total", "100.00", "c1", "b1", 3, "CNY", "e1")]
    bad = claim(field="revenue", value="100.00")
    with pytest.raises(AnswerError, match="收入"):
        validate_suggestion(
            Suggestion(status="suggestion_ready", lines=[{"account_code": "1002"}], claims=[bad]),
            **source(evidence=evidence),
        )


def test_c06_calculation_is_program_checked_and_inference_is_not_fact():
    calculation = {"operator": "-", "inputs": [
        {"evidence_id": "e1", "field": "gross_amount"},
        {"evidence_id": "tax", "field": "tax_amount"},
    ]}
    proof = source(evidence=[
        EvidenceFact("gross_amount", "100.00", "c1", "b1", 3, "CNY", "e1"),
        EvidenceFact("tax_amount", "20.00", "c1", "b1", 3, "CNY", "tax"),
    ])
    calc = claim(
        field="net_amount", value="80.00", kind="calculation", evidence_ids=("e1", "tax"), calculation=calculation
    )
    result = Suggestion(status="suggestion_ready", lines=[{"account_code": "6602"}], claims=[calc])
    assert validate_suggestion(result, **proof).status == "suggestion_ready"
    bad = claim(
        field="net_amount", value="81.00", kind="calculation", evidence_ids=("e1", "tax"), calculation=calculation
    )
    with pytest.raises(AnswerError, match="计算"):
        validate_suggestion(
            Suggestion(status="suggestion_ready", lines=[{"account_code": "6602"}], claims=[bad]), **proof
        )


def test_c07_cross_company_or_stale_ocr_reference_rejected():
    with pytest.raises(AnswerError, match="跨企业"):
        validate_suggestion(
            Suggestion(status="suggestion_ready", lines=[{"account_code": "6602"}], claims=[claim(company_id="c2")]),
            **source(),
        )
    with pytest.raises(AnswerError, match="版本"):
        validate_suggestion(
            Suggestion(status="suggestion_ready", lines=[{"account_code": "6602"}], claims=[claim(evidence_version=2)]),
            **source(fact_version=4),
        )


def test_c08_old_json_readable_but_unknown_version_rejected():
    payload = Suggestion(status="requires_accountant", warnings=["旧结构"]).as_dict()
    assert load_suggestion(payload).status == "requires_accountant"
    payload["version"] = "suggestion-v0"
    with pytest.raises(AnswerError, match="版本"):
        load_suggestion(payload)


def test_c09_nonready_becomes_offline_handoff_only():
    result = Suggestion(status="unsupported_case", warnings=["无适用模板"])
    handoff = to_offline_handoff(result)
    assert handoff["requires_accountant"] is True
    assert "lines" not in handoff
    with pytest.raises(AnswerError):
        to_offline_handoff(Suggestion(status="suggestion_ready", lines=[{"account_code": "6602"}]))
