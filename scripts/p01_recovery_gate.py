"""Execute joint recovery on disposable local PG16/17, never a deployment gate.

Reuses C01 backup, identity, private-object and privilege recovery; refuses
skipped/renamed tests and binds every phase to the same application candidate.
Prepare four explicitly isolated clusters before running this command.
"""

from __future__ import annotations

import argparse
from collections import Counter
from contextlib import contextmanager
import hashlib
import json
import os
import time
from pathlib import Path
import re
import subprocess
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.ak05_postgres_gate import (  # noqa: E402
    GateError,
    sanitized,
    validate_config,
    assert_server_and_roles,
)
from scripts.n02_n03_postgres_gate import _environment  # noqa: E402
from scripts.release_candidate import candidate_fingerprint  # noqa: E402

REQUIRED_CHECKS = (
    "database",
    "originals",
    "identity",
    "permissions",
    "hold",
    "review_export",
    "tax_delivery",
    "no_replay",
)
REQUIRED_CASES = {
    "joint": [
        "nexus_backend.tests.test_c01_full_recovery_postgres::"
        f"test_fresh_cluster_database_objects_signed_identity_and_http_recovery[{major}]"
        for major in (16, 17)
    ],
    "identity": [
        f"nexus_backend.tests.test_c01_gotrue_recovery::{name}[{major}]"
        for name in (
            "test_real_gotrue_identity_password_mfa_and_revocation_survive_restore",
            "test_real_restored_gotrue_drives_restored_accounting_api",
        )
        for major in (16, 17)
    ],
}


def validate_junit(path, phase):
    if phase not in REQUIRED_CASES:
        raise GateError("Unknown recovery phase")
    try:
        root = ET.parse(path).getroot()
        cases = list(root.iter("testcase"))
        if not cases or root.tag not in {"testsuite", "testsuites"}:
            raise GateError("Recovery evidence has no cases")
        for node in [root, *root.iter("testsuite")]:
            if any(
                int(node.get(k, "0")) != 0
                for k in ("skipped", "errors", "failures", "disabled", "deselected")
            ):
                raise GateError("Recovery contains failed or unexecuted tests")
            if "tests" in node.attrib and int(node.get("tests")) != len(
                list(node.iter("testcase"))
            ):
                raise GateError("Recovery test counters disagree")
        if any(list(root.iter(tag)) for tag in ("failure", "error", "skipped")):
            raise GateError("Recovery contains nonpassing tests")
        identities = [f"{c.get('classname')}::{c.get('name')}" for c in cases]
        if Counter(identities) != Counter(REQUIRED_CASES[phase]):
            raise GateError("Exact recovery cases are missing, duplicated or replaced")
    except (OSError, ET.ParseError, ValueError) as error:
        if isinstance(error, GateError):
            raise
        raise GateError("Invalid recovery JUnit") from None
    return {
        "passed": len(cases),
        "junit_sha256": hashlib.sha256(Path(path).read_bytes()).hexdigest(),
    }


def validate_evidence(path, major):
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
        if (
            type(value["major"]) is not int
            or value["major"] != major
            or value["actual_pg_dump_restore"] is not True
        ):
            raise GateError("Recovery database evidence is not for this version")
        for key in ("source_version", "target_version"):
            if not isinstance(value[key], str) or not re.match(
                rf"^{major}\.\d+(?:\s|$)", value[key]
            ):
                raise GateError(
                    "Recovery source and target must match the required PostgreSQL version"
                )
        for key in (
            "migrations",
            "originals",
            "source_users",
            "restored_capability_grants",
            "restored_column_grants",
        ):
            if type(value[key]) is not int or value[key] <= 0:
                raise GateError("Recovery evidence is empty")
        checks = value["p01_checks"]
        if set(checks) != set(REQUIRED_CHECKS) or any(v is not True for v in checks.values()):
            raise GateError("P01 joint business recovery checks are incomplete")
        return value
    except (OSError, ValueError, KeyError, TypeError) as error:
        if isinstance(error, GateError):
            raise
        raise GateError("P01 recovery evidence is unreadable or incomplete") from None


REGRESSION_FILES = (
    "test_c01_identity_recovery.py",
    "test_c01_recovery_security.py",
    "test_c01_restore_safety.py",
    "test_c01_wsl_bridge.py",
    "test_n03_recovery.py",
    "test_p01_local_recovery_env.py",
    "test_p01_recovery_business_contract.py",
    "test_p01_recovery_gate.py",
    "test_p01_recovery_inventory.py",
    "test_recovery_inventory_coverage.py",
    "test_restore_hold.py",
    "test_stage10_backup_recovery.py",
    "test_p01_r2_gate.py",
    "test_legacy_review_recheck.py",
)
MINIMUM_MIGRATION = 190  # permission grants and current human review evidence are mandatory.


