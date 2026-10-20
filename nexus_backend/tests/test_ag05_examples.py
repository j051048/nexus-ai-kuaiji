from pathlib import Path

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
    baseline = [{"sample_id": "a", "amount_correct": True}, {"sample_id": "b", "amount_correct": False}]
    candidate = [{"sample_id": "a", "unknown": True}, {"sample_id": "b", "missing": True}]
    result = evaluate(baseline, candidate)
    assert result["denominator"] == 2
    assert result["unknown"] == 1 and result["missing"] == 1
    assert result["result"] == "regression"


def test_e09_critical_error_increase_is_regression_even_if_average_improves():
    baseline = [{"sample_id": "a", "amount_correct": False}, {"sample_id": "b", "amount_correct": False}, {"sample_id": "c", "amount_correct": False}]
    candidate = [{"sample_id": "a", "amount_correct": True}, {"sample_id": "b", "amount_correct": True}, {"sample_id": "c", "cross_company": True}]
    result = evaluate(baseline, candidate)
    assert result["candidate"]["average"] > result["baseline"]["average"]
    assert result["regression"] is True


def test_r2_manifest_cannot_exempt_identity_fields():
    base = {"dataset_id": "d1", "policy_version": "p1", "prompt_version": "p1",
            "context_version": "c1", "split": "test", "sample_ids": ["a"]}
    with pytest.raises(RetrievalError, match="差异"):
        validate_manifest(base, {**base, "dataset_id": "d2"}, allowed_differences={"dataset_id"})
    with pytest.raises(RetrievalError, match="不一致"):
        validate_manifest(base, {**base, "prompt_version": "p2"})
    validate_manifest(base, {**base, "prompt_version": "p2"}, allowed_differences={"prompt_version"})


def test_r1_manifest_requires_identity_even_when_both_sides_empty():
    with pytest.raises(RetrievalError, match="缺少|为空"):
        validate_manifest({}, {})


def test_r1_evaluation_uses_sample_identity_not_array_order():
    from tools.ai_inference_lab.examples import evaluate_samples
    baseline = [{"sample_id": "a", "amount_correct": True}, {"sample_id": "b", "amount_correct": False}]
    with pytest.raises(RetrievalError, match="身份"):
        evaluate_samples(baseline, [{"sample_id": "c", "amount_correct": True}, {"sample_id": "b", "amount_correct": True}])
    reordered = [{"sample_id": "b", "amount_correct": True}, {"sample_id": "a", "amount_correct": True}]
    assert evaluate_samples(baseline, reordered)["result"] == "improvement"


def test_r1_critical_regression_beats_average_improvement():
    from tools.ai_inference_lab.examples import evaluate_samples
    baseline = [{"sample_id": str(i), "amount_correct": False} for i in range(3)]
    candidate = [
        {"sample_id": "0", "amount_correct": True},
        {"sample_id": "1", "amount_correct": True},
        {"sample_id": "2", "cross_company": True},
    ]
    result = evaluate_samples(baseline, candidate)
    assert result["result"] == "regression"


def test_r1_current_fact_120_cannot_be_replaced_by_history_100():
    from tools.ai_inference_lab.answers import AnswerError, Claim, EvidenceFact, Suggestion, validate_suggestion
    from tools.ai_inference_lab.examples import retrieve

    history = example(amount="100.00")
    assert retrieve(query(), [history])[0].amount == "100.00"
    current = EvidenceFact("gross_amount", "120.00", "c1", "b1", 1, "CNY", "current")
    fake = Claim("gross_amount", "100.00", "source_fact", "c1", "b1", 1, currency="CNY", evidence_ids=("current",))
    with pytest.raises(AnswerError):
        validate_suggestion(
            Suggestion("suggestion_ready", lines=[{"account_code": "6602"}], claims=[fake]),
            company_id="c1", book_id="b1", fact_version=1, evidence=[current],
        )


def test_r4_frozen_denominator_keeps_missing_and_unknown():
    from tools.ai_inference_lab.examples import evaluate_samples
    result = evaluate_samples(
        [{"sample_id": "a", "amount_correct": True}],
        [{"sample_id": "a", "amount_correct": True}],
        required_ids={"a", "b", "c"},
    )
    assert result["denominator"] == 3
    assert result["missing"] == ["b", "c"]
    assert result["absolute_safety_failed"] is True
    assert result["result"] == "regression"


def test_r4_unknown_self_report_does_not_count_correct():
    from tools.ai_inference_lab.examples import evaluate_samples
    result = evaluate_samples(
        [{"sample_id": "a", "amount_correct": False}],
        [{"sample_id": "a", "amount_correct": True, "unknown": True}],
        required_ids={"a"},
    )
    assert result["candidate_correct"] == 0
    assert result["absolute_safety_failed"] is True


def test_r5_risk_false_positive_and_false_negative_are_separate():
    from tools.ai_inference_lab.examples import evaluate_samples
    result = evaluate_samples(
        [{"sample_id": "risk", "risk": True, "risk_escalated": True},
         {"sample_id": "normal", "risk": False, "risk_escalated": False}],
        [{"sample_id": "risk", "risk": True, "risk_escalated": False},
         {"sample_id": "normal", "risk": False, "risk_escalated": True}],
        required_ids={"risk", "normal"},
    )
    assert result["risk"]["false_negatives"] == ["risk"]
    assert result["risk"]["false_positives"] == ["normal"]


def test_r5_h01_result_level_identity_reuses_replay_validator():
    from tools.ai_inference_lab.examples import h01_validate_result
    fixture = Path(__file__).resolve().parent / "fixtures" / "ocr_replay" / "manifest.json"
    assert fixture.exists()
    with pytest.raises(RetrievalError) as invalid:
        h01_validate_result(fixture, "A1", {})
    assert invalid.value.code in {"RESULT_SHAPE", "RESULT_SCHEMA"}
    with pytest.raises(RetrievalError) as missing:
        h01_validate_result(fixture, "missing", {})
    assert missing.value.code == "h01_sample_unknown"


def test_r3_h01_manifest_adapter_reuses_frozen_identity():
    from pathlib import Path

    from tools.ai_inference_lab.examples import h01_manifest_identity

    fixture = Path(__file__).resolve().parent / "fixtures" / "ocr_replay" / "manifest.json"
    assert fixture.exists()
    identity = h01_manifest_identity(fixture)
    assert identity["dataset_id"] and len(identity["sample_ids"]) > 0
    assert set(identity["sample_ids"]) == set(identity["companies"])

    from tools.ai_inference_lab.examples import evaluate_samples, evaluation_report
    baseline = [{"sample_id": "a", "amount_correct": True}, {"sample_id": "b", "amount_correct": False}]
    candidate = [{"sample_id": "a", "amount_correct": True}, {"sample_id": "b", "cross_company": True}]
    report = evaluation_report(evaluate_samples(baseline, candidate))
    assert '"sample_id": "b"' in report["json"]
    assert "关键退步" in report["markdown"]
    assert "b" in report["markdown"]


def test_e10_manifest_change_invalidates_comparison():
    base = {"dataset_id": "d1", "policy_version": "p1", "prompt_version": "p1",
            "context_version": "c1", "split": "test", "sample_ids": ["a"]}
    with pytest.raises(RetrievalError, match="不一致"):
        validate_manifest(base, {**base, "policy_version": "p2"})
