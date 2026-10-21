from pathlib import Path
from copy import deepcopy
from dataclasses import replace
import json
import shutil

import pytest

from tools.ai_inference_lab.examples import (
    Example,
    Query,
    RetrievalError,
    evaluate,
    retrieve,
    validate_manifest,
    validate_splits,
    CandidateConfig,
    EvaluationSpec,
    UnknownAttempt,
    evaluate_samples,
    evaluation_report,
)
from tools.ocr_replay import replay


def query(**changes):
    base = dict(
        firm_id="f1", company_id="c1", book_id="b1", vendor_id="v1",
        business_type="office", direction="expense", accounting_policy="p1",
        sample_id="s1", period="2026-10", source_hash="1" * 64,
    )
    base.update(changes)
    return Query(**base)


def example(**changes):
    base = dict(
        example_id="e1", firm_id="f1", company_id="c1", book_id="b1",
        vendor_id="v1", business_type="office", direction="expense",
        accounting_policy="p1", account_code="6602", amount="100.00",
        source_hash="2" * 64, historical_period="2026-09",
        policy_effective_from="2026-01",
    )
    base.update(changes)
    return Example(**base)


@pytest.fixture
def frozen(tmp_path):
    """Independent synthetic dataset; production and shared H01 files stay read-only."""
    original = Path(__file__).parent / "fixtures" / "ocr_replay"
    root = tmp_path / "ag05_dataset"
    shutil.copytree(original, root)
    path = root / "manifest.json"
    raw = replay.read_json(path)
    for sample in raw["samples"]:
        gold_path = root / sample["gold_path"]
        gold = replay.read_json(gold_path)
        gold["evaluation"] = {
            "risk_required": sample["sample_id"] in {"A2", "B1"},
            "abstention_expected": sample["sample_id"] == "B1",
        }
        gold_path.write_text(json.dumps(gold, ensure_ascii=False), encoding="utf-8")
        sample["gold_sha256"] = replay.sha256_path(gold_path)
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    baseline = CandidateConfig("baseline", "baseline-v1", "extract baseline", "context-1")
    candidate = CandidateConfig("candidate", "candidate-v1", "extract candidate", "context-2")
    spec = EvaluationSpec(path, replay.sha256_path(path), baseline, candidate)
    manifest = replay.load_manifest(path)

    def rows(config):
        result = []
        for sample in manifest["samples"]:
            gold = replay.read_json(Path(sample["gold_path"]))
            abstain = gold["evaluation"]["abstention_expected"]
            fields = {} if abstain else deepcopy(sample["gold_fields"])
            evidence = []
            for field, value in fields.items():
                if value is None:
                    continue
                page = next(page for page in sample["ocr_pages"] if replay._quote_supports(field, value, page["raw_text"]))
                evidence.append({"field": field, "page": page["page"], "quote": page["raw_text"]})
            result.append({
                "schema_version": replay.SCHEMA,
                **{key: sample[key] for key in ("sample_id", "firm_id", "company_id", "ledger_id", "book_id", "period")},
                "manifest_sha256": spec.manifest_sha256,
                "ocr_sha256": sample["ocr_sha256"].lower(),
                "input_hash": sample["ocr_sha256"].lower(),
                "gold_sha256": sample["gold_sha256"].lower(),
                "source_hash": sample["source_hash"],
                "candidate_fingerprint": config.fingerprint,
                "request_fingerprint": replay.expected_request_fingerprint(sample, config),
                "policy_version": config.policy_version,
                "policy_prompt": config.policy_prompt,
                "status": "succeeded",
                "review_required": gold["evaluation"]["risk_required"],
                "fields": fields, "evidence": evidence,
                **({"decision": {"kind": "abstain", "reason": "unsupported_case"}} if abstain else {}),
            })
        return result
    return spec, rows(baseline), rows(candidate)


def by_id(rows, sid):
    return next(row for row in rows if row["sample_id"] == sid)


