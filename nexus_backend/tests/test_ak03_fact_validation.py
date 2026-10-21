"""AK03: claims are proofs against current evidence, not model self-consistency."""
from pathlib import Path
from decimal import Decimal
import subprocess
import sys

import pytest

from tools.ai_inference_lab import answers as facts


def source(field="gross_amount", value="100.00", evidence_id="gross", **kwargs):
    return facts.EvidenceFact(field, value, "c", "b", 2, "CNY", evidence_id, **kwargs)


def claim(**changes):
    values = dict(field="gross_amount", value="100.00", kind="source_fact", company_id="c", book_id="b", evidence_version=2, currency="CNY", evidence_ids=("gross",))
    values.update(changes)
    return facts.Claim(**values)


def verify(claims, evidence=None, **kwargs):
    return facts.validate_suggestion(
        facts.Suggestion(status="suggestion_ready", lines=[{"account_code": "6602"}], claims=claims),
        company_id="c", book_id="b", fact_version=2, evidence=[source()] if evidence is None else evidence,
        **kwargs,
    )


def bound_calculation(**changes):
    values = dict(field="net_amount", value="80.00", kind="calculation", evidence_ids=("gross", "tax"), calculation={
        "operator": "-", "inputs": [{"evidence_id": "gross", "field": "gross_amount"}, {"evidence_id": "tax", "field": "tax_amount"}],
    })
    values.update(changes)
    return claim(**values)


def test_self_reported_arithmetic_does_not_prove_financial_facts():
    forged = claim(field="net_amount", value="80.00", kind="calculation", calculation={"operator": "-", "inputs": ["100.00", "20.00"]})
    with pytest.raises(facts.AnswerError):
        verify([forged])


def test_bound_calculation_reads_all_operands_from_current_evidence():
    assert verify([bound_calculation()], [source(), source("tax_amount", "20.00", "tax")]).status == "suggestion_ready"


@pytest.mark.parametrize("change", ["unknown", "scope", "book", "version", "currency", "field", "references"])
def test_each_calculation_operand_is_bound(change):
    evidence = [source(), source("tax_amount", "20.00", "tax")]
    current = bound_calculation()
    if change == "unknown":
        evidence.pop()
    elif change in {"scope", "book", "version", "currency", "field"}:
        values = vars(evidence[-1]).copy()
        values[{"scope": "company_id", "book": "book_id", "version": "version", "currency": "currency", "field": "field"}[change]] = {"scope": "other", "book": "other", "version": 1, "currency": "USD", "field": "gross_amount"}[change]
        evidence[-1] = facts.EvidenceFact(**values)
    else:
        current = bound_calculation(evidence_ids=("gross",))
    with pytest.raises(facts.AnswerError):
        verify([current], evidence)


@pytest.mark.parametrize("kind", ["inference", "unknown"])
def test_one_valid_fact_does_not_make_unknown_or_inference_ready(kind):
    with pytest.raises(facts.AnswerError):
        verify([claim(), claim(field="net_amount", kind=kind, value=None if kind == "unknown" else "80.00", evidence_ids=())])


@pytest.mark.parametrize("bad", [True, False, 100.0, float("nan"), float("inf"), "NaN", "Infinity", "1e500", "1000000000000001", "0.123456789"])
def test_monetary_values_reject_lossy_or_unbounded_values(bad):
    with pytest.raises(facts.AnswerError):
        verify([claim(value=bad)], [source(value=str(bad))])


def test_duplicate_evidence_identity_cannot_overwrite_another_value():
    with pytest.raises(facts.AnswerError):
        verify([claim()], [source(value="99.00"), source()])


def test_identical_evidence_duplicate_is_still_one_proof():
    assert verify([claim()], [source(), source()]).status == "suggestion_ready"


@pytest.mark.parametrize("operator,inputs,value", [
    ("+", ["100.00", "0.00"], "100.00"),
    ("*", ["100.00", "1"], "100.00"),
])
def test_source_gross_total_cannot_be_laundered_through_calculation(operator, inputs, value):
    # Old implementation accepts this arithmetic without reading the evidence.
    bad = claim(field="revenue", value=value, kind="calculation", evidence_ids=("gross",), calculation={"operator": operator, "inputs": inputs})
    with pytest.raises(facts.AnswerError):
        verify([bad], [source(field="source_gross_total")])


def test_bound_source_gross_total_cannot_be_relabelled_after_adding_zero():
    bad = bound_calculation(field="profit", value="100.00", calculation={"operator": "+", "inputs": [
        {"evidence_id": "gross", "field": "source_gross_total"}, {"evidence_id": "tax", "field": "tax_amount"},
    ]})
    with pytest.raises(facts.AnswerError):
        verify([bad], [source(field="source_gross_total"), source("tax_amount", "0.00", "tax")])


