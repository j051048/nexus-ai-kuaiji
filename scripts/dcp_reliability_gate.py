"""Separate settlement and DCP PG16/17 gates, exact cases and no skips."""

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
    candidate_fingerprint,
    local_connection,
    sanitized,
    validate_config,
)

MANIFEST = Path(__file__).with_name("dcp_reliability_required_cases.json")


def contract(suite):
    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    if data.get("version") != 1 or suite not in {"settlement", "dcp"}:
        raise GateError("Invalid DCP gate manifest")
    result = data["suites"][suite]
    if not result["cases"] or len(set(result["cases"])) != len(result["cases"]):
        raise GateError("Required case identities must be unique and nonempty")
    return result


def validate_junit(path, required):
    try:
        root = ET.parse(path).getroot()
        suites = list(root.iter("testsuite"))
        cases = list(root.iter("testcase"))
        if root.tag not in {"testsuite", "testsuites"} or not suites or not cases:
            raise ValueError()
        if any(list(root.iter(tag)) for tag in ("failure", "error", "skipped")):
            raise ValueError()
        for node in [root, *suites]:
            if any(
                int(node.get(key, "0"))
                for key in ("skipped", "errors", "failures", "disabled", "deselected")
            ):
                raise ValueError()
            if "tests" in node.attrib and int(node.get("tests")) != len(
                list(node.iter("testcase"))
            ):
                raise ValueError()
        actual = [f"{case.get('classname')}::{case.get('name')}" for case in cases]
        if Counter(actual) != Counter(required):
            raise ValueError()
    except (OSError, ET.ParseError, TypeError, ValueError):
        raise GateError("Missing, failed, skipped, duplicated or substituted PG evidence") from None
    return {
        "passed": len(cases),
        "junit_sha256": hashlib.sha256(Path(path).read_bytes()).hexdigest(),
    }


def run(config_path, output, suite):
    selected = contract(suite)
    config = validate_config(json.loads(Path(config_path).read_text(encoding="utf-8-sig")))
    with local_connection(config["admin"]) as conn:
        major = int(conn.execute("SHOW server_version_num").fetchone()[0]) // 10000
    if major not in {16, 17}:
        raise GateError("Actual PostgreSQL 16 or 17 is required")
    assert_server_and_roles(config, major)
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    before = candidate_fingerprint()
    report = {
        "status": "failed",
        "suite": suite,
        "major": major,
        "candidate": before,
        "manifest_sha256": hashlib.sha256(MANIFEST.read_bytes()).hexdigest(),
    }
    xml = output / "results.xml"
    # The isolated runner strips deployment/model credentials and blocks WAN.
    environment = {
        key: value
        for key, value in os.environ.items()
        if key.upper() not in {"PYTEST_ADDOPTS", "PYTEST_PLUGINS"}
    }
    environment.update(PYTEST_DISABLE_PLUGIN_AUTOLOAD="1", PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    try:
        result = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts/test_accounting_postgres_isolated.py"),
                "--config",
                str(Path(config_path).resolve()),
                *[str(ROOT / "nexus_backend/tests" / file) for file in selected["files"]],
                "-q",
                "--tb=short",
                "--junitxml=" + str(xml),
            ],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=1200,
        )
        (output / "run.log").write_text(
            sanitized(result.stdout + result.stderr, config), encoding="utf-8"
        )
        if xml.exists():
            xml.write_text(sanitized(xml.read_text(encoding="utf-8"), config), encoding="utf-8")
        report.update(validate_junit(xml, selected["cases"]))
        if result.returncode or candidate_fingerprint() != before:
            raise GateError("Nonzero process result or candidate changed during verification")
        sys.path.insert(0, str(ROOT / "nexus_backend"))
        from scripts.migrate_accounting import status

        if any(state != "已应用" for _, state in status(config["admin"])):
            raise GateError("Missing or changed migration history")
        report["status"] = "passed"
    except (GateError, subprocess.TimeoutExpired) as error:
        report["reason"] = str(error) if isinstance(error, GateError) else "Gate timeout"
    (output / "summary.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"PG{major} {suite}: {report['status']}")
    return report["status"] == "passed"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", choices=["settlement", "dcp"], required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    try:
        return 0 if run(args.config, args.out, args.suite) else 1
    except Exception:
        print(
            "PG gate failed; inspect local configuration and sanitized evidence.", file=sys.stderr
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