def unknown(row):
    return UnknownAttempt(
        sample_id=row["sample_id"], attempt_id="attempt-1",
        **{key: row[key] for key in (
            "manifest_sha256", "candidate_fingerprint", "input_hash",
            "gold_sha256", "source_hash", "request_fingerprint",
        )},
    )


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


def test_e08_missing_unknown_and_all_abstain_keep_denominator(frozen):
    spec, baseline, candidate = frozen
    result = evaluate(baseline, [unknown(by_id(candidate, "A1")), by_id(candidate, "B1")], spec=spec)
    assert result["denominator"] == 4
    assert result["unknown"] == ["A1"] and result["unknown_count"] == 1
    assert result["missing"] == ["A2", "A3"] and result["missing_count"] == 2
    assert result["result"] == "not_comparable"
    assert result["acceptance"] == "incomplete"


def test_e09_critical_error_increase_is_regression_even_if_average_improves(frozen):
    spec, baseline, candidate = frozen
    by_id(baseline, "A1")["fields"]["gross_amount"] = "999.00"
    by_id(baseline, "A2")["fields"]["gross_amount"] = "999.00"
    by_id(candidate, "A3")["fields"]["invoice_number"] = "999"
    result = evaluate(baseline, candidate, spec=spec)
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


def test_r1_evaluation_uses_sample_identity_not_array_order(frozen):
    spec, baseline, candidate = frozen
    assert evaluate_samples(baseline, list(reversed(candidate)), spec=spec)["acceptance"] == "accepted_offline"
    candidate[0]["sample_id"] = "OTHER"
    with pytest.raises(RetrievalError) as caught:
        evaluate_samples(baseline, candidate, spec=spec)
    assert caught.value.code == "evaluation_identity_mismatch"


def test_r1_critical_regression_beats_average_improvement(frozen):
    spec, baseline, candidate = frozen
    by_id(baseline, "A1")["review_required"] = True
    by_id(baseline, "A3")["review_required"] = True
    by_id(candidate, "A2")["review_required"] = False
    result = evaluate_samples(baseline, candidate, spec=spec)
    assert result["result"] == "regression"
    assert result["absolute_safety_failed"] is True


def test_r1_current_fact_120_cannot_be_replaced_by_history_100():
    from tools.ai_inference_lab.answers import AnswerError, Claim, Suggestion
    from tools.ai_inference_lab.context import Evidence, Fact
    from tools.ai_inference_lab.examples import compile_example_context, validate_context_suggestion
    amount = Evidence("a", "x", "d", 1, "gross_amount", "120.00", version=2)
    currency = Evidence("a", "x", "d", 1, "currency", "CNY", version=2)
    pack = compile_example_context(
        query(), [example(amount="100.00")],
        current_facts=[
            Fact("gross_amount", "120.00", 2, "approved_fact", amount.identity),
            Fact("currency", "CNY", 2, "approved_fact", currency.identity),
        ], current_evidence=[amount, currency],
        budget={"input_tokens": 4000, "output_reserve": 200},
        prompt_version="prompt-1", schema_version="suggestion-v1",
    )
    assert pack.scope["company_id"] == "c1" and pack.fact_version == 2
    assert any(row["value"] == "100.00" and row["source"] == "history" and row["key"].startswith("history:") for row in pack.facts)
    assert next(row for row in pack.facts if row["key"] == "gross_amount")["value"] == "120.00"
    claim = Claim("gross_amount", "120.00", "source_fact", "c1", "b1", 2, currency="CNY", evidence_ids=(amount.identity,))
    valid = Suggestion("suggestion_ready", lines=[{"account_code": "6602"}], claims=[claim])
    assert validate_context_suggestion(pack, valid) is valid
    with pytest.raises(AnswerError) as caught:
        validate_context_suggestion(pack, replace(valid, claims=[replace(claim, value="100.00")]))
    assert caught.value.code == "claim_value_mismatch"


