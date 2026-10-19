import copy
import json
from pathlib import Path

import pytest

from tools.ai_inference_lab import selective
from tools.ocr_replay import replay

MANIFEST = Path(__file__).parent / "fixtures/ocr_replay/manifest.json"


def test_clean_invoice_does_not_review_and_anomaly_reviews_once():
    report = selective.run(MANIFEST)
    rows = {r["sample_id"]: r for r in report["results"]}
    assert rows["A3"]["route"] == "none" and rows["A3"]["actual_calls"] == 0
    assert rows["A1"]["route"] == "review" and rows["A1"]["actual_calls"] == 1
    assert rows["A1"]["logical_calls"] == 2
    assert rows["A1"]["candidate_status"] == "review_required"


def test_unsupported_is_blocked_not_looped():
    report = selective.run(MANIFEST)
    b1 = next(r for r in report["results"] if r["sample_id"] == "B1")
    assert b1["route"] == "blocked"
    assert b1["candidate_status"] == "blocked"
    assert b1["actual_calls"] == 0


def test_review_repairs_gross_but_never_marks_accounting_complete():
    report = selective.run(MANIFEST)
    a1 = next(r for r in report["results"] if r["sample_id"] == "A1")
    assert a1["before"]["gross_amount"] is None
    assert a1["after"]["gross_amount"] == "106.00"
    assert a1["candidate_status"] == "review_required"
    assert report["summary"]["status"] == "promising_offline"


def test_bad_review_can_regress_and_is_not_hidden_by_a_fix():
    report = selective.run(MANIFEST, bad_review=True)
    assert report["summary"]["status"] == "regression"
    assert report["summary"]["critical_regressions"] == ["A1"]


def test_budget_exhaustion_stops_extra_review():
    report = selective.run(MANIFEST, budget=0)
    assert report["summary"]["actual_mock_calls"] == 0
    assert all(r["review_status"] == "budget_exhausted" for r in report["results"] if r["route"] == "review")
    assert all(r["candidate_status"] == "review_required" for r in report["results"] if r["route"] == "review")


def test_unknown_review_stays_unknown_and_has_one_attempt():
    report = selective.run(MANIFEST, unknown_review=True)
    a2 = next(r for r in report["results"] if r["sample_id"] == "A2")
    assert a2["candidate_status"] == "unknown"
    assert a2["review_status"] == "unknown"
    assert a2["actual_calls"] == 1


def test_two_companies_are_kept_separate():
    report = selective.run(MANIFEST)
    assert {r["company_id"] for r in report["results"]} == {"company-a", "company-b"}
    assert all(r["inputs"]["ocr_sha256"] for r in report["results"])
    assert report["model_verified"] is False


def test_b01_budget_zero_keeps_review_required_and_explicit_reason():
    report = selective.run(MANIFEST, budget=0)
    a1 = next(r for r in report["results"] if r["sample_id"] == "A1")
    assert a1["review_status"] == "budget_exhausted"
    assert a1["candidate_status"] == "review_required"
    assert a1["review_requested"] is True and a1["review_attempted"] is False
    assert a1["actual_calls"] == 0


def test_b02_requested_attempted_completed_are_not_collapsed():
    report = selective.run(MANIFEST)
    assert report["summary"]["review_requested"] == 2
    assert report["summary"]["review_attempted"] == 2
    assert report["summary"]["review_completed"] == 2
    empty = selective.run(MANIFEST, budget=0)
    assert empty["summary"]["review_requested"] == 2
    assert empty["summary"]["review_attempted"] == 0
    assert empty["summary"]["review_completed"] == 0


def test_b03_review_amount_mismatch_is_rechecked():
    assert "AMOUNT_MISMATCH" in selective.validate_fields(
        {"gross_amount": "100.00", "net_amount": "6.00", "tax_amount": "0.00"}
    )


def test_b04_wrong_label_evidence_does_not_support_gross():
    path = MANIFEST.parent / "review_responses_evidence_bad.json"
    report = selective.run(MANIFEST, responses_path=path)
    a1 = next(r for r in report["results"] if r["sample_id"] == "A1")
    assert a1["after"]["gross_amount"] is None
    assert a1["review_status"] == "rejected_evidence"


