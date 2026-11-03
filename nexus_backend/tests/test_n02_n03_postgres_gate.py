"""The N02/N03 gate must reject green-looking incomplete or stale evidence."""

import json
from pathlib import Path
from types import SimpleNamespace
import xml.etree.ElementTree as ET

import pytest

from scripts import n02_n03_postgres_gate as gate


def junit(path, cases, *, child=None):
    root = ET.Element("testsuites", tests=str(len(cases)), failures="0", errors="0")
    suite = ET.SubElement(root, "testsuite", tests=str(len(cases)))
    for identity in cases:
        classname, name = identity.split("::", 1)
        case = ET.SubElement(suite, "testcase", classname=classname, name=name)
        if child:
            ET.SubElement(case, child)
            child = None
    ET.ElementTree(root).write(path, encoding="utf-8")
    return path


@pytest.mark.parametrize("suite,count", [("n02", 45), ("n03", 56)])
def test_all_collected_parameterized_cases_are_required(tmp_path, suite, count):
    contract = gate.load_contract()
    cases = contract["suites"][suite]["cases"]
    assert len(cases) == count
    checked = gate.validate_junit(junit(tmp_path / "pass.xml", cases), suite)
    assert checked["passed"] == count
    assert len(checked["junit_sha256"]) == 64


def test_provider_receipt_permission_case_is_required():
    assert (
        "nexus_backend.tests.test_n03_notification_postgres::"
        "test_provider_receipt_uses_worker_insert_and_app_tenant_read"
    ) in gate.load_contract()["suites"]["n03"]["cases"]


@pytest.mark.parametrize("suite", ["n02", "n03"])
@pytest.mark.parametrize("mutation", ["empty", "missing", "duplicate", "substitute"])
def test_zero_missing_duplicate_or_replaced_case_rejected(tmp_path, suite, mutation):
    cases = list(gate.load_contract()["suites"][suite]["cases"])
    if mutation == "empty":
        cases = []
    elif mutation == "missing":
        cases.pop()
    elif mutation == "duplicate":
        cases[-1] = cases[0]
    else:
        cases[-1] = "nexus_backend.tests.test_fake::test_placeholder"
    with pytest.raises(gate.GateError):
        gate.validate_junit(junit(tmp_path / "invalid.xml", cases), suite)


@pytest.mark.parametrize("suite", ["n02", "n03"])
@pytest.mark.parametrize("child", ["skipped", "failure", "error"])
def test_return_zero_cannot_hide_nonpassing_testcase(tmp_path, suite, child):
    with pytest.raises(gate.GateError):
        gate.validate_junit(
            junit(
                tmp_path / "case.xml", gate.load_contract()["suites"][suite]["cases"], child=child
            ),
            suite,
        )


@pytest.mark.parametrize("where", ["root", "suite"])
@pytest.mark.parametrize(
    "key,value",
    [
        ("skipped", "1"),
        ("failures", "-1"),
        ("errors", "no"),
        ("disabled", "1"),
        ("deselected", "1"),
        ("tests", "1"),
    ],
)
def test_inconsistent_counters_rejected(tmp_path, where, key, value):
    path = junit(tmp_path / "counter.xml", gate.load_contract()["suites"]["n02"]["cases"])
    tree = ET.parse(path)
    (tree.getroot() if where == "root" else tree.getroot()[0]).set(key, value)
    tree.write(path)
    with pytest.raises(gate.GateError):
        gate.validate_junit(path, "n02")


@pytest.mark.parametrize(
    "body",
    ["<broken", "<testsuites/>", '<testsuite tests="0"/>', '<wrong><testsuite tests="0"/></wrong>'],
)
def test_invalid_or_empty_xml_is_never_success(tmp_path, body):
    path = tmp_path / "broken.xml"
    path.write_text(body)
    with pytest.raises(gate.GateError):
        gate.validate_junit(path, "n02")


def test_unrecognized_suite_and_unreadable_xml_fail_closed(tmp_path):
    with pytest.raises(gate.GateError):
        gate.validate_junit(tmp_path / "missing.xml", "n02")
    with pytest.raises(gate.GateError):
        gate.validate_junit(tmp_path / "missing.xml", "unknown")


