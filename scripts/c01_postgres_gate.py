"""Independent C01 PG16/17 gate; never changes the frozen AK05 167-case gate."""

import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from urllib.parse import urlsplit
from uuid import uuid4
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.ak05_postgres_gate import (
    GateError,
    assert_server_and_roles,
    local_connection,
    prepare,
    sanitized,
    validate_config,
)
from scripts.release_candidate import candidate_fingerprint

REQUIRED_PATH = Path(__file__).with_name("c01_postgres_required_cases.json")
REQUIRED_MANIFEST = json.loads(REQUIRED_PATH.read_text(encoding="utf-8"))
REQUIRED_CASES = REQUIRED_MANIFEST["cases"]
if (
    REQUIRED_MANIFEST.get("version") != 1
    or len(REQUIRED_CASES) != 34
    or len(set(REQUIRED_CASES)) != 34
):
    raise GateError("C01 frozen case manifest is invalid")


def validate_junit(path):
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError):
        raise GateError("C01 JUnit is missing or invalid") from None
    suites = list(root.iter("testsuite"))
    cases = list(root.iter("testcase"))
    if root.tag not in {"testsuites", "testsuite"} or not suites or not cases:
        raise GateError("C01 JUnit has no executed cases")
    for node in [root, *suites]:
        try:
            if any(
                int(node.get(key, "0")) != 0
                for key in ("skipped", "errors", "failures", "disabled", "deselected")
            ):
                raise GateError("C01 contains failed or unexecuted cases")
            if "tests" in node.attrib and int(node.attrib["tests"]) != len(
                list(node.iter("testcase"))
            ):
                raise GateError("C01 case count is inconsistent")
        except ValueError:
            raise GateError("C01 JUnit counters are invalid") from None
    if any(list(root.iter(tag)) for tag in ("failure", "error", "skipped")):
        raise GateError("C01 case result is not passing")
    identities = [f"{case.get('classname')}::{case.get('name')}" for case in cases]
    if Counter(identities) != Counter(REQUIRED_CASES):
        raise GateError("C01 case identities changed, duplicated, or were omitted")
    return {
        "passed": len(cases),
        "junit_sha256": hashlib.sha256(Path(path).read_bytes()).hexdigest(),
    }


