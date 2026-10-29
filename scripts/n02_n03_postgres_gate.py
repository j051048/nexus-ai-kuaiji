"""Run the complete N02/N03 contract on one explicit local PostgreSQL 16/17 target.

This command does not create databases, migrate schemas or change grants. It
reuses the isolated runner and the AK05 target/role checks without changing
either of their contracts. Reports contain candidate identities, never DSNs.
"""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.ak05_postgres_gate import (  # noqa: E402
    GateError,
    assert_server_and_roles,
    sanitized,
    validate_config,
)
from scripts.release_candidate import candidate_fingerprint  # noqa: E402


REQUIRED_PATH = Path(__file__).with_name("n02_n03_postgres_required_cases.json")
SUITES = {
    "n02": ("test_tax_workpaper_postgres.py", "test_n02_tax_concurrency_postgres.py"),
    "n03": ("test_n03_notification_postgres.py", "test_n03_worker_postgres.py"),
}
CASE_COUNTS = {"n02": 45, "n03": 55}
DURABILITY = ("fsync", "synchronous_commit", "full_page_writes")


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise GateError("Duplicate manifest key")
        result[key] = value
    return result


def load_contract(path=REQUIRED_PATH):
    """Read the versioned, reviewed collection; reject partial/redirected suites."""
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"), object_pairs_hook=_unique_object)
        if (
            set(value) != {"version", "suites"}
            or type(value["version"]) is not int
            or value["version"] != 1
            or set(value["suites"]) != set(SUITES)
        ):
            raise GateError("N02/N03 case manifest version or suites do not match")
        for name, files in SUITES.items():
            suite = value["suites"][name]
            cases = suite["cases"]
            allowed = {"nexus_backend.tests." + Path(file).stem for file in files}
            if (
                set(suite) != {"files", "cases"}
                or suite["files"] != list(files)
                or not isinstance(cases, list)
                or len(cases) != CASE_COUNTS[name]
                or any(
                    not isinstance(case, str)
                    or "::" not in case
                    or case.split("::", 1)[0] not in allowed
                    or not case.split("::", 1)[1].startswith("test_")
                    for case in cases
                )
                or len(set(cases)) != len(cases)
            ):
                raise GateError("N02/N03 case manifest is incomplete or redirected")
        return value
    except (OSError, ValueError, TypeError, KeyError) as error:
        if isinstance(error, GateError):
            raise
        raise GateError("N02/N03 case manifest is unreadable or invalid") from None


def contract_digest():
    load_contract()
    return hashlib.sha256(REQUIRED_PATH.read_bytes()).hexdigest()


def validate_junit(path, suite):
    if suite not in SUITES:
        raise GateError("Unknown N02/N03 suite")
    required = load_contract()["suites"][suite]["cases"]
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError):
        raise GateError("N02/N03 JUnit is missing or invalid") from None
    suites = list(root.iter("testsuite"))
    cases = list(root.iter("testcase"))
    if root.tag not in {"testsuites", "testsuite"} or not suites or not cases:
        raise GateError("N02/N03 JUnit has no executed cases")
    for node in [root, *suites]:
        try:
            if any(
                int(node.get(key, "0")) != 0
                for key in ("skipped", "errors", "failures", "disabled", "deselected")
            ):
                raise GateError("N02/N03 contains failed or unexecuted cases")
            if "tests" in node.attrib and int(node.attrib["tests"]) != len(
                list(node.iter("testcase"))
            ):
                raise GateError("N02/N03 JUnit case count is inconsistent")
        except ValueError:
            raise GateError("N02/N03 JUnit counters are invalid") from None
    if any(list(root.iter(tag)) for tag in ("failure", "error", "skipped")):
        raise GateError("N02/N03 JUnit contains a nonpassing result")
    identities = [f"{case.get('classname')}::{case.get('name')}" for case in cases]
    if Counter(identities) != Counter(required):
        raise GateError("N02/N03 required case identities were omitted, duplicated or replaced")
    return {
        "passed": len(cases),
        "junit_sha256": hashlib.sha256(Path(path).read_bytes()).hexdigest(),
    }


def migration_status(url):
    sys.path.insert(0, str(ROOT / "nexus_backend"))
    from scripts.migrate_accounting import status

    return status(url)


def _verified_migrations(config):
    states = list(migration_status(config["admin"]))
    if (
        not states
        or len({version for version, _ in states}) != len(states)
        or any(state != "已应用" for _, state in states)
    ):
        raise GateError("Migrations must all be applied with matching checksums")
    return states


