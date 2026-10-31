"""The dynamic acceptance gate must reject partial or skipped evidence."""

import json
import xml.etree.ElementTree as ET

import pytest

from scripts.assistant_delivery_postgres_gate import load_contract, validate_junit
from scripts.ak05_postgres_gate import GateError


def xml(tmp_path):
    root = ET.Element("testsuites")
    cases = load_contract()["cases"]
    suite = ET.SubElement(
        root, "testsuite", tests=str(len(cases)), failures="0", errors="0", skipped="0"
    )
    for identity in cases:
        classname, name = identity.split("::")
        ET.SubElement(suite, "testcase", classname=classname, name=name)
    return root, suite, tmp_path / "results.xml"


def test_complete_dynamic_collection(tmp_path):
    root, suite, path = xml(tmp_path)
    ET.ElementTree(root).write(path)
    assert validate_junit(path)["passed"] == 43


@pytest.mark.parametrize(
    "attack", ["skip", "failure", "error", "missing", "duplicate", "renamed", "count"]
)
def test_partial_or_substituted_result_is_not_acceptance(tmp_path, attack):
    root, suite, path = xml(tmp_path)
    case = list(suite)[0]
    if attack in {"skip", "failure", "error"}:
        ET.SubElement(case, "skipped" if attack == "skip" else attack)
    elif attack == "missing":
        suite.remove(case)
        suite.set("tests", str(len(suite)))
    elif attack == "duplicate":
        list(suite)[1].attrib.update(case.attrib)
    elif attack == "renamed":
        case.set("classname", "unrelated.test_suite")
    else:
        suite.set("tests", "0")
    ET.ElementTree(root).write(path)
    with pytest.raises(GateError):
        validate_junit(path)


@pytest.mark.parametrize("attack", ["version", "selectors", "cases"])
def test_incomplete_contract_cannot_claim_full_coverage(tmp_path, attack):
    contract = load_contract()
    contract[attack] = 999 if attack == "version" else contract[attack][:-1]
    path = tmp_path / "contract.json"
    path.write_text(json.dumps(contract), encoding="utf-8")
    with pytest.raises(GateError):
        load_contract(path)


@pytest.mark.parametrize("attack", ["remote", "missing", "database", "mismatch", "query"])
def test_target_is_rejected_before_any_fixture_migration(monkeypatch, attack):
    from types import SimpleNamespace
    import test_assistant_delivery_postgres as suite

    monkeypatch.setenv("ACCOUNTING_AK05_ISOLATED_TARGET", "1")
    url = "postgresql://fixture:synthetic@127.0.0.1:25616/ak01_local_ak05_unit"
    for name in ("ADMIN_URL", "APP_URL", "WORKER_URL"):
        monkeypatch.setattr(suite, name, url)
    bad = {
        "remote": url.replace("127.0.0.1", "example.test"),
        "missing": None,
        "database": url.replace("ak01_local_ak05_unit", "production"),
        "mismatch": url.replace("25616", "25617"),
        "query": url + "?hostaddr=192.0.2.1",
    }[attack]
    monkeypatch.setattr(suite, "APP_URL", bad)
    called = []
    monkeypatch.setattr(
        suite,
        "existing_business_postgres",
        SimpleNamespace(__wrapped__=lambda: called.append(True)),
    )
    with pytest.raises(AssertionError):
        suite.business_postgres.__wrapped__()
    assert not called
