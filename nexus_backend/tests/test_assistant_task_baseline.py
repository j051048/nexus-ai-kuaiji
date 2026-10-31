"""Task-level evaluation guards, independent of a provider's self-reported score."""

import json

import pytest

from tools.assistant_replay.baseline import (
    DEFAULT_MANIFEST,
    BaselineError,
    candidate_identity,
    evaluate,
    load_dataset,
    render_markdown,
    save_report,
)


def test_frozen_six_cases_and_separate_gold():
    dataset = load_dataset(DEFAULT_MANIFEST)
    assert set(dataset["cases"]) == {"normal", "missing", "conflict", "revoked", "quota", "unknown"}
    assert {row["company"] for row in dataset["cases"].values()} == {"company_a", "company_b"}
    assert all("gold" not in row and "expected" not in row for row in dataset["cases"].values())
    assert candidate_identity()["mode"] == "mock"


def test_missing_results_remain_in_fixed_denominator():
    result = evaluate(load_dataset(DEFAULT_MANIFEST), [], candidate_identity())
    assert result["denominator"] == 6 and result["missing_count"] == 6
    assert result["engineering"] == "incomplete"
    assert result["task_success_count"] == 0
    assert "真实模型效果未验证" in render_markdown(result)


@pytest.mark.parametrize("filename", ["cases.json", "gold.json"])
def test_changed_sources_or_gold_cannot_be_silently_rescored(tmp_path, filename):
    import shutil

    shutil.copytree(DEFAULT_MANIFEST.parent, tmp_path / "fixture")
    path = tmp_path / "fixture" / filename
    path.write_text(path.read_text(encoding="utf-8") + " ", encoding="utf-8")
    with pytest.raises(BaselineError, match="hash"):
        load_dataset(tmp_path / "fixture" / "manifest.json")


@pytest.mark.parametrize("path", ["../cases.json", "C:/private.json"])
def test_manifest_paths_cannot_escape_fixture(tmp_path, path):
    data = json.loads(DEFAULT_MANIFEST.read_text(encoding="utf-8"))
    data["cases"]["path"] = path
    (tmp_path / "manifest.json").write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(BaselineError):
        load_dataset(tmp_path / "manifest.json")


def test_no_observation_can_self_award_task_success():
    dataset = load_dataset(DEFAULT_MANIFEST)
    with pytest.raises(BaselineError):
        evaluate(
            dataset, [{"sample_id": "normal", "passed": True, "score": 1}], candidate_identity()
        )


def test_candidate_identity_covers_runtime_prompt_and_harness():
    identity = candidate_identity()
    assert "nexus_backend/app/assistant/provider.py" in identity["files"]
    assert "nexus_backend/app/assistant/fact_validation.py" in identity["files"]
    assert "tools/assistant_replay/baseline.py" in identity["files"]
    assert all(len(value) == 64 for value in identity["files"].values())


def test_loaded_dataset_detects_later_tampering(tmp_path):
    import shutil

    shutil.copytree(DEFAULT_MANIFEST.parent, tmp_path / "fixture")
    dataset = load_dataset(tmp_path / "fixture" / "manifest.json")
    (tmp_path / "fixture" / "gold.json").write_text("{}", encoding="utf-8")
    with pytest.raises(BaselineError, match="hash"):
        evaluate(dataset, [], candidate_identity())


def test_in_memory_truth_cannot_override_frozen_gold():
    dataset = load_dataset(DEFAULT_MANIFEST)
    dataset["gold"]["normal"]["calls"] = 99
    with pytest.raises(BaselineError):
        evaluate(dataset, [], candidate_identity())


def test_unknown_cannot_be_changed_to_success_by_summary_flags():
    # The workflow suite supplies complete observer evidence. Here malformed
    # envelopes must fail closed, not become a plausible-looking score.
    row = {"sample_id": "unknown", "status": "succeeded", "cost_cny": 0}
    with pytest.raises(BaselineError):
        evaluate(load_dataset(DEFAULT_MANIFEST), [row], candidate_identity())


def test_report_and_observations_are_immutable(tmp_path):
    result = evaluate(load_dataset(DEFAULT_MANIFEST), [], candidate_identity())
    save_report(result, tmp_path, observations=[])
    saved = (tmp_path / "report.json").read_bytes()
    with pytest.raises(BaselineError, match="already exists"):
        save_report(result, tmp_path, observations=[])
    assert (tmp_path / "report.json").read_bytes() == saved


@pytest.mark.parametrize("fault", ["skip", "failure", "error", "missing", "duplicate"])
def test_gate_requires_six_executed_cases_without_skips(tmp_path, fault):
    import xml.etree.ElementTree as ET
    from tools.assistant_replay.__main__ import validate_junit

    suite = ET.Element("testsuite")
    for sid in ("normal", "missing", "conflict", "revoked", "quota", "unknown"):
        ET.SubElement(suite, "testcase", name=f"test_monthly_delivery_task_real_worker[{sid}]")
    if fault == "missing":
        suite.remove(suite[-1])
    elif fault == "duplicate":
        ET.SubElement(suite, "testcase", name=suite[-1].attrib["name"])
    else:
        ET.SubElement(suite[-1], "skipped" if fault == "skip" else fault)
    path = tmp_path / "junit.xml"
    ET.ElementTree(suite).write(path)
    with pytest.raises(BaselineError):
        validate_junit(path)


def test_runner_refuses_existing_output_and_fixture_directory(tmp_path):
    from tools.assistant_replay.__main__ import main

    for path in (tmp_path, DEFAULT_MANIFEST.parent):
        with pytest.raises(SystemExit) as error:
            main(["--out", str(path)])
        assert error.value.code == 2
