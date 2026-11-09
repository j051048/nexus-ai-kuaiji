"""The continuous PG gate must fail closed even when pytest exits successfully."""

import json
from pathlib import Path
from types import SimpleNamespace
import xml.etree.ElementTree as ET

import pytest

from scripts.ak05_postgres_gate import (
    GateError,
    REQUIRED_CASES,
    GROUPS,
    validate_config,
    validate_junit,
    validate_report_manifest,
    prepare,
    run_gate,
    sanitized,
)
from scripts import ak05_postgres_gate as gate


def junit(path, cases, *, child=None, counter=None):
    root = ET.Element("testsuites")
    suite = ET.SubElement(root, "testsuite", tests=str(len(cases)), **(counter or {}))
    for identity in cases:
        classname, name = identity.split("::", 1)
        case = ET.SubElement(suite, "testcase", classname=classname, name=name)
        if child:
            ET.SubElement(case, child)
            child = None
    ET.ElementTree(root).write(path, encoding="utf-8")
    return path


@pytest.mark.parametrize("group", list(GROUPS))
def test_complete_frozen_junit_passes(tmp_path, group):
    result = validate_junit(junit(tmp_path / "results.xml", REQUIRED_CASES[group]), group)
    assert result["passed"] == len(REQUIRED_CASES[group])


@pytest.mark.parametrize("child", ["skipped", "failure", "error"])
def test_green_exit_cannot_hide_nonpassing_testcase(tmp_path, child):
    with pytest.raises(GateError):
        validate_junit(
            junit(tmp_path / "results.xml", REQUIRED_CASES["unified"], child=child), "unified"
        )


@pytest.mark.parametrize(
    "counter",
    [{"skipped": "1"}, {"errors": "1"}, {"failures": "1"}, {"tests": "0"}, {"skipped": "no"}],
)
def test_junit_suite_counters_cannot_claim_success(tmp_path, counter):
    path = junit(tmp_path / "results.xml", REQUIRED_CASES["unified"])
    tree = ET.parse(path)
    tree.getroot()[0].attrib.update(counter)
    tree.write(path)
    with pytest.raises(GateError):
        validate_junit(path, "unified")


@pytest.mark.parametrize("mutation", ["empty", "missing", "duplicate", "substitute"])
def test_missing_critical_parameterization_is_failure(tmp_path, mutation):
    cases = list(REQUIRED_CASES["process"])
    if mutation == "empty":
        cases = []
    elif mutation == "missing":
        cases.pop()
    elif mutation == "duplicate":
        cases[-1] = cases[0]
    else:
        cases[-1] = "nexus_backend.tests.test_fake::test_placeholder"
    with pytest.raises(GateError):
        validate_junit(junit(tmp_path / "results.xml", cases), "process")


def test_unreadable_xml_is_a_gate_failure(tmp_path):
    path = tmp_path / "broken.xml"
    path.write_text("not xml")
    with pytest.raises(GateError):
        validate_junit(path, "unified")


def config():
    return {
        key: f"postgresql://{user}:fixture-secret@127.0.0.1:25496/ak01_local_ak05_bootstrap"
        for key, user in {
            "admin": "postgres",
            "app": "ak05_runtime",
            "worker": "ak05_worker",
            "projection": "ak05_projection",
        }.items()
    }


def test_explicit_complete_loopback_config_passes():
    assert validate_config(config()) == config()


@pytest.mark.parametrize(
    "invalid",
    [
        "remote",
        "default-port",
        "wrong-db",
        "missing-projection",
        "query",
        "different-db",
        "wrong-projection-role",
    ],
)
def test_prepare_refuses_unsafe_or_incomplete_targets(invalid):
    data = config()
    if invalid == "missing-projection":
        data.pop("projection")
    elif invalid == "different-db":
        data["worker"] = data["worker"].replace("bootstrap", "other")
    elif invalid == "wrong-projection-role":
        data["projection"] = data["app"]
    else:
        old, new = {
            "remote": ("127.0.0.1", "cloud.invalid"),
            "default-port": (":25496", ":5432"),
            "wrong-db": ("ak01_local_ak05_bootstrap", "production"),
            "query": ("bootstrap", "bootstrap?host=cloud.invalid"),
        }[invalid]
        data = {key: value.replace(old, new) for key, value in data.items()}
    with pytest.raises(GateError):
        validate_config(data)


def test_report_manifest_requires_all_four_distinct_databases(tmp_path):
    manifest = {"version": 1, "major": 16, "groups": {}}
    for group in GROUPS:
        path = tmp_path / f"{group}.json"
        path.write_text(json.dumps(config()))
        manifest["groups"][group] = str(path)
    with pytest.raises(GateError):
        validate_report_manifest(manifest)


@pytest.mark.parametrize("major", [14, 15, 18, "16", True])
def test_manifest_cannot_mislabel_server_version(major):
    with pytest.raises(GateError):
        validate_report_manifest({"version": 1, "major": major, "groups": {}})