def test_b05_identity_change_rejects_comparison():
    base = selective.run(MANIFEST)
    candidate = copy.deepcopy(base)
    candidate["results"][0]["ledger_id"] = "other-ledger"
    with pytest.raises(replay.ReplayError, match="RESULT_SCOPE"):
        selective.compare(MANIFEST, base, candidate)


def test_b06_empty_missing_duplicate_results_cannot_compare():
    base = selective.run(MANIFEST)
    empty = {"results": [], "manifest_sha256": base["manifest_sha256"]}
    with pytest.raises(replay.ReplayError, match="NO_RESULTS"):
        selective.compare(MANIFEST, base, empty)
    missing = copy.deepcopy(base)
    missing["results"].pop()
    with pytest.raises(replay.ReplayError, match="RESULT_SET"):
        selective.compare(MANIFEST, base, missing)
    duplicate = copy.deepcopy(base)
    duplicate["results"].append(copy.deepcopy(duplicate["results"][0]))
    with pytest.raises(replay.ReplayError, match="DUPLICATE_RESULT"):
        selective.compare(MANIFEST, base, duplicate)


def test_b07_unknown_and_cancel_preserve_attempt_outcome():
    unknown = selective.run(MANIFEST, fail_mode="unknown")
    assert unknown["summary"]["unknown"] >= 1
    assert unknown["summary"]["review_completed"] == 0
    assert unknown["summary"]["actual_mock_calls"] >= 1
    cancelled = selective.run(MANIFEST, fail_mode="cancel")
    a1 = next(r for r in cancelled["results"] if r["sample_id"] == "A1")
    assert a1["review_call"]["sent"] is True
    assert a1["review_status"] == "cancelled"


def test_b08_policy_block_does_not_call():
    report = selective.run(
        MANIFEST,
        policies={"company-a": selective.Policy("company-a", allowed=False), "company-b": selective.Policy("company-b")},
    )
    a1 = next(r for r in report["results"] if r["sample_id"] == "A1")
    assert a1["route"] == "blocked"
    assert a1["actual_calls"] == 0
    assert a1["candidate_status"] == "blocked"


def test_b09_post_review_type_date_precision_and_required_are_revalidated():
    assert "INVALID_DATE" in selective.validate_fields({"issued_on": "2026-13-01"})
    assert "INVALID_AMOUNT:gross_amount" in selective.validate_fields({"gross_amount": "106.009"})
    assert "FIELD_TYPE:gross_amount" in selective.validate_fields({"gross_amount": 106})
    assert "MISSING_FIELD" in selective.validate_fields({"gross_amount": None})


def test_b10_b11_resume_reuses_completed_and_rejects_changed_budget(tmp_path):
    state = tmp_path / "state"
    first = selective.run(MANIFEST, state_dir=state)
    resumed = selective.run(MANIFEST, state_dir=state, resume=True)
    assert resumed["results"] == first["results"]
    with pytest.raises(replay.ReplayError, match="INCOMPATIBLE_RUN"):
        selective.run(MANIFEST, state_dir=state, resume=True, budget=1)


def test_b10_unknown_review_is_reused_without_resend(tmp_path):
    state = tmp_path / "unknown-state"
    first = selective.run(MANIFEST, state_dir=state, fail_mode="unknown")
    resumed = selective.run(MANIFEST, state_dir=state, resume=True, fail_mode="unknown")
    assert [r["candidate_status"] for r in resumed["results"]] == [r["candidate_status"] for r in first["results"]]
    assert resumed["summary"]["unknown"] == first["summary"]["unknown"]
    assert resumed["summary"]["actual_mock_calls"] == first["summary"]["actual_mock_calls"]


def test_b12_duplicate_cross_company_sample_id_is_rejected(tmp_path):
    data = tmp_path / "data"
    import shutil
    shutil.copytree(MANIFEST.parent, data)
    manifest = json.loads((data / "manifest.json").read_text(encoding="utf-8"))
    duplicate = copy.deepcopy(manifest["samples"][0])
    duplicate["sample_id"] = "A1"
    duplicate["firm_id"] = "firm-b"
    duplicate["company_id"] = "company-b"
    duplicate["source_hash"] = "different-source"
    manifest["samples"].append(duplicate)
    (data / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(replay.ReplayError, match="SAMPLE_DUPLICATE"):
        replay.load_manifest(data / "manifest.json")