def test_r5_compat_entry_report_can_render_missing_ids(frozen):
    spec, baseline, candidate = frozen
    result = evaluate(baseline, candidate, spec=spec)
    report = evaluation_report(result)
    assert '"missing"' in report["json"]
    assert "缺点数：0" in report["markdown"]


def test_r4_manifest_rejects_wrong_types_and_unknown_split():
    base = {"dataset_id": "d1", "policy_version": "p1", "prompt_version": "p1",
            "context_version": "c1", "split": "test", "sample_ids": ["a"]}
    with pytest.raises(RetrievalError, match="split"):
        validate_manifest({**base, "split": "mystery"}, base)
    with pytest.raises(RetrievalError, match="字符串"):
        validate_manifest({**base, "dataset_id": True}, base)
    with pytest.raises(RetrievalError, match="重复"):
        validate_manifest({**base, "sample_ids": ["a", "a"]}, base)


def test_r4_candidate_wrong_scope_rejected_even_with_valid_ids(frozen):
    spec, baseline, candidate = frozen
    candidate[0]["company_id"] = "OTHER"
    with pytest.raises(RetrievalError) as caught:
        evaluate_samples(baseline, candidate, spec=spec)
    assert caught.value.code == "RESULT_SCOPE"


def test_r4_frozen_denominator_keeps_missing_and_unknown(frozen):
    spec, baseline, candidate = frozen
    result = evaluate_samples(baseline[:1], candidate[:1], spec=spec)
    assert result["denominator"] == 4
    assert result["missing"] == ["A2", "A3", "B1"]
    assert result["baseline"]["missing"] == ["A2", "A3", "B1"]
    assert result["absolute_safety_failed"] is True
    assert result["result"] == "not_comparable"


def test_r4_unknown_self_report_does_not_count_correct(frozen):
    spec, baseline, candidate = frozen
    result = evaluate_samples(baseline, [unknown(row) for row in candidate], spec=spec)
    assert result["candidate_correct"] == 0
    assert result["absolute_safety_failed"] is True
    candidate[0]["status"] = "unknown"
    candidate[0]["amount_correct"] = True
    with pytest.raises(RetrievalError) as caught:
        evaluate_samples(baseline, candidate, spec=spec)
    assert caught.value.code == "RESULT_UNKNOWN"


def test_r5_risk_false_positive_and_false_negative_are_separate(frozen):
    spec, baseline, candidate = frozen
    by_id(candidate, "A2")["review_required"] = False
    by_id(candidate, "A1")["review_required"] = True
    result = evaluate_samples(baseline, candidate, spec=spec)
    assert result["risk"]["fn"] == ["A2"]
    assert result["risk"]["fp"] == ["A1"]
    assert result["risk"]["tn"] == ["A3"]
    assert result["risk"]["tp"] == ["B1"]
    assert result["risk"]["correct_abstentions"] == ["B1"]
    assert result["acceptance"] == "rejected"


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


def test_r4_report_keeps_missing_reason_and_result_code(frozen):
    spec, baseline, candidate = frozen
    report = evaluation_report(evaluate_samples(baseline, candidate[1:], spec=spec))
    assert '"missing"' in report["json"]
    assert '"result_code": "incomplete"' in report["json"]
    assert "缺点数：1" in report["markdown"]
    assert "A1" in report["markdown"] and "missing" in report["markdown"]


def test_r3_h01_manifest_adapter_reuses_frozen_identity(frozen):
    from pathlib import Path

    from tools.ai_inference_lab.examples import h01_manifest_identity

    fixture = Path(__file__).resolve().parent / "fixtures" / "ocr_replay" / "manifest.json"
    assert fixture.exists()
    identity = h01_manifest_identity(fixture)
    assert identity["dataset_id"] and len(identity["sample_ids"]) > 0
    assert set(identity["sample_ids"]) == set(identity["companies"])

    spec, baseline, candidate = frozen
    report = evaluation_report(evaluate_samples(baseline, candidate, spec=spec))
    assert '"sample_id": "B1"' in report["json"]
    assert "correct_abstention" in report["markdown"]


