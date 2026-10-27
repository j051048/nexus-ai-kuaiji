"""复核政策 PG 门槛必须拒绝缺例、替换、重复和伪绿 JUnit。"""

from pathlib import Path
import xml.etree.ElementTree as ET

import pytest


ROOT = Path(__file__).resolve().parents[2]


def test_ci_requires_review_policy_gate_after_existing_gates():
    workflow = (ROOT / ".github/workflows/accounting.yml").read_text(encoding="utf-8")
    assert "python scripts/review_policy_postgres_gate.py" in workflow
    assert workflow.index("python scripts/c01_postgres_gate.py") < workflow.index(
        "python scripts/review_policy_postgres_gate.py"
    )
    assert "nexus_backend/tests/test_review_policy_pg_gate.py" in workflow


def _module():
    from scripts import review_policy_postgres_gate

    return review_policy_postgres_gate


def _junit(path, cases, child=None):
    root = ET.Element("testsuites")
    suite = ET.SubElement(root, "testsuite", tests=str(len(cases)))
    for identifier in cases:
        classname, name = identifier.split("::", 1)
        case = ET.SubElement(suite, "testcase", classname=classname, name=name)
        if child:
            ET.SubElement(case, child)
            child = None
    ET.ElementTree(root).write(path, encoding="utf-8")
    return path


def test_exact_frozen_case_identities_pass(tmp_path):
    gate = _module()
    assert len(gate.REQUIRED_CASES) == 28
    assert len(set(gate.REQUIRED_CASES)) == 28
    assert gate.validate_junit(_junit(tmp_path / "pass.xml", gate.REQUIRED_CASES))["passed"] == 28


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "replacement", "zero"])
def test_missing_duplicate_or_replaced_cases_fail(tmp_path, mutation):
    gate = _module()
    cases = list(gate.REQUIRED_CASES)
    if mutation == "missing":
        cases.pop()
    elif mutation == "duplicate":
        cases[-1] = cases[0]
    elif mutation == "replacement":
        cases[-1] = "fake.module::fake_test"
    else:
        cases.clear()
    with pytest.raises(gate.GateError):
        gate.validate_junit(_junit(tmp_path / "bad.xml", cases))


@pytest.mark.parametrize("child", ["skipped", "failure", "error"])
def test_process_zero_cannot_hide_failed_or_skipped_case(tmp_path, child):
    gate = _module()
    with pytest.raises(gate.GateError):
        gate.validate_junit(_junit(tmp_path / "failed.xml", gate.REQUIRED_CASES, child))


@pytest.mark.parametrize(
    "counter", ["skipped", "failures", "errors", "disabled", "deselected", "tests"]
)
def test_counters_cannot_hide_missing_or_failed_execution(tmp_path, counter):
    gate = _module()
    path = _junit(tmp_path / "count.xml", gate.REQUIRED_CASES)
    tree = ET.parse(path)
    tree.getroot()[0].set(counter, "1")
    tree.write(path)
    with pytest.raises(gate.GateError):
        gate.validate_junit(path)


def test_malformed_junit_fails_closed(tmp_path):
    gate = _module()
    path = tmp_path / "malformed.xml"
    path.write_text("<broken", encoding="utf-8")
    with pytest.raises(gate.GateError):
        gate.validate_junit(path)


LOGICAL_CASES = [
    "nexus_backend.tests.test_review_policy_logical_version_postgres::"
    "test_pg_logical_version_counts_commands_but_history_counts_each_fact_change",
    "nexus_backend.tests.test_review_policy_logical_version_postgres::"
    "test_pg_noop_header_cannot_consume_next_native_fact_version",
]


def test_ci_runs_logical_version_as_separate_exact_gate(tmp_path):
    workflow = (ROOT / ".github/workflows/accounting.yml").read_text(encoding="utf-8")
    assert "--suite logical" in workflow
    gate = _module()
    assert (
        gate.validate_junit(_junit(tmp_path / "logical.xml", LOGICAL_CASES), suite="logical")[
            "passed"
        ]
        == 2
    )
    assert len(gate.REQUIRED_CASES) == 28


@pytest.mark.parametrize(
    "mutation", ["missing", "duplicate", "replacement", "skip", "error", "counter"]
)
def test_logical_gate_rejects_nonexact_or_unexecuted_cases(tmp_path, mutation):
    gate = _module()
    cases = list(LOGICAL_CASES)
    if mutation == "missing":
        cases.pop()
    elif mutation == "duplicate":
        cases[1] = cases[0]
    elif mutation == "replacement":
        cases[1] = gate.REQUIRED_CASES[0]
    path = _junit(
        tmp_path / "logical-bad.xml", cases, {"skip": "skipped", "error": "error"}.get(mutation)
    )
    if mutation == "counter":
        tree = ET.parse(path)
        tree.getroot()[0].set("tests", "3")
        tree.write(path)
    with pytest.raises(gate.GateError):
        gate.validate_junit(path, suite="logical")
