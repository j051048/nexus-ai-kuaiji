"""Independent gate must reject skipped, missing and substituted evidence."""

import json
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