def test_e10_manifest_change_invalidates_comparison():
    base = {"dataset_id": "d1", "policy_version": "p1", "prompt_version": "p1",
            "context_version": "c1", "split": "test", "sample_ids": ["a"]}
    with pytest.raises(RetrievalError, match="不一致"):
        validate_manifest(base, {**base, "policy_version": "p2"})


@pytest.mark.parametrize("entry", ["evaluate", "evaluate_samples"])
def test_r5_unbound_self_report_cannot_be_scored(entry):
    from tools.ai_inference_lab import examples as module

    with pytest.raises(RetrievalError) as caught:
        getattr(module, entry)(
            [{"sample_id": "a", "amount_correct": False}],
            [{"sample_id": "a", "company_id": "OTHER", "amount_correct": "yes"}],
        )
    assert caught.value.code == "evaluation_spec_required"


@pytest.mark.parametrize("split", ["test", "validation", "mystery", "dev"])
def test_r5_retrieval_excludes_every_non_training_split(split):
    assert retrieve(query(), [example(split=split)]) == []


def test_r5_retrieval_corpus_checks_derived_holdout():
    with pytest.raises(RetrievalError) as caught:
        validate_splits([example(source_sample_ids=("held",))], {"held"})
    assert caught.value.code == "example_leak"


def test_r5_reports_have_complete_sample_reasons_not_only_aggregates():
    from tools.ai_inference_lab.examples import evaluate_samples, evaluation_report

    # A raw boolean-only comparison must not create a report for a financial candidate.
    with pytest.raises(RetrievalError):
        evaluation_report(evaluate_samples(
            [{"sample_id": "a", "amount_correct": False}],
            [{"sample_id": "a", "amount_correct": True, "risk": False}],
        ))


@pytest.mark.parametrize("field,value", [
    ("company_id", "OTHER"), ("firm_id", "OTHER"), ("book_id", "OTHER"),
    ("ledger_id", "OTHER"), ("period", "2027-01"),
    ("input_hash", "0" * 64), ("ocr_sha256", "0" * 64),
    ("gold_sha256", "0" * 64), ("source_hash", "OTHER"),
    ("manifest_sha256", "0" * 64), ("candidate_fingerprint", "0" * 64),
    ("request_fingerprint", "0" * 64), ("policy_version", "OTHER"),
    ("policy_prompt", "OTHER"),
])
def test_r5_every_scope_source_and_candidate_binding_is_mandatory(frozen, field, value):
    spec, baseline, candidate = frozen
    candidate[0][field] = value
    with pytest.raises(RetrievalError) as caught:
        evaluate_samples(baseline, candidate, spec=spec)
    assert caught.value.code in {"evaluation_binding_mismatch", "RESULT_SCOPE", "RESULT_INPUT", "RESULT_POLICY"}


def test_r5_both_public_entries_must_invoke_h01(frozen, monkeypatch):
    spec, baseline, candidate = frozen
    calls = []
    original = replay.validate_result_identity

    def checked(manifest, sample, row, policy=None):
        calls.append((sample["sample_id"], policy.fingerprint))
        return original(manifest, sample, row, policy)
    monkeypatch.setattr(replay, "validate_result_identity", checked)
    for entry in (evaluate, evaluate_samples):
        entry(baseline, candidate, spec=spec)
    assert len(calls) == 16
    assert {fingerprint for _, fingerprint in calls} == {spec.baseline.fingerprint, spec.candidate.fingerprint}