@pytest.mark.parametrize("target", ["profit", "revenue", "operating_revenue", "net_profit", "made_up_total"])
def test_bound_arithmetic_does_not_grant_undeclared_financial_meaning(target):
    bad = bound_calculation(field=target, value="120.00", calculation={"operator": "+", "inputs": [
        {"evidence_id": "gross", "field": "gross_amount"}, {"evidence_id": "tax", "field": "tax_amount"},
    ]})
    with pytest.raises(facts.AnswerError):
        verify([bad], [source(), source("tax_amount", "20.00", "tax")])


def test_financial_target_must_match_the_declared_operation_and_input_fields():
    bad = bound_calculation(value="120.00", calculation={"operator": "+", "inputs": [
        {"evidence_id": "gross", "field": "gross_amount"}, {"evidence_id": "tax", "field": "tax_amount"},
    ]})
    with pytest.raises(facts.AnswerError):
        verify([bad], [source(), source("tax_amount", "20.00", "tax")])


@pytest.mark.parametrize("target,operator,left_field,left_value,right_field,right_value,expected", [
    ("net_amount", "-", "gross_amount", "100.00", "tax_amount", "20.00", "80.00"),
    ("gross_amount", "+", "net_amount", "80.00", "tax_amount", "20.00", "100.00"),
    ("gross_amount", "+", "tax_amount", "20.00", "net_amount", "80.00", "100.00"),
    ("tax_amount", "-", "gross_amount", "100.00", "net_amount", "80.00", "20.00"),
])
def test_only_declared_financial_derivations_are_available_by_default(target, operator, left_field, left_value, right_field, right_value, expected):
    current = claim(field=target, value=expected, kind="calculation", evidence_ids=("left", "right"), calculation={
        "operator": operator, "inputs": [{"evidence_id": "left", "field": left_field}, {"evidence_id": "right", "field": right_field}],
    })
    assert verify([current], [source(left_field, left_value, "left"), source(right_field, right_value, "right")]).status == "suggestion_ready"


@pytest.mark.parametrize("target", ["revenue", "profit", "net_amount"])
def test_extra_calculator_rules_cannot_open_income_or_override_amount_relationships(target):
    bad = bound_calculation(field=target, value="120.00", calculation={"operator": "+", "inputs": [
        {"evidence_id": "gross", "field": "gross_amount"}, {"evidence_id": "tax", "field": "tax_amount"},
    ]})
    with pytest.raises(facts.AnswerError):
        verify([bad], [source(), source("tax_amount", "20.00", "tax")], calculation_rules=(facts.CalculationRule(target, "+", ("gross_amount", "tax_amount")),))


def test_model_cannot_declare_a_rule_in_its_calculation_payload():
    calculation = {"operator": "+", "inputs": [
        {"evidence_id": "gross", "field": "gross_amount"}, {"evidence_id": "tax", "field": "tax_amount"},
    ], "rule": {"target": "profit", "operator": "+", "input_fields": ["gross_amount", "tax_amount"]}}
    with pytest.raises(facts.AnswerError):
        verify([bound_calculation(field="profit", value="120.00", calculation=calculation)], [source(), source("tax_amount", "20.00", "tax")])


def test_unsupported_expression_and_self_reported_input_values_are_rejected():
    for calculation in (
        {"operator": "+", "inputs": ["100", "20"], "expression": "__import__('os')"},
        {"operator": "eval", "inputs": []},
        {"operator": "+", "inputs": [{"evidence_id": "gross", "field": "gross_amount", "value": "100"}, {"evidence_id": "tax", "field": "tax_amount"}]},
    ):
        with pytest.raises(facts.AnswerError):
            verify([bound_calculation(value="120", calculation=calculation)], [source(), source("tax_amount", "20", "tax")])


def test_claim_unit_and_calculation_operand_units_must_match():
    with pytest.raises(facts.AnswerError):
        verify([claim(unit="分")], [source(unit="元")])
    with pytest.raises(facts.AnswerError):
        verify([bound_calculation(unit="元")], [source(unit="元"), source("tax_amount", "20", "tax", unit="分")])


def test_book_none_and_individual_evidence_versions_are_explicit():
    current = facts.Claim("amount", "12.00", "source_fact", "c", None, 4, "元", "CNY", ("event:v4",))
    proof = facts.EvidenceFact("amount", "12.00", "c", None, 4, "CNY", "event:v4", unit="元", value_type="decimal")
    result = facts.validate_bound_claims([current], company_id="c", book_id=None, fact_version=None, evidence=[proof], require_verified=True)
    assert result == [current]
    with pytest.raises(facts.AnswerError):
        facts.validate_bound_claims([current], company_id="c", book_id=None, fact_version=5, evidence=[proof], require_verified=True)