def configuration_names():
    return [
        f"pg{major}-{suffix}.json"
        for major in (16, 17)
        for suffix in ("recovery_source", "recovery-fresh-target")
    ] + ["wsl-bridge-private.json"]


def configuration_fingerprint(directory):
    directory = Path(directory)
    names = configuration_names()
    # New runs freeze the auth migration manifest too; old helper tests may not.
    if (directory / "gotrue-migrations-manifest.json").exists():
        names += ["gotrue-migrations-manifest.json"]
    return {name: hashlib.sha256((directory / name).read_bytes()).hexdigest() for name in names}


def freeze_configs(source, destination):
    from scripts.p01_local_recovery_env import secure

    source, destination = Path(source).resolve(), Path(destination)
    if destination.exists() or destination.is_symlink():
        raise GateError("Configuration snapshot already exists; choose a fresh run")
    names = configuration_names()
    if (source / "gotrue-migrations-manifest.json").exists():
        names += ["gotrue-migrations-manifest.json"]
    if any((source / name).is_symlink() or not (source / name).is_file() for name in names):
        raise GateError("Missing or linked recovery configuration")
    expected = configuration_fingerprint(source)
    destination.mkdir(parents=True, mode=0o700)
    secure(destination)
    for name in names:
        # Destination parent is private before any secret byte is copied.
        with (destination / name).open("xb") as output:
            output.write((source / name).read_bytes())
        secure(destination / name)
    if (
        configuration_fingerprint(source) != expected
        or configuration_fingerprint(destination) != expected
    ):
        raise GateError("Configuration changed while taking immutable snapshot")
    return expected


def validate_timeout(value):
    if type(value) is not int or not 30 <= value <= 86400:
        raise GateError("Phase timeout must be an integer between 30 and 86400 seconds")
    return value


def error_diagnostic(error, phase):
    code = (
        "phase_timeout"
        if isinstance(error, subprocess.TimeoutExpired)
        else "contract_rejected"
        if isinstance(error, GateError)
        else "environment_failure"
    )
    return {
        "code": code,
        "phase": phase,
        # Never stringify arbitrary exceptions, which can contain DSNs/passwords.
        "reason": str(error)
        if isinstance(error, GateError)
        else "Inspect the private run environment; no raw connection error is published.",
        "next_action": "Preserve this run; correct the stated phase and prepare a new source/empty-target pair.",
    }


def require_migration_floor(rows):
    versions = [int(str(version).split("_")[0]) for version, _ in rows]
    if not versions or max(versions) < MINIMUM_MIGRATION:
        raise GateError(
            "P01 recovery requires migration 190 or newer (permission and current review evidence); apply all candidate migrations first"
        )


def validate_phase_bindings(phases, candidate, binding):
    if set(phases) != {"regression", "joint", "identity"}:
        raise GateError("Regression, joint recovery and identity phases are all required")
    for phase in phases.values():
        if (
            type(phase.get("passed")) is not int
            or phase["passed"] <= 0
            or phase.get("candidate") != candidate
            or phase.get("configuration_sha256") != binding
        ):
            raise GateError(
                "Every successful phase must bind the same candidate and frozen configuration"
            )


@contextmanager
def claim_run(directory):
    from scripts.p01_local_recovery_env import write_new_private, RecoveryEnvironmentError

    directory = Path(directory)
    claim = {"pid": os.getpid(), "state": "single-use-recovery-gate"}
    try:
        write_new_private(directory / "gate-claim.json", claim)
        write_new_private(directory / "gate-active.json", claim)
    except RecoveryEnvironmentError:
        raise GateError("Run already claimed; prepare a fresh source/empty-target pair") from None
    try:
        yield
    finally:
        (directory / "gate-active.json").unlink()


