"""Execute joint recovery on disposable local PG16/17, never a deployment gate.

Reuses C01 backup, identity, private-object and privilege recovery; refuses
skipped/renamed tests and binds every phase to the same application candidate.
Prepare four explicitly isolated clusters before running this command.
"""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
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


def configuration_fingerprint(directory):
    directory = Path(directory)
    names = [
        f"pg{major}-{suffix}.json"
        for major in (16, 17)
        for suffix in ("recovery_source", "recovery-fresh-target")
    ]
    names.append("wsl-bridge-private.json")
    return {name: hashlib.sha256((directory / name).read_bytes()).hexdigest() for name in names}


def run_gate(output):
    output = Path(output).resolve()
    if not output.is_relative_to(ROOT / "tmp_test"):
        raise GateError("Recovery artifacts must stay in the ignored local tmp_test directory")
    output.mkdir(parents=True, exist_ok=False)
    # Database dumps and identity bundles are private, including test passwords.
    sys.path.insert(0, str(ROOT / "nexus_backend"))
    sys.path.insert(0, str(ROOT / "nexus_backend/tests"))
    from c01_identity_fixture import _secure, _private_write

    _secure(output)
    config_root = ROOT / ".codex/c01-final-pg"
    configs = {}
    report = {
        "schema": "p01-recovery-v1",
        "status": "failed",
        "candidate": candidate_fingerprint(),
        "scope": "local-only",
        "phases": {},
    }
    environment = _environment()
    environment.update(C01_FULL_RECOVERY_ACCEPT="1", C01_GOTRUE_ACCEPT="1", C01_GOTRUE_APP="1")
    phase = "environment"

    def unchanged():
        if report["candidate"] != candidate_fingerprint():
            raise GateError("Candidate changed during recovery verification")
        if report["configuration_sha256"] != configuration_fingerprint(config_root):
            raise GateError(
                "Private database or bridge targets changed during recovery verification"
            )

    try:
        report["configuration_sha256"] = configuration_fingerprint(config_root)
        for major in (16, 17):
            for suffix in ("recovery_source", "recovery-fresh-target"):
                config = validate_config(
                    json.loads(
                        (config_root / f"pg{major}-{suffix}.json").read_text(encoding="utf-8-sig")
                    )
                )
                assert_server_and_roles(config, major)
                configs.update(
                    {
                        f"{major}-{suffix}-{key}": val
                        for key, val in config.items()
                        if isinstance(val, str)
                    }
                )
        for phase, filename in (
            ("joint", "test_c01_full_recovery_postgres.py"),
            ("identity", "test_c01_gotrue_recovery.py"),
        ):
            unchanged()
            xml = output / f"{phase}.xml"
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pytest",
                    str(ROOT / "nexus_backend/tests" / filename),
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
                timeout=1200,
            )
            (output / f"{phase}.log").write_text(
                sanitized(result.stdout + result.stderr, configs), encoding="utf-8"
            )
            if xml.exists():
                xml.write_text(
                    sanitized(xml.read_text(encoding="utf-8"), configs), encoding="utf-8"
                )
            report["phases"][phase] = validate_junit(xml, phase)
            if result.returncode:
                raise GateError("Recovery pytest returned a nonzero exit code")
            unchanged()
            if phase == "joint":
                pointers = {}
                report["databases"] = {}
                for major in (16, 17):
                    paths = list(
                        (output / "joint-private").glob(f"*/pg{major}-full-recovery-evidence.json")
                    )
                    if len(paths) != 1:
                        raise GateError(
                            "Recovery requires exactly one full evidence record per version"
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
                _private_write(pointer_path, json.dumps(pointers).encode("utf-8"))
                environment["C01_ARTIFACT_POINTERS_PATH"] = str(pointer_path)
        report["status"] = "passed"
    except subprocess.TimeoutExpired as error:

        def decoded(raw):
            return raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else (raw or "")

        (output / f"{phase}.log").write_text(
            sanitized(decoded(error.stdout) + decoded(error.stderr), configs), encoding="utf-8"
        )
        report["reason"] = f"Recovery phase timed out: {phase}"
    except Exception as error:
        report["reason"] = (
            str(error)
            if isinstance(error, GateError)
            else f"Recovery {phase} failed; inspect private environment"
        )
    (output / "summary.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print("P01 local recovery:", report["status"])
    return report["status"] == "passed"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    try:
        return 0 if run_gate(args.out) else 1
    except Exception:
        print(
            "P01 recovery failed; inspect local evidence. No connection secrets were printed.",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