@pytest.mark.parametrize(
    "mutation", ["version", "suite", "files", "empty", "duplicate", "foreign-case"]
)
def test_contract_cannot_weaken_or_redirect_frozen_suite(tmp_path, mutation):
    value = gate.load_contract()
    if mutation == "version":
        value["version"] = 2
    elif mutation == "suite":
        value["suites"].pop("n03")
    elif mutation == "files":
        value["suites"]["n02"]["files"] = ["../../outside.py"]
    elif mutation == "empty":
        value["suites"]["n02"]["cases"] = []
    elif mutation == "duplicate":
        value["suites"]["n02"]["cases"][-1] = value["suites"]["n02"]["cases"][0]
    else:
        value["suites"]["n02"]["cases"][-1] = "nexus_backend.tests.test_fake::test_fake"
    path = tmp_path / "contract.json"
    path.write_text(json.dumps(value))
    with pytest.raises(gate.GateError):
        gate.load_contract(path)


def candidate():
    return {
        "schema": "release-candidate-v1",
        "head": "a" * 40,
        "source_files": 200,
        "source_sha256": "b" * 64,
        "migration_files": 10,
        "migration_sha256": "c" * 64,
    }


@pytest.fixture
def execution(tmp_path, monkeypatch):
    config = {
        key: f"postgresql://{user}:local-secret@127.0.0.1:55432/ak01_local_ak05_test"
        for key, user in {
            "admin": "postgres",
            "app": "ak05_runtime",
            "worker": "ak05_worker",
            "projection": "ak05_projection",
        }.items()
    }
    path = tmp_path / "config.json"
    path.write_text(json.dumps(config))
    monkeypatch.setattr(gate, "candidate_fingerprint", candidate)
    monkeypatch.setattr(gate, "assert_server_and_roles", lambda c, major: major * 10000 + 11)
    monkeypatch.setattr(
        gate, "migration_status", lambda url: [("001.sql", "已应用"), ("002.sql", "已应用")]
    )
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        suite = "n02" if any("test_tax_workpaper_postgres.py" in arg for arg in command) else "n03"
        xml = Path(next(arg.split("=", 1)[1] for arg in command if arg.startswith("--junitxml=")))
        junit(xml, gate.load_contract()["suites"][suite]["cases"])
        return SimpleNamespace(returncode=0, stdout="success", stderr="")

    monkeypatch.setattr(gate.subprocess, "run", run)
    return SimpleNamespace(
        path=path, output=tmp_path / "result", config=config, calls=calls, run=run
    )