def run_gate(config_path, output):
    config = validate_config(json.loads(Path(config_path).read_text(encoding="utf-8-sig")))
    with local_connection(config["admin"]) as connection:
        major = int(connection.execute("SHOW server_version_num").fetchone()[0]) // 10000
    if major not in {16, 17}:
        raise GateError("C01 requires actual PostgreSQL 16 or 17")
    assert_server_and_roles(config, major)
    sys.path.insert(0, str(ROOT / "nexus_backend"))
    from scripts.migrate_accounting import status

    if any(state != "已应用" for _, state in status(config["admin"])):
        raise GateError("C01 requires complete unchanged migrations")
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    # Full real-transaction fixtures need more than five minutes on shared CI
    # runners. Keep a finite budget and exact-case/no-skip acceptance unchanged.
    timeout_seconds = 600
    started = time.monotonic()
    report = {
        "major": major,
        "status": "failed",
        "candidate": candidate_fingerprint(),
        "required_cases_sha256": hashlib.sha256(REQUIRED_PATH.read_bytes()).hexdigest(),
        "timeout_seconds": timeout_seconds,
    }
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
    xml = output / "c01.xml"
    try:
        result = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts/test_accounting_postgres_isolated.py"),
                "--config",
                str(Path(config_path).resolve()),
                str(ROOT / "nexus_backend/tests/test_c01_remaining_postgres.py"),
                "-q",
                "-o",
                "faulthandler_timeout=120",
                "--durations=10",
                "--junitxml=" + str(xml),
            ],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
        )
        (output / "c01.log").write_text(
            sanitized(result.stdout + result.stderr, config), encoding="utf-8"
        )
        if xml.exists():
            xml.write_text(sanitized(xml.read_text(encoding="utf-8"), config), encoding="utf-8")
        report.update(validate_junit(xml))
        if result.returncode:
            raise GateError("C01 pytest returned a nonzero result")
        if report["candidate"] != candidate_fingerprint():
            raise GateError("C01 candidate changed during verification")
        report["status"] = "passed"
    except (GateError, subprocess.TimeoutExpired) as error:
        if isinstance(error, subprocess.TimeoutExpired):
            # TimeoutExpired may carry bytes even with text=True. Preserve the
            # completed work/stack dump, but never upload local credentials.
            chunks = [
                part.decode("utf-8", errors="replace") if isinstance(part, bytes) else part or ""
                for part in (error.stdout, error.stderr)
            ]
            (output / "c01.log").write_text(sanitized("".join(chunks), config), encoding="utf-8")
        report["reason"] = (
            str(error)
            if isinstance(error, GateError)
            else f"C01 verification exceeded {timeout_seconds} seconds"
        )
    report["elapsed_seconds"] = round(time.monotonic() - started, 3)
    (output / "summary.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"PG{major} C01: {report['status']}", flush=True)
    return report["status"] == "passed"


def run_fresh_gate(config_path, output, *, private_root=None):
    """Borrow local cluster credentials, never its existing business database.

    Every invocation creates one disposable database via the existing AK05
    preparation path. Keep failed databases/configs for diagnosis; never drop,
    reset or rotate credentials of someone else's run. Public reports contain
    only hashes, while credentials remain in a separate ignored run directory.
    """
    output = Path(output).resolve()
    private_root = Path(private_root or ROOT / "tmp_test/c01-private").resolve()
    if private_root == output or output in private_root.parents:
        raise GateError("私有配置不得放在公开报告目录内")
    output.mkdir(parents=True, exist_ok=False)
    report = {"schema": "c01-fresh-gate-v1", "status": "failed", "phase": "prepare"}
    source = {}
    try:
        report["candidate"] = candidate_fingerprint()
        source = validate_config(json.loads(Path(config_path).read_text(encoding="utf-8-sig")))
        with local_connection(source["admin"]) as conn:
            major = int(conn.execute("SHOW server_version_num").fetchone()[0]) // 10000
        if major not in {16, 17}:
            raise GateError("C01 requires actual PostgreSQL 16 or 17")
        report["major"] = major
        run_id = uuid4().hex
        manifest_path = prepare(
            dict(source),
            major,
            private_root / run_id / "config",
            allow_create=True,
            groups=("regression",),
        )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        fresh_path = Path(manifest["groups"]["regression"])
        fresh = validate_config(json.loads(fresh_path.read_text(encoding="utf-8")))
        if urlsplit(source["admin"]).path == urlsplit(fresh["admin"]).path:
            raise GateError("C01 must not run on its source database")
        with local_connection(fresh["admin"]) as conn:
            if conn.execute("SELECT COUNT(*) FROM accounting.companies").fetchone()[0] != 0:
                raise GateError("C01 requires a fresh database without previous fixtures")
        report["environment"] = {
            "run_id": run_id,
            "fresh_database": True,
            "initial_companies": 0,
            "configuration_sha256": hashlib.sha256(fresh_path.read_bytes()).hexdigest(),
            "retained_for_diagnosis": True,
        }
        if report["candidate"] != candidate_fingerprint():
            raise GateError("C01 candidate changed during environment preparation")
        report["phase"] = "verification"
        passed = run_gate(fresh_path, output / "verification")
        inner = json.loads((output / "verification/summary.json").read_text(encoding="utf-8"))
        report["verification"] = inner
        if (
            inner.get("candidate") != report["candidate"]
            or candidate_fingerprint() != report["candidate"]
        ):
            raise GateError("C01 candidate changed during verification")
        for key in (
            "passed",
            "junit_sha256",
            "required_cases_sha256",
            "elapsed_seconds",
            "timeout_seconds",
        ):
            if key in inner:
                report[key] = inner[key]
        report["status"] = "passed" if passed else "failed"
    except Exception as error:
        report["error_type"] = type(error).__name__
        report["reason"] = (
            sanitized(str(error), source)
            if isinstance(error, GateError)
            else ("Local preparation failed; preserve the private run and inspect its environment.")
        )
    (output / "summary.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"C01 fresh environment: {report['status']}", flush=True)
    return report["status"] == "passed"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    try:
        return 0 if run_fresh_gate(args.config, args.out) else 1
    except Exception:
        print(
            "C01 gate failed; inspect the sanitized report and approved local configuration.",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
