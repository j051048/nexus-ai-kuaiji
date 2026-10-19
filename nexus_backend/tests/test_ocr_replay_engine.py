import copy
import json
from pathlib import Path
import shutil

import pytest

from tools.ocr_replay import replay
from scripts.evaluate_ocr_fields import evaluate


FIXTURE = Path(__file__).parent / "fixtures/ocr_replay/manifest.json"


def _run(tmp_path, policy="baseline", name="run"):
    return replay.run_manifest(FIXTURE, policy, tmp_path / name)


def test_vendored_harness2_helpers_are_used(tmp_path):
    from tools.ocr_replay.vendor.harness2_runmark import is_complete
    from tools.ocr_replay.vendor.harness2_tree import normalize_rel

    assert normalize_rel("./firm_a/a1.ocr.json") == "firm_a/a1.ocr.json"
    with pytest.raises(ValueError):
        normalize_rel("../outside.json")
    result = _run(tmp_path)
    assert (Path(result["run_dir"]) / ".adapt_complete").is_file()
    assert is_complete(Path(result["run_dir"]), harness_dir=Path(replay.__file__).parent)


def test_manifest_and_two_company_run(tmp_path):
    manifest = replay.load_manifest(FIXTURE)
    assert len(manifest["samples"]) == 4
    assert {s["firm_id"] for s in manifest["samples"]} == {"firm-a", "firm-b"}
    result = _run(tmp_path)
    rows = [json.loads(line) for line in Path(result["results"]).read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 4
    assert rows[0]["firm_id"] == "firm-a"
    assert all(row["evidence_errors"] == [] for row in rows)


def test_baseline_and_candidate_are_actually_different_executions(tmp_path):
    base = _run(tmp_path, "baseline", "base")
    cand = _run(tmp_path, "candidate", "cand")
    base_rows = [json.loads(line) for line in Path(base["results"]).read_text(encoding="utf-8").splitlines()]
    cand_rows = [json.loads(line) for line in Path(cand["results"]).read_text(encoding="utf-8").splitlines()]
    assert base_rows[0]["policy_prompt"] != cand_rows[0]["policy_prompt"]
    assert base_rows[0]["request_fingerprint"] != cand_rows[0]["request_fingerprint"]
    assert base_rows[0]["fields"]["gross_amount"] is None
    assert cand_rows[0]["fields"]["gross_amount"] == "106.00"
    assert cand_rows[0]["fields"]["invoice_number"] == "1"
    assert base_rows[0]["fields"]["invoice_number"] == "0001"


def test_a_plain_label_sample_is_identical_for_both_policies(tmp_path):
    base = _run(tmp_path, "baseline", "base")
    cand = _run(tmp_path, "candidate", "cand")
    base_rows = {row["sample_id"]: row for row in (json.loads(line) for line in Path(base["results"]).read_text(encoding="utf-8").splitlines())}
    cand_rows = {row["sample_id"]: row for row in (json.loads(line) for line in Path(cand["results"]).read_text(encoding="utf-8").splitlines())}
    assert base_rows["A3"]["fields"] == cand_rows["A3"]["fields"]
    assert base_rows["A3"]["evidence"] == cand_rows["A3"]["evidence"]


def test_compare_reports_regression_and_fixed_field(tmp_path):
    base = _run(tmp_path, "baseline", "base")
    cand = _run(tmp_path, "candidate", "cand")
    report = replay.compare(FIXTURE, Path(base["run_dir"]), Path(cand["run_dir"]))
    assert report["samples"] == 4
    assert report["baseline"]["fields"]["gross_amount"]["correct"] == 1
    assert report["candidate"]["fields"]["gross_amount"]["correct"] == 3
    assert report["candidate"]["fields"]["invoice_number"]["correct"] == 1
    assert {"sample_id": "A1", "field": "gross_amount"} in report["fixed_fields"]
    assert {"sample_id": "A1", "field": "invoice_number"} in report["regressed_fields"]
    assert report["unsupported_samples"] == ["B1"]
    assert "A1" in report["critical_regressions"]
    assert report["status"] == "regression"
    assert "离线比较" in report["disclaimer"]


def test_reproducible_run_and_resume_do_not_reinvoke(tmp_path, monkeypatch):
    first = _run(tmp_path, "baseline", "base")
    calls = []
    original = replay.extract
    monkeypatch.setattr(replay, "extract", lambda sample, policy: calls.append(sample["sample_id"]) or original(sample, policy))
    replay.run_manifest(FIXTURE, "baseline", Path(first["run_dir"]), resume=True)
    assert calls == []


def test_archived_results_mode_is_explicit_and_does_not_extract(tmp_path, monkeypatch):
    base = _run(tmp_path, "baseline", "base")
    monkeypatch.setattr(replay, "extract", lambda *args: (_ for _ in ()).throw(AssertionError("must not extract")))
    archived = replay.run_manifest(
        FIXTURE,
        "baseline",
        tmp_path / "archived",
        mode="archived_results",
        archived=Path(base["results"]),
    )
    rows = [json.loads(line) for line in Path(archived["results"]).read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 4
    assert all(row["imported_mode"] == "archived_results" for row in rows)
    assert all(row["mode"] == "mock" for row in rows)


def test_archived_results_reject_missing_sample(tmp_path):
    base = _run(tmp_path, "baseline", "base")
    lines = Path(base["results"]).read_text(encoding="utf-8").splitlines()[:-1]
    partial = tmp_path / "partial.jsonl"
    partial.write_text("\n".join(lines) + "\n", encoding="utf-8")
    with pytest.raises(replay.ReplayError, match="RESULT_SET"):
        replay.run_manifest(
            FIXTURE, "baseline", tmp_path / "bad", mode="archived_results", archived=partial
        )


def test_incompatible_resume_is_rejected(tmp_path):
    first = _run(tmp_path, "baseline", "base")
    with pytest.raises(replay.ReplayError, match="INCOMPATIBLE_RESUME"):
        replay.run_manifest(FIXTURE, "candidate", Path(first["run_dir"]), resume=True)


def test_incomplete_result_set_is_rejected(tmp_path):
    base = _run(tmp_path, "baseline", "base")
    cand = _run(tmp_path, "candidate", "cand")
    lines = Path(cand["results"]).read_text(encoding="utf-8").splitlines()
    Path(cand["results"]).write_text("\n".join(lines[:-1]) + "\n", encoding="utf-8")
    with pytest.raises(replay.ReplayError, match="RESULT_SET"):
        replay.compare(FIXTURE, Path(base["run_dir"]), Path(cand["run_dir"]))


def test_duplicate_or_extra_result_is_rejected(tmp_path):
    base = _run(tmp_path, "baseline", "base")
    cand = _run(tmp_path, "candidate", "cand")
    lines = Path(cand["results"]).read_text(encoding="utf-8").splitlines()
    Path(cand["results"]).write_text(lines[0] + "\n" + lines[0] + "\n", encoding="utf-8")
    with pytest.raises(replay.ReplayError, match="DUPLICATE_RESULT|RESULT_SET"):
        replay.compare(FIXTURE, Path(base["run_dir"]), Path(cand["run_dir"]))


def test_evidence_quote_must_exist_in_frozen_text():
    manifest = replay.load_manifest(FIXTURE)
    pages = manifest["samples"][0]["ocr_pages"]
    assert replay.validate_evidence([{"field": "gross_amount", "page": 1, "quote": "价税合计（小写）：106.00"}], pages) == []
    assert replay.validate_evidence([{"field": "gross_amount", "page": 1, "quote": "伪造原文"}], pages)
    assert replay.validate_evidence([{"field": "gross_amount", "page": 9, "quote": "106.00"}], pages)


def test_path_traversal_and_hash_change_are_rejected(tmp_path):
    manifest = json.loads(FIXTURE.read_text(encoding="utf-8"))
    bad = tmp_path / "manifest.json"
    manifest["samples"][0]["ocr_path"] = "../outside.json"
    bad.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(replay.ReplayError, match="UNSAFE_PATH"):
        replay.load_manifest(bad)
    data = tmp_path / "data"
    shutil.copytree(FIXTURE.parent, data)
    manifest = json.loads((data / "manifest.json").read_text(encoding="utf-8"))
    manifest["samples"][0]["ocr_sha256"] = "0" * 64
    bad = data / "manifest.json"
    bad.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(replay.ReplayError, match="OCR_HASH"):
        replay.load_manifest(bad)


def test_duplicate_source_and_unknown_fields_are_rejected(tmp_path):
    data = tmp_path / "data"
    shutil.copytree(FIXTURE.parent, data)
    manifest = json.loads((data / "manifest.json").read_text(encoding="utf-8"))
    manifest["samples"][1]["source_hash"] = manifest["samples"][0]["source_hash"]
    bad = data / "manifest.json"
    bad.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(replay.ReplayError, match="SOURCE_DUPLICATE"):
        replay.load_manifest(bad)


def _input(pages, sample_id="bad", category="standard"):
    return replay.ExtractionInput(
        sample_id=sample_id,
        firm_id="f",
        company_id="c",
        ledger_id="l",
        book_id="b",
        period="2026-10",
        category=category,
        ocr_sha256="0" * 64,
        pages=tuple(pages),
    )


def test_invalid_amount_and_date_are_explicit_failures():
    policy = replay.POLICIES["baseline"]
    sample = _input(
        [{"page": 1, "raw_text": "发票号码：1\n开票日期：2026-13-01\n价税合计：1e2\n金额：1.00\n税额：0.00\n币种：CNY"}]
    )
    result = replay.extract(sample, policy)
    assert result["status"] == "failed"
    assert result["review_required"] is True
    assert result["error_code"] in {"INVALID_AMOUNT", "INVALID_DATE"}


def test_missing_fields_are_null_not_zero():
    policy = replay.POLICIES["baseline"]
    sample = _input([{"page": 1, "raw_text": "发票号码：0003\n币种：CNY"}], "missing")
    result = replay.extract(sample, policy)
    assert result["status"] == "succeeded"
    assert result["fields"]["tax_amount"] is None
    assert result["fields"]["gross_amount"] is None
    assert result["review_required"] is True


def test_mock_cost_is_unknown_not_zero():
    sample = replay.to_extraction_input(replay.load_manifest(FIXTURE)["samples"][0])
    result = replay.extract(sample, replay.POLICIES["baseline"])
    assert result["cost_cny"] is None


def test_t01_extractor_input_contains_no_gold(tmp_path):
    sample = replay.load_manifest(FIXTURE)["samples"][0]
    inp = replay.to_extraction_input(sample)
    assert "gold" not in repr(inp).lower()
    assert not hasattr(inp, "gold_fields")
    assert not hasattr(inp, "gold_path")
    captured = {}
    original = replay.extract
    def spy(value, policy):
        captured["input"] = value
        return original(value, policy)
    replay.extract = spy
    try:
        _run(tmp_path, "baseline", "spy")
    finally:
        replay.extract = original
    assert isinstance(captured["input"], replay.ExtractionInput)
    assert "gold" not in repr(captured["input"]).lower()


def test_t06_t07_evidence_is_revalidated_not_trusted():
    pages = [{"page": 1, "raw_text": "价税合计：106.00\n发票号码：0001"}]
    assert replay.validate_evidence([{"field": "gross_amount", "page": 1, "quote": "价税合计：106.00"}], pages, {"gross_amount": "106.00"}) == []
    assert replay.validate_evidence([], pages, {"gross_amount": "106.00"}) == ["missing-evidence:gross_amount"]
    assert replay.validate_evidence([{"field": "tax_amount", "page": 1, "quote": "价税合计：106.00"}], pages, {"gross_amount": "106.00"}) == ["missing-evidence:gross_amount"]


def test_t08_strict_amount_does_not_prefix_truncate():
    policy = replay.POLICIES["baseline"]
    for bad in ("1e2", "106.009"):
        text = f"发票号码：0007\n开票日期：2026-10-01\n价税合计：{bad}\n金额：100.00\n税额：6.00\n币种：CNY"
        result = replay.extract(_input([{"page": 1, "raw_text": text}], "amount-" + bad), policy)
        assert result["status"] == "failed"
        assert result["error_code"] == "INVALID_AMOUNT"
        assert result["review_required"] is True


def test_t09_amount_relation_is_deterministic_and_reviewed():
    text = "发票号码：0008\n开票日期：2026-10-01\n价税合计：999.00\n金额：100.00\n税额：6.00\n币种：CNY"
    result = replay.extract(_input([{"page": 1, "raw_text": text}]), replay.POLICIES["baseline"])
    assert result["status"] == "succeeded"
    assert "AMOUNT_MISMATCH" in result["deterministic_errors"]
    assert result["review_required"] is True


def test_t10_t11_same_uses_field_type_not_value_suffix():
    assert replay._same("gross_amount", "106", "106.00") is True
    assert replay._same("invoice_number", "000123", "123") is False
    assert replay._same("tax_amount", None, None) is True
    assert replay._same("tax_amount", None, "") is False


def test_t19_resume_output_matches_fresh_business_results(tmp_path):
    first = _run(tmp_path, "baseline", "base")
    fresh = _run(tmp_path, "baseline", "fresh")
    resumed = replay.run_manifest(FIXTURE, "baseline", Path(first["run_dir"]), resume=True)
    left = [json.loads(line) for line in Path(first["results"]).read_text(encoding="utf-8").splitlines()]
    right = [json.loads(line) for line in Path(fresh["results"]).read_text(encoding="utf-8").splitlines()]
    assert [{k: v for k, v in row.items() if k != "duration_ms"} for row in left] == [{k: v for k, v in row.items() if k != "duration_ms"} for row in right]
    assert resumed["completed"] == 4


def test_t15_modified_state_result_is_rejected(tmp_path):
    run = _run(tmp_path, "baseline", "base")
    state_path = Path(run["run_dir"]) / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["samples"]["A1"]["result"]["fields"]["gross_amount"] = "999.00"
    state_path.write_text(json.dumps(state), encoding="utf-8")
    with pytest.raises(replay.ReplayError, match="RESULT_CORRUPT"):
        replay.run_manifest(FIXTURE, "baseline", Path(run["run_dir"]), resume=True)


def test_t16_archived_content_change_invalidates_resume(tmp_path):
    base = _run(tmp_path, "baseline", "base")
    archive = tmp_path / "archive.jsonl"
    archive.write_text(Path(base["results"]).read_text(encoding="utf-8"), encoding="utf-8")
    out = tmp_path / "arch"
    replay.run_manifest(FIXTURE, "baseline", out, mode="archived_results", archived=archive)
    archive.write_text(Path(base["results"]).read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(replay.ReplayError, match="INCOMPATIBLE_RESUME"):
        replay.run_manifest(FIXTURE, "baseline", out, resume=True, mode="archived_results", archived=archive)


def test_t17_missing_completion_marker_blocks_resume(tmp_path):
    run = _run(tmp_path, "baseline", "base")
    (Path(run["run_dir"]) / ".adapt_complete").unlink()
    with pytest.raises(replay.ReplayError, match="INCOMPLETE"):
        replay.run_manifest(FIXTURE, "baseline", Path(run["run_dir"]), resume=True)


def test_t18_output_must_not_be_manifest_or_existing_report(tmp_path):
    with pytest.raises(replay.ReplayError, match="OUTPUT_PROTECTED"):
        replay.run_manifest(FIXTURE, "baseline", FIXTURE.parent)
    report = tmp_path / "report.json"
    report.write_text("{}", encoding="utf-8")
    with pytest.raises(replay.ReplayError, match="OUTPUT_EXISTS"):
        replay.compare(FIXTURE, tmp_path / "missing", tmp_path / "missing", report)


def test_t04_missing_content_digest_is_rejected(tmp_path):
    data = tmp_path / "data"
    shutil.copytree(FIXTURE.parent, data)
    manifest = json.loads((data / "manifest.json").read_text(encoding="utf-8"))
    manifest["samples"][0].pop("ocr_sha256")
    (data / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(replay.ReplayError, match="SAMPLE_SHAPE"):
        replay.load_manifest(data / "manifest.json")


def test_b01_b02_request_prompt_input_and_source_fields_are_bound(tmp_path):
    base = _run(tmp_path, "baseline", "base")
    rows = [json.loads(line) for line in Path(base["results"]).read_text(encoding="utf-8").splitlines()]
    for index, field in enumerate(("request_fingerprint", "input_hash", "policy_prompt")):
        bad_rows = copy.deepcopy(rows)
        bad_rows[0][field] = "tampered"
        archive = tmp_path / f"bad-{index}.jsonl"
        archive.write_text("\n".join(json.dumps(row) for row in bad_rows) + "\n", encoding="utf-8")
        with pytest.raises(replay.ReplayError):
            replay.run_manifest(FIXTURE, "baseline", tmp_path / f"bad-{index}", mode="archived_results", archived=archive)
    bad_rows = copy.deepcopy(rows)
    bad_rows[0].pop("request_fingerprint")
    archive = tmp_path / "missing-source.jsonl"
    archive.write_text("\n".join(json.dumps(row) for row in bad_rows) + "\n", encoding="utf-8")
    with pytest.raises(replay.ReplayError, match="RESULT_SOURCE"):
        replay.run_manifest(FIXTURE, "baseline", tmp_path / "missing-source", mode="archived_results", archived=archive)


def test_b03_result_row_must_match_run_metadata(tmp_path):
    base = _run(tmp_path, "baseline", "base")
    cand = _run(tmp_path, "candidate", "cand")
    meta_path = Path(cand["run_dir"]) / "run.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["policy_version"] = "unrelated"
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    with pytest.raises(replay.ReplayError, match="RESULT_POLICY"):
        replay.compare(FIXTURE, Path(base["run_dir"]), Path(cand["run_dir"]))


def test_t03_changed_sample_scope_is_rejected(tmp_path):
    data = tmp_path / "data"
    shutil.copytree(FIXTURE.parent, data)
    manifest = json.loads((data / "manifest.json").read_text(encoding="utf-8"))
    manifest["samples"][0]["company_id"] = "company-b"
    (data / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(replay.ReplayError, match="SAMPLE_SCOPE"):
        replay.load_manifest(data / "manifest.json")


def test_a01_archived_manifest_and_policy_are_validated_before_import(tmp_path):
    base = _run(tmp_path, "baseline", "base")
    rows = [json.loads(line) for line in Path(base["results"]).read_text(encoding="utf-8").splitlines()]
    rows[0]["manifest_sha256"] = "0" * 64
    rows[0]["policy_version"] = "unrelated-policy"
    archive = tmp_path / "bad-source.jsonl"
    archive.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    with pytest.raises(replay.ReplayError, match="RESULT_DATASET|RESULT_POLICY"):
        replay.run_manifest(
            FIXTURE, "baseline", tmp_path / "bad-import", mode="archived_results", archived=archive
        )


def test_a01_archived_policy_mismatch_is_rejected(tmp_path):
    base = _run(tmp_path, "baseline", "base")
    rows = [json.loads(line) for line in Path(base["results"]).read_text(encoding="utf-8").splitlines()]
    rows[0]["policy_version"] = "unrelated-policy"
    archive = tmp_path / "bad-policy.jsonl"
    archive.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    with pytest.raises(replay.ReplayError, match="RESULT_POLICY"):
        replay.run_manifest(
            FIXTURE, "baseline", tmp_path / "bad-policy-import", mode="archived_results", archived=archive
        )


def test_a01_archive_import_preserves_original_source_metadata(tmp_path):
    base = _run(tmp_path, "baseline", "base")
    archive = tmp_path / "good-source.jsonl"
    archive.write_text(Path(base["results"]).read_text(encoding="utf-8"), encoding="utf-8")
    imported = replay.run_manifest(
        FIXTURE, "baseline", tmp_path / "good-import", mode="archived_results", archived=archive
    )
    rows = [json.loads(line) for line in Path(imported["results"]).read_text(encoding="utf-8").splitlines()]
    assert rows[0]["manifest_sha256"] == json.loads(Path(base["results"]).read_text(encoding="utf-8").splitlines()[0])["manifest_sha256"]
    assert rows[0]["imported_mode"] == "archived_results"


def test_t05_archived_identity_cannot_be_rebadged(tmp_path):
    base = _run(tmp_path, "baseline", "base")
    rows = [json.loads(line) for line in Path(base["results"]).read_text(encoding="utf-8").splitlines()]
    rows[0]["firm_id"] = "firm-b"
    archive = tmp_path / "bad.jsonl"
    archive.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    with pytest.raises(replay.ReplayError, match="RESULT_SCOPE"):
        replay.run_manifest(
            FIXTURE, "baseline", tmp_path / "arch", mode="archived_results", archived=archive
        )


def test_t12_null_empty_and_missing_are_distinct(tmp_path):
    base = _run(tmp_path, "baseline", "base")
    rows = {row["sample_id"]: row for row in (json.loads(line) for line in Path(base["results"]).read_text(encoding="utf-8").splitlines())}
    assert rows["A1"]["fields"]["gross_amount"] is None
    assert replay._same("gross_amount", None, None) is True
    assert replay._same("gross_amount", None, "") is False
    assert replay._same("gross_amount", "106.00", "106") is True


def test_t13_failed_residual_fields_are_not_success(tmp_path):
    base = _run(tmp_path, "baseline", "base")
    rows = [json.loads(line) for line in Path(base["results"]).read_text(encoding="utf-8").splitlines()]
    rows[0]["status"] = "failed"
    rows[0]["review_required"] = True
    archive = tmp_path / "failed.jsonl"
    archive.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    good_archive = tmp_path / "good.jsonl"
    good_archive.write_text(
        Path(base["results"]).read_text(encoding="utf-8"), encoding="utf-8"
    )
    good_run = tmp_path / "good-run"
    replay.run_manifest(FIXTURE, "baseline", good_run, mode="archived_results", archived=good_archive)
    out = tmp_path / "failed-run"
    replay.run_manifest(FIXTURE, "baseline", out, mode="archived_results", archived=archive)
    report = replay.compare(FIXTURE, good_run, out)
    assert report["failed_or_unknown"] >= 1
    assert report["status"] == "incomplete"


def test_t14_unsupported_and_splits_are_separate(tmp_path):
    base = _run(tmp_path, "baseline", "base")
    cand = _run(tmp_path, "candidate", "cand")
    report = replay.compare(FIXTURE, Path(base["run_dir"]), Path(cand["run_dir"]))
    assert report["unsupported_samples"] == ["B1"]
    assert report["splits"]["dev"]["samples"] == 2
    assert report["splits"]["validation"]["samples"] == 1
    assert report["candidate"]["samples"] == 3


def test_t06_compare_recomputes_evidence_instead_of_trusting_flag(tmp_path):
    base = _run(tmp_path, "baseline", "base")
    cand = _run(tmp_path, "candidate", "cand")
    path = Path(cand["results"])
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    rows[0]["evidence"] = [{"field": "gross_amount", "page": 1, "quote": "伪造引用"}]
    rows[0]["evidence_errors"] = []
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    report = replay.compare(FIXTURE, Path(base["run_dir"]), Path(cand["run_dir"]))
    assert report["candidate_evidence_errors"]
    assert report["status"] == "regression"


def test_t15_modified_results_file_is_rejected(tmp_path):
    run = _run(tmp_path, "baseline", "base")
    path = Path(run["results"])
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    rows[0]["fields"]["gross_amount"] = "999.00"
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    with pytest.raises(replay.ReplayError, match="RESULT_CORRUPT"):
        replay.run_manifest(FIXTURE, "baseline", Path(run["run_dir"]), resume=True)


def test_t18_output_paths_are_protected(tmp_path):
    with pytest.raises(replay.ReplayError, match="OUTPUT_PROTECTED"):
        replay.run_manifest(FIXTURE, "baseline", FIXTURE.parent)
    report = tmp_path / "report.json"
    report.write_text("{}", encoding="utf-8")
    with pytest.raises(replay.ReplayError, match="OUTPUT_EXISTS"):
        replay.compare(FIXTURE, tmp_path / "a", tmp_path / "b", report)


def test_existing_evaluator_contract_still_works():
    report = evaluate(
        [{"sample_id": "one", "category": "synthetic", "fields": {"invoice_number": "0001"}}],
        [{"sample_id": "one", "status": "succeeded", "review_required": False, "fields": {"invoice_number": "0001"}}],
    )
    assert report["all_labeled_fields_exact"] == 1