@pytest.mark.parametrize("kind", ["manifest", "ocr", "gold", "empty"])
def test_r5_frozen_input_tampering_is_rejected(frozen, kind):
    spec, baseline, candidate = frozen
    data = replay.read_json(spec.manifest_path)
    if kind == "manifest":
        data["dataset_version"] = "changed"
        spec.manifest_path.write_text(json.dumps(data), encoding="utf-8")
    elif kind == "empty":
        data["samples"] = []
        spec.manifest_path.write_text(json.dumps(data), encoding="utf-8")
        spec = replace(spec, manifest_sha256=replay.sha256_path(spec.manifest_path))
    else:
        path = spec.manifest_path.parent / data["samples"][0][f"{kind}_path"]
        path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(RetrievalError) as caught:
        evaluate_samples(baseline, candidate, spec=spec)
    assert caught.value.code in {"evaluation_manifest_changed", "OCR_HASH", "GOLD_HASH", "SAMPLE_LIMIT"}


def test_r5_missing_risk_gold_is_not_inferred_from_candidate(frozen):
    spec, baseline, candidate = frozen
    data = replay.read_json(spec.manifest_path)
    sample = data["samples"][0]
    path = spec.manifest_path.parent / sample["gold_path"]
    gold = replay.read_json(path)
    del gold["evaluation"]
    path.write_text(json.dumps(gold), encoding="utf-8")
    sample["gold_sha256"] = replay.sha256_path(path)
    spec.manifest_path.write_text(json.dumps(data), encoding="utf-8")
    spec = replace(spec, manifest_sha256=replay.sha256_path(spec.manifest_path))
    with pytest.raises(RetrievalError) as caught:
        evaluate_samples(baseline, candidate, spec=spec)
    assert caught.value.code == "evaluation_gold_invalid"


def test_r5_candidate_flags_cannot_change_gold_scoring(frozen):
    spec, baseline, candidate = frozen
    by_id(candidate, "A2").update(
        risk=False, risk_escalated=True, cross_company=False,
        amount_correct="yes", evidence_complete=True, review_required=False,
    )
    by_id(candidate, "A1")["fields"]["gross_amount"] = "0.00"
    by_id(candidate, "A1")["amount_correct"] = True
    result = evaluate_samples(baseline, candidate, spec=spec)
    assert result["risk"]["fn"] == ["A2"]
    assert not next(row for row in result["samples"] if row["sample_id"] == "A1")["candidate"]["amount_correct"]
    assert result["absolute_safety_failed"] is True and result["acceptance"] == "rejected"


def test_r5_existing_risk_failure_still_rejects_even_when_fields_improve(frozen):
    spec, baseline, candidate = frozen
    by_id(baseline, "A2")["review_required"] = False
    by_id(candidate, "A2")["review_required"] = False
    by_id(baseline, "A1")["fields"]["gross_amount"] = "999.00"
    result = evaluate_samples(baseline, candidate, spec=spec)
    assert result["relative_result"] == "improvement"
    assert result["absolute_safety_failed"] is True
    assert result["acceptance"] == "rejected"


def test_r5_correcting_normal_false_positive_is_improvement(frozen):
    spec, baseline, candidate = frozen
    by_id(baseline, "A1")["review_required"] = True
    result = evaluate_samples(baseline, candidate, spec=spec)
    assert result["relative_result"] == "improvement" and result["regressions"] == []
    assert result["acceptance"] == "accepted_offline"


def test_r5_all_review_flags_do_not_mean_candidate_accepted(frozen):
    spec, baseline, candidate = frozen
    for row in [*baseline, *candidate]:
        row["review_required"] = True
    result = evaluate_samples(baseline, candidate, spec=spec)
    assert result["risk"]["fp"] == ["A1", "A3"]
    assert result["acceptance"] != "accepted_offline"


@pytest.mark.parametrize("side", ["baseline", "candidate"])
@pytest.mark.parametrize("state", ["missing", "unknown", "failed"])
def test_r5_incomplete_either_side_not_comparable(frozen, side, state):
    spec, baseline, candidate = frozen
    rows = baseline if side == "baseline" else candidate
    row = rows.pop(0)
    if state == "unknown":
        rows.append(unknown(row))
    elif state == "failed":
        rows.append({**row, "status": "failed", "review_required": True, "fields": {}, "evidence": []})
    result = evaluate_samples(baseline, candidate, spec=spec)
    assert result["denominator"] == 4 and result["relative_result"] == "not_comparable"
    assert result["acceptance"] == "incomplete"
    assert result[side][state if state == "failed" else state] == ["A1"]


