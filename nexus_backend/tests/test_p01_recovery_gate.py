"""P01 must prove executed joint recovery, not inventory-only or skipped tests."""

import importlib
import json
import os
import subprocess
import xml.etree.ElementTree as ET

import pytest


def gate():
    return importlib.import_module("scripts.p01_recovery_gate")


def link_directory(alias, target):
    if os.name == "nt":
        subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(alias), str(target)],
            check=True,
            capture_output=True,
        )
    else:
        alias.symlink_to(target, target_is_directory=True)


def test_joint_evidence_counts_pytest_current_alias_only_once(tmp_path):
    folder = tmp_path / "test_recovery0"
    folder.mkdir()
    proof = folder / "pg16-full-recovery-evidence.json"
    proof.write_text("{}")
    link_directory(tmp_path / "test_recoverycurrent", folder)
    assert gate().joint_evidence_path(tmp_path, 16) == proof.resolve()


@pytest.mark.parametrize("kind", ["missing", "distinct_duplicate", "external_alias"])
def test_joint_evidence_still_rejects_missing_duplicate_or_outside_run(tmp_path, kind):
    root = tmp_path / "joint-private"
    root.mkdir()
    for name in ["first", "second"] if kind == "distinct_duplicate" else []:
        folder = root / name
        folder.mkdir()
        (folder / "pg16-full-recovery-evidence.json").write_text("{}")
    if kind == "external_alias":
        outside = tmp_path / "old-run"
        outside.mkdir()
        (outside / "pg16-full-recovery-evidence.json").write_text("{}")
        link_directory(root / "test_recoverycurrent", outside)
    with pytest.raises(gate().GateError):
        gate().joint_evidence_path(root, 16)


def report(tmp_path, cases, *, skip=False):
    root = ET.Element("testsuite", tests=str(len(cases)))
    for identity in cases:
        classname, name = identity.split("::")
        case = ET.SubElement(root, "testcase", classname=classname, name=name)
        if skip:
            ET.SubElement(case, "skipped")
    path = tmp_path / "result.xml"
    ET.ElementTree(root).write(path)
    return path


def test_exact_joint_recovery_and_identity_cases_are_required(tmp_path):
    g = gate()
    for phase, expected in g.REQUIRED_CASES.items():
        result = g.validate_junit(report(tmp_path, expected), phase)
        assert result["passed"] == len(expected)


@pytest.mark.parametrize("mutation", ["skip", "missing", "duplicate", "replacement"])
def test_inventory_or_partial_success_cannot_pass_recovery_gate(tmp_path, mutation):
    g = gate()
    cases = list(g.REQUIRED_CASES["joint"])
    if mutation == "missing":
        cases.pop()
    if mutation == "duplicate":
        cases[-1] = cases[0]
    if mutation == "replacement":
        cases[-1] = "test_inventory::test_tables_exist"
    with pytest.raises(g.GateError):
        g.validate_junit(report(tmp_path, cases, skip=mutation == "skip"), "joint")


def test_recovery_business_evidence_must_be_nonempty_and_complete(tmp_path):
    g = gate()
    value = {
        "major": 16,
        "source_version": "16.15",
        "target_version": "16.15",
        "migrations": 166,
        "actual_pg_dump_restore": True,
        "originals": 2,
        "source_users": 6,
        "restored_capability_grants": 800,
        "restored_column_grants": 49,
        "p01_checks": {key: True for key in g.REQUIRED_CHECKS},
    }
    path = tmp_path / "proof.json"
    path.write_text(json.dumps(value))
    assert g.validate_evidence(path, 16)["p01_checks"] == value["p01_checks"]
    for key in g.REQUIRED_CHECKS:
        changed = dict(value, p01_checks=dict(value["p01_checks"], **{key: False}))
        path.write_text(json.dumps(changed))
        with pytest.raises(g.GateError):
            g.validate_evidence(path, 16)
    for key, bad in [
        ("major", 17),
        ("major", 16.0),
        ("source_version", "17.11"),
        ("target_version", "14.24"),
        ("migrations", 0),
        ("migrations", True),
        ("originals", 0),
        ("source_users", 0),
        ("restored_column_grants", 0),
        ("actual_pg_dump_restore", False),
    ]:
        path.write_text(json.dumps(dict(value, **{key: bad})))
        with pytest.raises(g.GateError):
            g.validate_evidence(path, 16)


def test_environment_identity_changes_if_any_private_config_changes(tmp_path):
    g = gate()
    names = [
        f"pg{major}-{suffix}.json"
        for major in (16, 17)
        for suffix in ("recovery_source", "recovery-fresh-target")
    ]
    names.append("wsl-bridge-private.json")
    for name in names:
        (tmp_path / name).write_text('{"private":"synthetic-secret"}')
    before = g.configuration_fingerprint(tmp_path)
    assert "synthetic-secret" not in str(before)
    for name in names:
        path = tmp_path / name
        path.write_text('{"private":"synthetic-changed"}')
        assert g.configuration_fingerprint(tmp_path) != before
        path.write_text('{"private":"synthetic-secret"}')
