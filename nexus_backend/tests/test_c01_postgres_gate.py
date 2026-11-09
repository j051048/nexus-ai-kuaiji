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


def test_timeout_retains_sanitized_partial_evidence(tmp_path, monkeypatch):
    import json
    import subprocess
    from contextlib import contextmanager
    from scripts import c01_postgres_gate as gate
    from scripts import migrate_accounting

    config = {"admin": "postgresql://test:private-ci-secret@127.0.0.1:25616/ak01_local_ak05_test"}
    path = tmp_path / "config.json"
    path.write_text(json.dumps(config))

    @contextmanager
    def connection(*_):
        class FakeConnection:
            def execute(self, *_):
                return self

            def fetchone(self):
                return (160000,)

        yield FakeConnection()

    monkeypatch.setattr(gate, "validate_config", lambda value: value)
    monkeypatch.setattr(gate, "local_connection", connection)
    monkeypatch.setattr(gate, "assert_server_and_roles", lambda *_: None)
    monkeypatch.setattr(gate, "candidate_fingerprint", lambda: {"head": "frozen"})
    monkeypatch.setattr(migrate_accounting, "status", lambda *_: [])

    def timeout(command, **kwargs):
        assert kwargs["timeout"] == 600
        raise subprocess.TimeoutExpired(
            command, 600, output=b"test_first PASSED private-ci-secret", stderr=b"waiting"
        )

    monkeypatch.setattr(gate.subprocess, "run", timeout)
    out = tmp_path / "report"
    assert gate.run_gate(path, out) is False
    log = (out / "c01.log").read_text()
    assert "test_first PASSED" in log and "waiting" in log
    assert "private-ci-secret" not in log
    report = json.loads((out / "summary.json").read_text())
    assert report["status"] == "failed"
    assert report["timeout_seconds"] == 600
    assert report["elapsed_seconds"] >= 0
    assert not report.get("passed")


@pytest.mark.parametrize(
    "fault", [None, "prepare", "same_database", "dirty_database", "candidate", "during_run"]
)
def test_fresh_gate_never_runs_on_source_or_reuses_old_evidence(tmp_path, monkeypatch, fault):
    import json
    from contextlib import contextmanager
    from scripts import c01_postgres_gate as gate
    from test_ak05_postgres_gate import config

    source = config()
    source_path = tmp_path / "source.json"
    source_path.write_text(json.dumps(source))
    private = tmp_path / "private"
    calls = []

    @contextmanager
    def connect(*_):
        class Connection:
            def execute(self, sql):
                self.sql = sql
                return self

            def fetchone(self):
                if self.sql.startswith("SHOW"):
                    return (160015,)
                assert self.sql == "SELECT COUNT(*) FROM accounting.companies"
                return (1 if fault == "dirty_database" else 0,)

        yield Connection()

    def prepare(value, major, destination, **kwargs):
        assert value == source and major == 16
        assert kwargs == {"allow_create": True, "groups": ("regression",)}
        if fault == "prepare":
            raise GateError("fixture-secret must not appear in public evidence")
        destination.mkdir(parents=True, exist_ok=False)
        fresh = (
            value
            if fault == "same_database"
            else {
                key: url.replace("bootstrap", destination.parent.name) for key, url in value.items()
            }
        )
        path = destination / "regression.json"
        path.write_text(json.dumps(fresh))
        manifest = destination / "manifest.json"
        manifest.write_text(json.dumps({"groups": {"regression": str(path)}}))
        return manifest

    def run(path, output):
        calls.append(path)
        output.mkdir()
        (output / "summary.json").write_text(
            json.dumps({"status": "passed", "passed": 34, "candidate": {"source_sha256": "before"}})
        )
        return True

    fingerprints = iter(
        [
            {"source_sha256": "before"},
            {"source_sha256": "changed" if fault == "candidate" else "before"},
            {"source_sha256": "changed" if fault == "during_run" else "before"},
        ]
    )
    monkeypatch.setattr(gate, "candidate_fingerprint", lambda: next(fingerprints))
    monkeypatch.setattr(gate, "local_connection", connect)
    monkeypatch.setattr(gate, "prepare", prepare, raising=False)
    monkeypatch.setattr(gate, "run_gate", run)
    out = tmp_path / "report"
    assert gate.run_fresh_gate(source_path, out, private_root=private) is (fault is None)
    report_text = (out / "summary.json").read_text()
    report = json.loads(report_text)
    assert report["status"] == ("failed" if fault else "passed")
    assert "fixture-secret" not in report_text
    assert source_path.read_text() == json.dumps(source)
    assert len(calls) == (1 if fault in (None, "during_run") else 0)
    with pytest.raises(FileExistsError):
        gate.run_fresh_gate(source_path, out, private_root=private)


def test_cli_uses_a_fresh_database_by_default(monkeypatch, tmp_path):
    import sys
    from scripts import c01_postgres_gate as gate

    called = []
    monkeypatch.setattr(gate, "run_fresh_gate", lambda *a: called.append(a) or True, raising=False)
    monkeypatch.setattr(sys, "argv", ["gate", "--config", "source.json", "--out", str(tmp_path)])
    assert gate.main() == 0
    assert called == [("source.json", str(tmp_path))]