def test_r5_duplicate_extra_and_unknown_identity_cannot_hide_in_missing(frozen):
    spec, baseline, candidate = frozen
    with pytest.raises(RetrievalError) as duplicate:
        evaluate_samples(baseline, candidate + [candidate[0]], spec=spec)
    assert duplicate.value.code == "evaluation_duplicate"
    with pytest.raises(RetrievalError) as other:
        evaluate_samples(baseline, candidate + [{**candidate[0], "sample_id": "OTHER"}], spec=spec)
    assert other.value.code == "evaluation_identity_mismatch"
    with pytest.raises(RetrievalError) as unbound:
        evaluate_samples(baseline, [replace(unknown(candidate[0]), candidate_fingerprint="0" * 64)], spec=spec)
    assert unbound.value.code == "evaluation_binding_mismatch"


@pytest.mark.parametrize("corruption", ["missing", "wrong_quote", "bad_page", "nan"])
def test_r5_evidence_is_recomputed_not_trusted(frozen, corruption):
    spec, baseline, candidate = frozen
    row = by_id(candidate, "A1")
    row["evidence_complete"] = True
    if corruption == "missing":
        row["evidence"] = []
    elif corruption == "wrong_quote":
        row["evidence"][0]["quote"] = "made up"
    elif corruption == "bad_page":
        row["evidence"][0]["page"] = 999
    else:
        row["fields"]["gross_amount"] = "NaN"
    result = evaluate_samples(baseline, candidate, spec=spec)
    assert result["acceptance"] == "rejected"
    assert next(row for row in result["samples"] if row["sample_id"] == "A1")["candidate"]["reasons"]


@pytest.mark.parametrize("changes", [
    {"policy_effective_from": "2026-11"},
    {"policy_effective_to": "2026-09"},
    {"policy_effective_from": "2026-00"},
    {"historical_period": "2026-11"},
    {"historical_period": ""},
])
def test_r5_policy_period_and_history_validity(changes):
    assert retrieve(query(), [example(**changes)]) == []


def test_r5_same_hash_and_ancestor_hash_excluded_despite_changed_id():
    current = query(source_hash="1" * 64, evaluation_ids=("held",), evaluation_source_hashes=("3" * 64,))
    bad = [
        example(example_id="renamed", source_hash="1" * 64),
        example(example_id="derived", source_hash="3" * 64),
        example(example_id="child", ancestor_source_hashes=("3" * 64,)),
        example(example_id="newid", source_sample_ids=("held",)),
    ]
    assert retrieve(current, bad) == []
    with pytest.raises(RetrievalError) as caught:
        validate_splits([bad[2]], set(), evaluation_source_hashes={"3" * 64})
    assert caught.value.code == "example_leak"


def test_r5_period_boundaries_and_known_train_are_valid():
    item = example(policy_effective_from="2026-10", policy_effective_to="2026-10")
    assert retrieve(query(), [item]) == [item]
    validate_splits([item], set(), evaluation_source_hashes=set())


