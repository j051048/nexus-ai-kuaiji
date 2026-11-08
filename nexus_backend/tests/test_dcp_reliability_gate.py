"""Independent gate must reject skipped, missing and substituted evidence."""

import json
import re
import xml.etree.ElementTree as ET
import pytest
from scripts import dcp_reliability_gate as gate


def report(tmp_path, cases):
    root = ET.Element("testsuite", tests=str(len(cases)), errors="0", failures="0", skipped="0")
    for identity in cases:
        cls, name = identity.split("::")
        ET.SubElement(root, "testcase", classname=cls, name=name)
    path = tmp_path / "results.xml"
    ET.ElementTree(root).write(path)
    return path


def test_exact_settlement_manifest_is_accepted(tmp_path):
    cases = gate.contract("settlement")["cases"]
    assert gate.validate_junit(report(tmp_path, cases), cases)["passed"] == len(cases)


@pytest.mark.parametrize("fault", ["missing", "extra", "duplicate", "skip", "failure", "count"])
def test_gate_rejects_false_green(tmp_path, fault):
    cases = gate.contract("settlement")["cases"]
    values = list(cases)
    if fault == "missing":
        values = values[:-1]
    if fault == "extra":
        values += ["foreign::replacement"]
    if fault == "duplicate":
        values[-1] = values[0]
    path = report(tmp_path, values)
    tree = ET.parse(path)
    root = tree.getroot()
    if fault == "skip":
        ET.SubElement(root[0], "skipped")
    if fault == "failure":
        ET.SubElement(root[0], "failure")
    if fault == "count":
        root.set("tests", "999")
    tree.write(path)
    with pytest.raises(gate.GateError):
        gate.validate_junit(path, cases)


def test_both_suites_are_pinned_and_workflow_runs_independently():
    workflow = (gate.ROOT / ".github/workflows/accounting.yml").read_text(encoding="utf-8")
    for suite in ("settlement", "dcp"):
        data = gate.contract(suite)
        assert data["cases"] and len(set(data["cases"])) == len(data["cases"])
        assert "--suite " + suite in workflow
    assert "test_settlement_company_lock_postgres.py" in gate.contract("settlement")["files"]


def test_task_journey_gate_includes_analysis_and_clock_on_both_versions():
    selected = gate.contract("tasks")
    assert set(selected["files"]) == {
        "test_task_journey_postgres.py",
        "test_imp08_analysis_postgres.py",
        "test_pg_queue_clock_postgres.py",
        "test_tasks0406_postgres.py",
        "test_tasks0712_postgres.py",
    }
    workflow = (gate.ROOT / ".github/workflows/accounting.yml").read_text(encoding="utf-8")
    assert "--suite tasks" in workflow
    assert "--major ${{ matrix.postgres }}" in workflow


def test_task_browser_gate_retains_sanitized_evidence_even_on_failure():
    workflow = (gate.ROOT / ".github/workflows/accounting.yml").read_text(encoding="utf-8")
    job = workflow.split("  e2e:\n", 1)[1].split("  core-regression-gate:", 1)[0]
    steps = re.split(r"(?m)^      - ", job)
    retained = [step for step in steps if "test-reports/e2e-tasks/*/reports/*.json" in step]
    assert len(retained) == 1, "Task browser acceptance must retain its evidence"
    step = retained[0]
    assert "actions/upload-artifact@v4" in step
    assert "always()" in step and "matrix.shard == 1" in step
    assert "if-no-files-found: error" in step
    assert "runtime" not in step and "trace.zip" not in step


def test_tasks0406_join_existing_required_gates_without_replacing_tasks0103():
    selected = gate.contract("tasks")
    assert "test_tasks0406_postgres.py" in selected["files"]
    assert any("test_invitation_revocation_during_send" in case for case in selected["cases"])
    browser = json.loads((gate.ROOT / "scripts/e2e_task_cases.json").read_text(encoding="utf-8"))
    assert sum(row["file"] == "e2e/tasks0406.spec.ts" for row in browser["cases"]) == 6
    assert sum(row["file"] == "e2e/task-journey.spec.ts" for row in browser["cases"]) == 6


def test_tasks0712_extend_both_required_gates():
    selected = gate.contract("tasks")
    assert "test_tasks0712_postgres.py" in selected["files"]
    assert sum("test_tasks0712_postgres::" in case for case in selected["cases"]) == 5
    browser = json.loads((gate.ROOT / "scripts/e2e_task_cases.json").read_text(encoding="utf-8"))
    assert sum(row["file"] == "e2e/tasks0712.spec.ts" for row in browser["cases"]) == 6
    assert sum(row["file"] == "e2e/dcp-r567-evidence.spec.ts" for row in browser["cases"]) == 2