def test_text_identifiers_preserve_leading_zeroes():
    proof = facts.EvidenceFact("invoice_number", "000123", "c", "b", 2, "", "number", value_type="text")
    current = claim(field="invoice_number", value="000123", currency="", evidence_ids=("number",))
    assert verify([current], [proof]).status == "suggestion_ready"
    with pytest.raises(facts.AnswerError):
        verify([claim(field="invoice_number", value="123", currency="", evidence_ids=("number",))], [proof])


def test_strict_versions_and_ambiguous_claim_shape_rejected():
    for current in (claim(evidence_version=True), claim(calculation={"operator": "+", "inputs": [1, 2]}), claim(evidence_ids="gross")):
        with pytest.raises(facts.AnswerError):
            verify([current])


def test_bad_nonready_still_cannot_carry_applicable_lines():
    with pytest.raises(facts.AnswerError):
        facts.validate_suggestion(facts.Suggestion(status="requires_accountant", lines=[{"account_code": "1002"}]), company_id="c", book_id="b", fact_version=2, evidence=[])


@pytest.mark.parametrize("operator,left,right,expected", [
    ("+", "0.10", "0.20", "0.30"), ("-", "100.00", "20.00", "80.00"),
    ("*", "100.00", "0.13", "13.00"), ("/", "100.00", "2", "50.00"),
])
def test_four_fixed_operations_use_exact_current_operands(operator, left, right, expected):
    second_currency = "CNY" if operator in {"+", "-"} else ""
    evidence = [source(value=left, unit="元"), facts.EvidenceFact("tax_amount", right, "c", "b", 2, second_currency, "tax", unit="元" if second_currency else "", value_type="decimal")]
    current = bound_calculation(field="result", value=expected, unit="元", calculation={"operator": operator, "inputs": [
        {"evidence_id": "gross", "field": "gross_amount"}, {"evidence_id": "tax", "field": "tax_amount"},
    ]})
    allowed = (facts.CalculationRule("result", operator, ("gross_amount", "tax_amount")),)
    assert verify([current], evidence, calculation_rules=allowed).status == "suggestion_ready"


@pytest.mark.parametrize("denominator", ["0", "3"])
def test_division_does_not_hide_zero_or_inexact_rounding(denominator):
    evidence = [source(), facts.EvidenceFact("tax_amount", denominator, "c", "b", 2, "", "tax", value_type="decimal")]
    current = bound_calculation(field="result", value="33.33", calculation={"operator": "/", "inputs": [
        {"evidence_id": "gross", "field": "gross_amount"}, {"evidence_id": "tax", "field": "tax_amount"},
    ]})
    with pytest.raises(facts.AnswerError):
        verify([current], evidence, calculation_rules=(facts.CalculationRule("result", "/", ("gross_amount", "tax_amount")),))


def test_money_times_money_cannot_keep_money_units():
    with pytest.raises(facts.AnswerError):
        verify([bound_calculation(field="result", value="2000.00", calculation={"operator": "*", "inputs": [
            {"evidence_id": "gross", "field": "gross_amount"}, {"evidence_id": "tax", "field": "tax_amount"},
        ]})], [source(), source("tax_amount", "20.00", "tax")], calculation_rules=(facts.CalculationRule("result", "*", ("gross_amount", "tax_amount")),))


def test_extreme_decimal_object_is_a_controlled_validation_failure():
    huge = Decimal("1e1000000")
    with pytest.raises(facts.AnswerError):
        verify([claim(value=huge)], [source(value=huge)])


def test_nonready_unknown_stays_unverified_and_has_no_applicable_lines():
    current = claim(value=None, kind="unknown", evidence_ids=())
    result = facts.validate_suggestion(facts.Suggestion(status="requires_accountant", claims=[current]), company_id="c", book_id="b", fact_version=2, evidence=[])
    assert result.lines == []
    assert facts.to_offline_handoff(result)["requires_accountant"] is True


def test_one_implementation_and_backend_only_import():
    from app.intelligence import fact_validation

    assert facts.Claim is fact_validation.Claim
    root = Path(__file__).resolve().parents[2]
    code = (
        "import sys; " + f"sys.path.insert(0, {str(root)!r}); "
        "from tools.ai_inference_lab import answers; "
        f"sys.path.insert(0, {str(root / 'nexus_backend')!r}); "
        "from app.intelligence import fact_validation; "
        "assert answers.Claim is fact_validation.Claim"
    )
    result = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