def test_r5_report_contains_every_sample_scope_hash_reason_and_both_sides(frozen):
    spec, baseline, candidate = frozen
    result = evaluate_samples(baseline[1:], [unknown(row) for row in candidate], spec=spec)
    for entry in (evaluate_samples, evaluate):
        report = evaluation_report(entry(baseline[1:], [unknown(row) for row in candidate], spec=spec))
        parsed = json.loads(report["json"])
        assert parsed["baseline"]["missing"] == ["A1"]
        assert parsed["candidate"]["unknown"] == ["A1", "A2", "A3", "B1"]
        assert parsed["candidate"]["risk"]["unobserved"] == ["A1", "A2", "A3", "B1"]
        assert parsed["result_code"] == "incomplete"
        assert len(parsed["samples"]) == 4
        for sample in parsed["samples"]:
            assert sample["sample_id"] in report["markdown"]
            assert sample["scope"]["company_id"] in report["markdown"]
            assert sample["gold_sha256"] in report["markdown"]
            assert "unknown" in sample["candidate"]["reasons"]
    assert result["configurations"]["baseline"]["fingerprint"] != result["configurations"]["candidate"]["fingerprint"]


def test_r5_all_abstain_is_incomplete_not_an_improvement(frozen):
    spec, baseline, candidate = frozen
    for row in candidate:
        row.update(status="failed", review_required=True, fields={}, evidence=[])
    result = evaluate_samples(baseline, candidate, spec=spec)
    assert result["candidate_correct"] == 0  # Execution failures are never domain abstentions.
    assert result["candidate"]["failed"] == ["A1", "A2", "A3", "B1"]
    assert result["relative_result"] == "not_comparable"
    assert result["acceptance"] == "incomplete"


def test_r5_h01_positive_identity_and_frozen_gold_are_really_exercised(frozen):
    spec, baseline, candidate = frozen
    result = evaluate_samples(baseline, candidate, spec=spec)
    assert result["acceptance"] == "accepted_offline"
    assert result["candidate_correct"] == result["denominator"] == 4
    assert result["candidate"]["risk"]["correct_abstentions"] == ["B1"]
    assert {row["scope"]["company_id"] for row in result["samples"]} == {"company-a", "company-b"}
    assert all(row["gold_sha256"] and row["input_hash"] and row["source_hash"] for row in result["samples"])


def test_r5_swapped_candidate_cannot_be_adopted_by_merely_changing_fingerprint(frozen):
    spec, baseline, candidate = frozen
    forged = deepcopy(baseline)
    for row in forged:
        row["candidate_fingerprint"] = spec.candidate.fingerprint
    with pytest.raises(RetrievalError) as caught:
        evaluate_samples(baseline, forged, spec=spec)
    assert caught.value.code == "evaluation_binding_mismatch"


def test_r5_missing_baseline_cannot_manufacture_improvement(frozen):
    spec, _, candidate = frozen
    result = evaluate_samples([], candidate, spec=spec)
    assert result["baseline"]["missing_count"] == 4
    assert result["candidate"]["missing_count"] == 0
    assert result["relative_result"] == "not_comparable"
    assert result["acceptance"] == "incomplete"


@pytest.mark.parametrize("error", [
    {"error_code": "provider_timeout"}, {"failure_kind": "transport_error"}, {},
])
def test_r5_failure_is_not_expected_abstention_without_explicit_decision(frozen, error):
    spec, baseline, candidate = frozen
    row = by_id(candidate, "B1")
    row.update(status="failed", fields={}, evidence=[], review_required=True, **error)
    row.pop("decision", None)
    result = evaluate_samples(baseline, candidate, spec=spec)
    assert result["acceptance"] == "incomplete"
    assert "B1" in result["candidate"]["failed"]
    assert "B1" not in result["candidate"]["risk"]["correct_abstentions"]


def test_r5_current_amount_cannot_be_replaced_by_unbound_calculation():
    from tools.ai_inference_lab.answers import AnswerError, Claim, Suggestion
    from tools.ai_inference_lab.context import Evidence, Fact
    from tools.ai_inference_lab.examples import compile_example_context, validate_context_suggestion
    amount = Evidence("a", "x", "d", 1, "gross_amount", "120.00", version=2)
    pack = compile_example_context(
        query(), [example(amount="100.00")],
        current_facts=[Fact("gross_amount", "120.00", 2, "approved_fact", amount.identity)],
        current_evidence=[amount], budget={"input_tokens": 4000, "output_reserve": 200},
        prompt_version="p1", schema_version="suggestion-v1",
    )
    fake = Claim(
        "gross_amount", "100.00", "calculation", "c1", "b1", 2,
        calculation={"operator": "+", "inputs": ["50", "50"]},
    )
    with pytest.raises((RetrievalError, AnswerError)) as caught:
        validate_context_suggestion(pack, Suggestion("suggestion_ready", lines=[{"account_code": "6602"}], claims=[fake]))
    assert caught.value.code == "context_claim_kind_unsupported"