def _environment():
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.upper().startswith(
            ("ACCOUNTING_", "SUPABASE_", "OPENAI_", "DEEPSEEK_", "ZEABUR_", "VERCEL_", "PG")
        )
        and key.upper()
        not in {
            "DATABASE_URL",
            "HTTP_PROXY",
            "HTTPS_PROXY",
            "ALL_PROXY",
            "PYTEST_ADDOPTS",
            "PYTEST_PLUGINS",
        }
    }
    environment.update(PYTEST_DISABLE_PLUGIN_AUTOLOAD="1", PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    return environment


def run_gate(config_path, output, *, major):
    if type(major) is not int or major not in {16, 17}:
        raise GateError("N02/N03 requires actual PostgreSQL 16 or 17")
    config_path = Path(config_path).resolve()
    config = validate_config(json.loads(config_path.read_text(encoding="utf-8-sig")))
    output = Path(output).resolve()
    if config_path.is_relative_to(output):
        raise GateError("Private local configuration must remain outside report artifacts")
    output.mkdir(parents=True, exist_ok=False)
    report = {
        "schema": "n02-n03-pg-gate-v1",
        "status": "failed",
        "major": major,
        "suites": {name: {"status": "not_run"} for name in SUITES},
        "roles_checked": False,
    }

    def unchanged():
        if candidate_fingerprint() != report["candidate"]:
            raise GateError("Candidate source or migrations changed during verification")
        if contract_digest() != report["required_cases_sha256"]:
            raise GateError("Required case manifest changed during verification")

    current = None
    try:
        report["candidate"] = candidate_fingerprint()
        report["required_cases_sha256"] = contract_digest()
        contract = load_contract()
        actual = assert_server_and_roles(config, major)
        if type(actual) is not int or actual // 10000 != major:
            raise GateError("Actual PostgreSQL version does not match the requested gate")
        report.update(server_version_num=actual, roles_checked=True)
        report["durability"] = dict.fromkeys(DURABILITY, "on")
        migration_snapshot = _verified_migrations(config)
        report["migrations"] = {
            "status": "verified",
            "count": len(migration_snapshot),
            "digest": report["candidate"]["migration_sha256"],
        }
        for current in SUITES:
            unchanged()
            if _verified_migrations(config) != migration_snapshot:
                raise GateError("Database migration history changed during verification")
            xml = output / f"{current}.xml"
            command = [
                sys.executable,
                str(ROOT / "scripts/test_accounting_postgres_isolated.py"),
                "--config",
                str(config_path),
                *(
                    str(ROOT / "nexus_backend/tests" / file)
                    for file in contract["suites"][current]["files"]
                ),
                "-q",
                "--junitxml=" + str(xml),
            ]
            try:
                result = subprocess.run(
                    command,
                    cwd=ROOT,
                    env=_environment(),
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    capture_output=True,
                    timeout=600,
                )
                (output / f"{current}.log").write_text(
                    sanitized(result.stdout + result.stderr, config), encoding="utf-8"
                )
            finally:
                if xml.exists():
                    xml.write_text(
                        sanitized(xml.read_text(encoding="utf-8"), config), encoding="utf-8"
                    )
            if result.returncode:
                raise GateError("N02/N03 pytest returned a nonzero result")
            checked = validate_junit(xml, current)
            unchanged()
            if _verified_migrations(config) != migration_snapshot:
                raise GateError("Database migration history changed during verification")
            report["suites"][current] = {"status": "passed", **checked}
            print(f"PG{major} {current}: passed", flush=True)
        unchanged()
        assert_server_and_roles(config, major)
        report["status"] = "passed"
    except Exception as error:
        reason = (
            sanitized(str(error), config)
            if isinstance(error, GateError)
            else "N02/N03 exceeded 600 seconds"
            if isinstance(error, subprocess.TimeoutExpired)
            else "N02/N03 verification could not complete; connection details are hidden"
        )
        report["reason"] = reason
        if current is not None:
            report["suites"][current] = {"status": "failed", "reason": reason}
    report["passed"] = sum(item.get("passed", 0) for item in report["suites"].values())
    (output / "summary.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"PG{major} N02/N03: {report['status']}", flush=True)
    return report["status"] == "passed"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--major", required=True, type=int, choices=[16, 17])
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    try:
        return 0 if run_gate(args.config, args.out, major=args.major) else 1
    except Exception:
        print("N02/N03 gate setup failed; private connection details are hidden", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