def validate_phase_evidence(path, junit, phase):
    value = json.loads(Path(path).read_text())
    cases, events = value.get("collected", []), value.get("events", [])
    if (
        value.get("schema") != "p01-pytest-v1"
        or value.get("exitstatus") != 0
        or value.get("deselected") != 0
        or not cases
    ):
        raise GateError("Phase did not execute a complete successful collection")
    ids = [c["nodeid"] for c in cases]
    if len(ids) != len(set(ids)):
        raise GateError("Duplicate collected test identity")
    counts = Counter((e["nodeid"], e["when"]) for e in events)
    expected = Counter((nodeid, stage) for nodeid in ids for stage in ("setup", "call", "teardown"))
    if counts != expected or any(e["outcome"] != "passed" or e.get("wasxfail") for e in events):
        raise GateError("Skipped, failed, duplicated or incomplete test execution")
    # Test parameter labels may change; the integer major and function contract may not.
    if phase in ("joint", "identity"):
        functions = {
            "joint": {"test_fresh_cluster_database_objects_signed_identity_and_http_recovery"},
            "identity": {
                "test_real_gotrue_identity_password_mfa_and_revocation_survive_restore",
                "test_real_restored_gotrue_drives_restored_accounting_api",
            },
        }[phase]
        expected_cases = Counter((f, major) for f in functions for major in (16, 17))
        if Counter((c["function"], c["major"]) for c in cases) != expected_cases:
            raise GateError("Required semantic PG16/17 recovery cases changed")
        module = (
            "test_c01_full_recovery_postgres" if phase == "joint" else "test_c01_gotrue_recovery"
        )
        if any(
            c["module"] != module
            or not c["nodeid"].startswith(f"nexus_backend/tests/{module}.py::")
            for c in cases
        ):
            raise GateError("Required recovery module identity changed")
    elif phase == "regression":
        if {c["module"] + ".py" for c in cases} != set(REGRESSION_FILES):
            raise GateError("Required local regression files are incomplete")
    else:
        raise GateError("Unknown recovery phase")
    tree = ET.parse(junit).getroot()
    if tree.tag not in {"testsuite", "testsuites"}:
        raise GateError("Unrecognized JUnit document")
    for node in [tree, *tree.iter("testsuite")]:
        if any(
            int(node.get(k, "0")) != 0
            for k in ("skipped", "errors", "failures", "disabled", "deselected")
        ):
            raise GateError("JUnit contains unexecuted or failed cases")
        if "tests" in node.attrib and int(node.get("tests")) != len(list(node.iter("testcase"))):
            raise GateError("JUnit counters differ from execution")
    junit_cases = list(tree.iter("testcase"))
    if len(junit_cases) != len(cases) or any(
        list(tree.iter(t)) for t in ("failure", "error", "skipped")
    ):
        raise GateError("JUnit disagrees with executed phase")
    expected_junit = Counter(
        c["nodeid"].split("::")[0].removesuffix(".py").replace("/", ".")
        + "::"
        + c["nodeid"].split("::", 1)[1]
        for c in cases
    )
    if (
        Counter(c.get("classname", "") + "::" + c.get("name", "") for c in junit_cases)
        != expected_junit
    ):
        raise GateError("JUnit cases differ from the actual execution")
    return {
        "passed": len(cases),
        "skipped": 0,
        "junit_sha256": hashlib.sha256(Path(junit).read_bytes()).hexdigest(),
        "execution_sha256": hashlib.sha256(Path(path).read_bytes()).hexdigest(),
        "collection_sha256": hashlib.sha256(json.dumps(cases, sort_keys=True).encode()).hexdigest(),
    }


def run_gate(output, *, config_dir, timeout=3600):
    directory = Path(config_dir).resolve()
    if not any(directory.is_relative_to(ROOT / n) for n in (".codex", "tmp_test")):
        raise GateError("Use private ignored local workspace directories")
    with claim_run(directory):
        return _run_gate(output, config_dir=directory, timeout=timeout)