@pytest.mark.parametrize("changes", [
    {"reviewed": "false"}, {"reviewed": 1}, {"evidence_complete": "false"},
    {"evidence_complete": 1},
])
def test_r5_retrieval_rejects_truthy_permission_flags(changes):
    with pytest.raises(RetrievalError) as caught:
        retrieve(query(), [example(**changes)])
    assert caught.value.code == "example_identity_invalid"


@pytest.mark.parametrize("field", ["firm_id", "company_id", "book_id", "vendor_id"])
@pytest.mark.parametrize("value", ["", " ", True, 1])
def test_r5_matching_empty_or_invalid_scope_is_never_authorized(field, value):
    with pytest.raises(RetrievalError) as caught:
        retrieve(query(**{field: value}), [example(**{field: value})])
    assert caught.value.code == "query_identity_invalid"


@pytest.mark.parametrize("error", [
    {"error_code": "provider_timeout"},
    {"failure_kind": "transport_error"},
    {"error": "read timed out"},
])
def test_r5_explicit_abstention_cannot_hide_execution_error(frozen, error):
    spec, baseline, candidate = frozen
    row = by_id(candidate, "B1")
    assert row["status"] == "succeeded"
    assert row["decision"] == {"kind": "abstain", "reason": "unsupported_case"}
    row.update(error)
    result = evaluate_samples(baseline, candidate, spec=spec)
    assert result["acceptance"] == "incomplete"
    assert result["candidate"]["failed"] == ["B1"]
    assert result["candidate"]["risk"]["correct_abstentions"] == []


@pytest.mark.parametrize("decision", [
    None, {}, {"kind": "abstain"}, {"kind": "abstain", "reason": "provider_timeout"},
    {"kind": "abstain", "reason": "unsupported_case", "execution_failed": True},
])
def test_r5_successful_empty_result_requires_valid_domain_abstention(frozen, decision):
    spec, baseline, candidate = frozen
    by_id(candidate, "B1")["decision"] = decision
    result = evaluate_samples(baseline, candidate, spec=spec)
    assert result["acceptance"] == "rejected"
    assert result["absolute_safety_failed"]
    assert result["candidate"]["risk"]["correct_abstentions"] == []


@pytest.mark.parametrize("field", ["firm_id", "company_id", "book_id", "vendor_id"])
def test_r5_invalid_example_scope_is_rejected_even_without_matching_query(field):
    with pytest.raises(RetrievalError) as caught:
        retrieve(query(), [example(**{field: ""})])
    assert caught.value.code == "example_identity_invalid"


@pytest.mark.parametrize("decision", [
    {"kind": "abstain", "reason": "unsupported_case"},
    {"kind": "publish", "reason": "trusted"},
    {"kind": "abstain", "reason": "unknown"},
    None,
])
def test_r5_normal_extraction_cannot_ignore_contradictory_or_invalid_decision(frozen, decision):
    spec, baseline, candidate = frozen
    row = by_id(candidate, "A1")
    assert row["fields"] and row["evidence"] and row["review_required"] is False
    row["decision"] = decision
    result = evaluate_samples(baseline, candidate, spec=spec)
    assert result["acceptance"] == "rejected"
    assert result["absolute_safety_failed"]
    sample = next(item for item in result["samples"] if item["sample_id"] == "A1")
    assert sample["candidate"]["correct"] is False
    assert "domain_decision_invalid" in sample["candidate"]["reasons"]
