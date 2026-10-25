"""The new C01 gate must preserve every original case identity and parameter."""

import xml.etree.ElementTree as ET

import pytest

from scripts.c01_postgres_gate import GateError, REQUIRED_CASES, validate_junit


def result(path, cases, child=None):
    root = ET.Element("testsuites")
    suite = ET.SubElement(root, "testsuite", tests=str(len(cases)))
    for identity in cases:
        classname, name = identity.split("::", 1)
        case = ET.SubElement(suite, "testcase", classname=classname, name=name)
        if child:
            ET.SubElement(case, child)
            child = None
    ET.ElementTree(root).write(path, encoding="utf-8")
    return path


def test_all_frozen_case_identities_are_required(tmp_path):
    assert len(REQUIRED_CASES) == 34
    assert validate_junit(result(tmp_path / "pass.xml", REQUIRED_CASES))["passed"] == 34


@pytest.mark.parametrize("mutation", ["missing", "replacement", "duplicate", "zero"])
def test_partial_or_substituted_green_results_fail(tmp_path, mutation):
    cases = list(REQUIRED_CASES)
    if mutation == "missing":
        cases.pop()
    elif mutation == "replacement":
        cases[-1] = "nexus_backend.tests.test_fake::test_fake"
    elif mutation == "duplicate":
        cases[-1] = cases[0]
    else:
        cases = []
    with pytest.raises(GateError):
        validate_junit(result(tmp_path / "partial.xml", cases))


@pytest.mark.parametrize("child", ["skipped", "failure", "error"])
def test_return_zero_does_not_override_case_result(tmp_path, child):
    with pytest.raises(GateError):
        validate_junit(result(tmp_path / "case.xml", REQUIRED_CASES, child))


@pytest.mark.parametrize("counter", ["skipped", "failures", "errors", "disabled", "deselected"])
def test_suite_totals_must_not_hide_unexecuted_case(tmp_path, counter):
    path = result(tmp_path / "count.xml", REQUIRED_CASES)
    tree = ET.parse(path)
    tree.getroot()[0].set(counter, "1")
    tree.write(path)
    with pytest.raises(GateError):
        validate_junit(path)


def test_malformed_xml_never_becomes_an_empty_success(tmp_path):
    path = tmp_path / "broken.xml"
    path.write_text("<broken")
    with pytest.raises(GateError):
        validate_junit(path)
