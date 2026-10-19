import pytest

from tools.ai_inference_lab.examples import (
    Example,
    Query,
    RetrievalError,
    evaluate,
    retrieve,
    validate_manifest,
    validate_splits,
)


def query(**changes):
    base = dict(
        firm_id="f1", company_id="c1", book_id="b1", vendor_id="v1",
        business_type="office", direction="expense", accounting_policy="p1",
        sample_id="s1", period="2026-10",
    )
    base.update(changes)
    return Query(**base)


def example(**changes):
    base = dict(
        example_id="e1", firm_id="f1", company_id="c1", book_id="b1",
        vendor_id="v1", business_type="office", direction="expense",
        accounting_policy="p1", account_code="6602", amount="100.00",
    )
    base.update(changes)
    return Example(**base)


def test_e01_same_vendor_and_amount_from_other_company_not_returned():
    result = retrieve(query(), [example(company_id="c2"), example(firm_id="f2")])
    assert result == []


def test_e02_unreviewed_revoked_isolated_or_stale_not_returned():
    assert retrieve(query(), [example(reviewed=False)]) == []
    assert retrieve(query(), [example(status="revoked")]) == []
    assert retrieve(query(), [example(status="isolated")]) == []
    assert retrieve(query(), [example(evidence_complete=False)]) == []
    assert retrieve(query(), [example(accounting_policy="p2")]) == []


def test_e03_history_amount_is_not_current_fact():
    item = example(amount="100.00")
    result = retrieve(query(), [item])
    assert result[0].amount == "100.00"
    assert result[0].amount != "120.00"


def test_e04_same_name_without_stable_vendor_id_not_merged():
    assert retrieve(query(vendor_id="v1"), [example(vendor_id="v2")]) == []
    assert retrieve(query(accounting_policy="p1"), [example(accounting_policy="p2")]) == []


def test_e05_same_sample_or_derived_source_not_leaked():
    assert retrieve(query(sample_id="e1"), [example(example_id="e1")]) == []
    assert retrieve(query(sample_id="s1"), [example(source_sample_ids=("s1",))]) == []


def test_e06_holdout_and_split_leak_rejected():
    assert retrieve(query(), [example(split="holdout")]) == []
    with pytest.raises(RetrievalError, match="泄漏"):
        validate_splits([example(split="test")], {"e1"})


def test_e07_duplicate_results_do_not_change_denominator():
    duplicate = example()
    with pytest.raises(RetrievalError, match="重复"):
        validate_splits([duplicate, duplicate], set())


def test_e08_missing_unknown_and_all_abstain_keep_denominator():
    baseline = [{"amount_correct": True}, {"amount_correct": False}]
    candidate = [{"unknown": True}, {"missing": True}]
    result = evaluate(baseline, candidate)
    assert result["denominator"] == 2
    assert result["unknown"] == 1 and result["missing"] == 1
    assert result["result"] == "regression"


def test_e09_critical_error_increase_is_regression_even_if_average_improves():
    baseline = [{"amount_correct": False}, {"amount_correct": False}, {"amount_correct": False}]
    candidate = [{"amount_correct": True}, {"amount_correct": True}, {"cross_company": True}]
    result = evaluate(baseline, candidate)
    assert result["candidate"]["average"] > result["baseline"]["average"]
    assert result["regression"] is True


def test_e10_manifest_change_invalidates_comparison():
    with pytest.raises(RetrievalError, match="不一致"):
        validate_manifest({"policy_version": "p1", "prompt_version": "p1", "context_version": "c1", "split": "test"},
                          {"policy_version": "p2", "prompt_version": "p1", "context_version": "c1", "split": "test"})