def test_required_suites_keep_critical_recovery_and_cleanup_cases():
    assert sum(map(len, REQUIRED_CASES.values())) == 167
    all_cases = "\n".join(case for cases in REQUIRED_CASES.values() for case in cases)
    for name in [
        "test_pg_late_assistant_overlaps_slow_ocr",
        "test_expired_process_lease",
        "test_process_shutdown_retains_pg",
        "test_real_pg_three_companies_share_first_turns",
        "test_projection_still_cannot_delete_state_metadata",
        "test_pg_runtime_accepts_all_four_owned_entities",
        "test_pg_native_sql_cannot_bypass_entity_contract",
        "test_pg_two_reviewers_only_one_wins",
        "test_pg_native_sql_cannot_forge_evidence",
        "test_pg_inactive_account_cannot_skip_pending_coverage",
        "test_pg_signed_balances_remain_reviewable",
    ]:
        assert name in all_cases


def test_prepare_without_explicit_opt_in_never_opens_a_database(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("prepare must reject before opening any database")

    monkeypatch.setattr(gate, "local_connection", forbidden)
    with pytest.raises(GateError):
        prepare(config(), 16, tmp_path / "config")


@pytest.mark.parametrize("groups", [(), ("../other",), ("regression", "regression")])
def test_preparation_group_selection_is_bounded_before_any_connection(
    tmp_path, monkeypatch, groups
):
    monkeypatch.setattr(
        gate, "local_connection", lambda *_: pytest.fail("must reject before connecting")
    )
    with pytest.raises(GateError):
        prepare(config(), 16, tmp_path / "config", allow_create=True, groups=groups)


def manifest_for(tmp_path):
    manifest = {"version": 1, "major": 16, "groups": {}}
    for group in GROUPS:
        path = tmp_path / f"{group}.json"
        path.write_text(
            json.dumps({key: value.replace("bootstrap", group) for key, value in config().items()})
        )
        manifest["groups"][group] = str(path)
    return manifest


@pytest.mark.parametrize(
    "fault", [None, "exit-code", "missing-xml", "skipped", "missing-case", "candidate-changed"]
)
def test_runner_requires_both_exit_zero_and_complete_actual_evidence(tmp_path, monkeypatch, fault):
    from scripts import migrate_accounting

    monkeypatch.setattr(gate, "assert_server_and_roles", lambda *args, **kwargs: 160015)
    fingerprints = iter(
        [
            {"head": "frozen-test-candidate"},
            {"head": "changed" if fault == "candidate-changed" else "frozen-test-candidate"},
        ]
    )
    monkeypatch.setattr(gate, "candidate_fingerprint", lambda: next(fingerprints))
    monkeypatch.setattr(migrate_accounting, "status", lambda *_: [(152, "已应用")])
    monkeypatch.setenv("PYTEST_ADDOPTS", "-k nothing")
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-inherit")

    def run(command, **kwargs):
        assert kwargs["env"].get("PYTEST_DISABLE_PLUGIN_AUTOLOAD") == "1"
        assert "PYTEST_ADDOPTS" not in kwargs["env"]
        assert "OPENAI_API_KEY" not in kwargs["env"]
        xml = Path(next(arg.split("=", 1)[1] for arg in command if arg.startswith("--junitxml=")))
        group = xml.stem
        if fault != "missing-xml":
            cases = REQUIRED_CASES[group][:-1] if fault == "missing-case" else REQUIRED_CASES[group]
            junit(xml, cases, child="skipped" if fault == "skipped" else None)
        return SimpleNamespace(
            returncode=1 if fault == "exit-code" else 0, stdout="fixture-secret", stderr=""
        )

    monkeypatch.setattr(gate.subprocess, "run", run)
    output = tmp_path / "reports"
    assert run_gate(manifest_for(tmp_path), output) is (fault is None)
    report = json.loads((output / "summary.json").read_text())
    assert report["status"] == ("failed" if fault else "passed")
    assert "fixture-secret" not in "".join(path.read_text() for path in output.iterdir())


def test_driver_log_redaction_hides_url_and_encoded_password():
    data = config()
    data["admin"] = data["admin"].replace("fixture-secret", "one%40secret")
    text = " ".join(data.values()) + " one@secret one%40secret"
    result = sanitized(text, data)
    assert (
        "fixture-secret" not in result
        and "one@secret" not in result
        and "one%40secret" not in result
    )


@pytest.mark.parametrize("fault", ["major", "fsync", "privilege", "membership"])
def test_runtime_server_and_role_checks_are_not_manifest_self_reporting(monkeypatch, fault):
    class Cursor:
        def __init__(self, rows):
            self.rows = rows

        def fetchone(self):
            return self.rows[0] if self.rows else None

        def __iter__(self):
            return iter(self.rows)

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def execute(self, query, values=None):
            if query == "SHOW server_version_num":
                return Cursor([("170011" if fault == "major" else "160015",)])
            if query.startswith("SHOW "):
                return Cursor([("off" if fault == "fsync" else "on",)])
            if "pg_auth_members" in query:
                roles = (
                    {"accounting_projection"}
                    if fault == "membership"
                    else {"accounting_app", "accounting_system"}
                )
                return Cursor([(name,) for name in roles])
            if "rolinherit" in query:
                return Cursor([(True, fault == "privilege", False, False, False, False)])
            return Cursor([(False,) * 5])

    monkeypatch.setattr(gate, "local_connection", lambda *_: Connection())
    with pytest.raises(GateError):
        gate.assert_server_and_roles(config(), 16)