def _run_gate(output, *, config_dir, timeout=3600):
    timeout = validate_timeout(timeout)
    output = Path(output).resolve()
    config_root = Path(config_dir).resolve()
    if not output.is_relative_to(ROOT / "tmp_test") or not any(
        config_root.is_relative_to(ROOT / n) for n in (".codex", "tmp_test")
    ):
        raise GateError("Use private ignored local workspace directories")
    output.mkdir(parents=True, exist_ok=False)
    sys.path[:0] = [str(ROOT / "nexus_backend"), str(ROOT / "nexus_backend/tests")]
    from c01_identity_fixture import _secure, _private_write
    from c01_wsl_bridge import call as bridge_call
    from scripts.migrate_accounting import status as migration_status

    _secure(output)
    public = output / "reports"
    public.mkdir()
    snapshot = output / "configuration-private"
    configs = {}
    report = {
        "schema": "p01-recovery-v2",
        "status": "failed",
        "candidate": candidate_fingerprint(),
        "scope": "local-only",
        "phases": {},
        "minimum_migration": MINIMUM_MIGRATION,
    }
    environment = {k: v for k, v in _environment().items() if not k.startswith(("P01_", "C01_"))}
    environment.update(
        C01_FULL_RECOVERY_ACCEPT="1",
        C01_GOTRUE_ACCEPT="1",
        C01_GOTRUE_APP="1",
        P01_CONFIG_DIR=str(snapshot),
    )
    phase = "environment"

    def unchanged():
        if report["candidate"] != candidate_fingerprint():
            raise GateError("Candidate changed during acceptance")
        if report["configuration_sha256"] != configuration_fingerprint(snapshot):
            raise GateError("Archived recovery configuration changed")
        if report["configuration_sha256"] != configuration_fingerprint(config_root):
            raise GateError("Selected run configuration changed")
        state = bridge_call(snapshot / "wsl-bridge-private.json", "status")
        if (
            state.get("configuration_sha256")
            != report["configuration_sha256"]["wsl-bridge-private.json"]
        ):
            raise GateError("Active bridge is not the selected immutable run")

    try:
        report["configuration_sha256"] = freeze_configs(config_root, snapshot)
        for major in (16, 17):
            for suffix in ("recovery_source", "recovery-fresh-target"):
                config = validate_config(
                    json.loads(
                        (snapshot / f"pg{major}-{suffix}.json").read_text(encoding="utf-8-sig")
                    )
                )
                assert_server_and_roles(config, major)
                if suffix == "recovery_source":
                    states = list(migration_status(config["admin"]))
                    require_migration_floor(states)
                    if any(state != "已应用" for _, state in states):
                        raise GateError(
                            "Apply all candidate source migrations with matching checksums before acceptance"
                        )
                configs.update(
                    {f"{major}-{suffix}-{k}": v for k, v in config.items() if isinstance(v, str)}
                )
        unchanged()
        for phase, filenames in (
            ("regression", REGRESSION_FILES),
            ("joint", ("test_c01_full_recovery_postgres.py",)),
            ("identity", ("test_c01_gotrue_recovery.py",)),
        ):
            unchanged()
            xml, execution = output / f"{phase}.xml", output / f"{phase}-execution.json"
            environment["P01_PHASE_EVIDENCE"] = str(execution)
            started = time.monotonic()
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pytest",
                    "-p",
                    "scripts.p01_pytest_evidence",
                    *[str(ROOT / "nexus_backend/tests" / filename) for filename in filenames],
                    "-q",
                    "--tb=short",
                    "--junitxml=" + str(xml),
                    "--basetemp=" + str(output / (phase + "-private")),
                ],
                cwd=ROOT,
                env=environment,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
            )
            # Logs stay private, even sanitized. CI uploads only the explicit report directory.
            (output / f"{phase}.log").write_text(
                sanitized(result.stdout + result.stderr, configs), encoding="utf-8"
            )
            if result.returncode:
                raise GateError(f"Phase {phase} exited nonzero; inspect private phase log")
            proof = validate_phase_evidence(execution, xml, phase)
            unchanged()
            report["phases"][phase] = {
                **proof,
                "seconds": round(time.monotonic() - started, 3),
                "candidate": report["candidate"],
                "configuration_sha256": report["configuration_sha256"],
            }
            if phase == "joint":
                pointers, report["databases"] = {}, {}
                for major in (16, 17):
                    paths = list(
                        (output / "joint-private").glob(f"*/pg{major}-full-recovery-evidence.json")
                    )
                    if len(paths) != 1:
                        raise GateError(
                            "Exactly one joint business recovery record per major is required"
                        )
                    evidence = validate_evidence(paths[0], major)
                    pointers[str(major)] = str(paths[0].parent)
                    report["databases"][str(major)] = {
                        k: evidence[k]
                        for k in (
                            "source_version",
                            "target_version",
                            "migrations",
                            "originals",
                            "source_users",
                            "restored_capability_grants",
                            "restored_column_grants",
                            "p01_checks",
                        )
                    }
                pointer_path = output / "combined-artifacts-private.json"
                _private_write(pointer_path, json.dumps(pointers).encode())
                environment["C01_ARTIFACT_POINTERS_PATH"] = str(pointer_path)
        validate_phase_bindings(
            report["phases"], report["candidate"], report["configuration_sha256"]
        )
        unchanged()
        report["local_regression"] = report["phases"]["regression"]
        report["status"] = "passed"
    except Exception as error:
        report["diagnostic"] = error_diagnostic(error, phase)
    # A public result is always an outcome of this invocation, never hand-merged.
    (public / "summary.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {"status": report["status"], "phase": phase, "report": str(public / "summary.json")}
        )
    )
    return report["status"] == "passed"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--all", action="store_true", required=True)
    parser.add_argument("--config-dir", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--phase-timeout", type=int, default=3600)
    args = parser.parse_args()
    try:
        return (
            0 if run_gate(args.out, config_dir=args.config_dir, timeout=args.phase_timeout) else 1
        )
    except Exception as error:
        print(json.dumps(error_diagnostic(error, "setup")))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