def test_both_suites_pass_on_exact_actual_major_and_safe_runner(execution):
    assert gate.run_gate(execution.path, execution.output, major=17)
    report = json.loads((execution.output / "summary.json").read_text())
    assert report["schema"] == "n02-n03-pg-gate-v1"
    assert report["major"] == 17 and report["server_version_num"] == 170011
    assert report["migrations"]["status"] == "verified"
    assert report["migrations"]["count"] == 2
    assert report["migrations"]["digest"] == candidate()["migration_sha256"]
    assert report["roles_checked"] is True
    assert report["durability"] == {
        key: "on" for key in ("fsync", "synchronous_commit", "full_page_writes")
    }
    assert {key: result["passed"] for key, result in report["suites"].items()} == {
        "n02": 45,
        "n03": 56,
    }
    assert len(execution.calls) == 2
    assert all(
        any("test_accounting_postgres_isolated.py" in arg for arg in call[0])
        for call in execution.calls
    )
    assert all(call[1]["env"]["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] == "1" for call in execution.calls)


@pytest.mark.parametrize(
    "failure",
    [
        "returncode",
        "timeout",
        "skip",
        "missing-xml",
        "drift",
        "manifest-drift",
        "migration",
        "role",
        "changed-migration",
    ],
)
def test_failed_checks_never_produce_passed_summary(execution, monkeypatch, failure):
    def run(command, **kwargs):
        result = execution.run(command, **kwargs)
        if failure == "returncode":
            result.returncode = 3
        elif failure == "timeout":
            raise gate.subprocess.TimeoutExpired(command, 300)
        elif failure == "skip":
            path = Path(
                next(arg.split("=", 1)[1] for arg in command if arg.startswith("--junitxml="))
            )
            tree = ET.parse(path)
            ET.SubElement(tree.getroot()[0][0], "skipped")
            tree.write(path)
        elif failure == "missing-xml":
            Path(
                next(arg.split("=", 1)[1] for arg in command if arg.startswith("--junitxml="))
            ).unlink()
        elif failure == "drift":
            monkeypatch.setattr(
                gate, "candidate_fingerprint", lambda: {**candidate(), "source_sha256": "d" * 64}
            )
        elif failure == "manifest-drift":
            monkeypatch.setattr(gate, "contract_digest", lambda: "e" * 64)
        elif failure == "changed-migration":
            monkeypatch.setattr(gate, "migration_status", lambda url: [("001.sql", "摘要不一致")])
        return result

    monkeypatch.setattr(gate.subprocess, "run", run)
    if failure == "migration":
        monkeypatch.setattr(gate, "migration_status", lambda url: [("001.sql", "未应用")])
    if failure == "role":

        def bad_role(*args):
            raise gate.GateError("restricted role required")

        monkeypatch.setattr(gate, "assert_server_and_roles", bad_role)
    assert not gate.run_gate(execution.path, execution.output, major=16)
    report = json.loads((execution.output / "summary.json").read_text())
    assert report["status"] == "failed"


def test_invalid_major_does_not_connect_or_execute(execution):
    with pytest.raises(gate.GateError):
        gate.run_gate(execution.path, execution.output, major=14)
    assert not execution.calls


@pytest.mark.parametrize("actual", [140011, 170011, True])
def test_server_version_cannot_be_supplied_by_label(execution, monkeypatch, actual):
    monkeypatch.setattr(gate, "assert_server_and_roles", lambda *args: actual)
    assert not gate.run_gate(execution.path, execution.output, major=16)
    assert not execution.calls


@pytest.mark.parametrize("states", [[], [(1, "已应用"), (1, "已应用")], [(1, "未来迁移")]])
def test_empty_duplicate_or_future_migration_history_blocks_before_pytest(
    execution, monkeypatch, states
):
    monkeypatch.setattr(gate, "migration_status", lambda url: states)
    assert not gate.run_gate(execution.path, execution.output, major=16)
    assert not execution.calls


def test_existing_evidence_directory_is_not_overwritten(execution):
    execution.output.mkdir()
    original = execution.output / "summary.json"
    original.write_text("preserved")
    with pytest.raises(FileExistsError):
        gate.run_gate(execution.path, execution.output, major=16)
    assert original.read_text() == "preserved"
    assert not execution.calls


def test_report_and_logs_redact_local_credentials(execution, monkeypatch):
    def run(command, **kwargs):
        result = execution.run(command, **kwargs)
        result.stdout = execution.config["admin"] + " local-secret"
        return result

    monkeypatch.setattr(gate.subprocess, "run", run)
    assert gate.run_gate(execution.path, execution.output, major=16)
    text = "".join(path.read_text() for path in execution.output.iterdir())
    assert "local-secret" not in text
    assert "postgresql://" not in text


def test_untrusted_environment_does_not_select_or_skip_cases(execution, monkeypatch):
    monkeypatch.setenv("ACCOUNTING_DATABASE_URL", "postgresql://foreign")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "foreign-secret")
    monkeypatch.setenv("PYTEST_ADDOPTS", "-k nothing")
    monkeypatch.setenv("PYTEST_PLUGINS", "foreign_plugin")
    monkeypatch.setenv("HTTPS_PROXY", "https://foreign.invalid")
    assert gate.run_gate(execution.path, execution.output, major=16)
    env = execution.calls[0][1]["env"]
    for key in (
        "ACCOUNTING_DATABASE_URL",
        "SUPABASE_SERVICE_ROLE_KEY",
        "PYTEST_ADDOPTS",
        "PYTEST_PLUGINS",
        "HTTPS_PROXY",
    ):
        assert key not in env
