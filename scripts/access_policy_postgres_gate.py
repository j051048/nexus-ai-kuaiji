"""Local PG16/17 same-candidate gate; mock/synthetic fixtures, no deployment."""

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.ak05_postgres_gate import (  # noqa: E402
    GateError,
    assert_server_and_roles,
    local_connection,
    validate_config,
)
from scripts.release_candidate import candidate_fingerprint  # noqa: E402

SELECTORS = [
    "nexus_backend/tests/test_access_scope_postgres.py",
    "nexus_backend/tests/test_review_policy_postgres.py",
    "nexus_backend/tests/test_monthly_delivery_postgres.py",
    "nexus_backend/tests/test_business_postgres.py",
]
MANIFEST = Path(__file__).with_name("access_policy_postgres_required_cases.json")


def validate_junit(path):
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    if (
        manifest.get("version") != 1
        or manifest["selectors"] != SELECTORS
        or len(manifest["cases"]) != 81
        or len(set(manifest["cases"])) != 81
    ):
        raise GateError("权限验收范围不一致")
    root = ET.parse(path).getroot()
    cases = list(root.iter("testcase"))
    for node in [root, *root.iter("testsuite")]:
        if any(int(node.get(key, "0")) for key in ("skipped", "errors", "failures", "disabled")):
            raise GateError("权限验收统计包含未执行或失败用例")
    if any(list(root.iter(tag)) for tag in ("skipped", "failure", "error")):
        raise GateError("权限验收存在失败或跳过")
    actual = [f"{case.get('classname')}::{case.get('name')}" for case in cases]
    if Counter(actual) != Counter(manifest["cases"]):
        raise GateError("权限必验用例缺失、重复或被替换")
    return {
        "passed": len(cases),
        "skipped": 0,
        "junit_sha256": hashlib.sha256(Path(path).read_bytes()).hexdigest(),
    }


def run_gate(paths, output):
    sys.path.insert(0, str(ROOT / "nexus_backend"))
    from scripts.migrate_accounting import migrate, status

    output = Path(output).resolve()
    if not output.is_relative_to(ROOT / "tmp_test") or set(paths) != {16, 17}:
        raise GateError("仅允许 tmp_test 新目录及明确的两个本机版本")
    output.mkdir(parents=True, exist_ok=False)
    candidate = candidate_fingerprint()
    report = {
        "status": "failed",
        "candidate": candidate,
        "versions": {},
        "real_model": False,
        "production": False,
    }
    try:
        for major, path in paths.items():
            config_bytes = Path(path).read_bytes()
            config = validate_config(json.loads(config_bytes.decode("utf-8-sig")))
            assert_server_and_roles(config, major)
            with local_connection(config["admin"]) as conn:
                assert int(conn.execute("SHOW server_version_num").fetchone()[0]) // 10000 == major
            migrate(config["admin"])
            if any(state != "已应用" for _, state in status(config["admin"])):
                raise GateError("迁移版本或摘要未对齐")
            xml = output / f"pg{major}.xml"
            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts/test_accounting_postgres_isolated.py"),
                    "--config",
                    str(Path(path).resolve()),
                    *SELECTORS,
                    "-q",
                    "--tb=short",
                    f"--junitxml={xml}",
                ],
                cwd=ROOT,
                capture_output=True,
                timeout=900,
            )
            (output / f"pg{major}.log").write_bytes(result.stdout + result.stderr)
            if result.returncode:
                raise GateError(f"PG{major} 权限用例失败，检查本机运行日志")
            report["versions"][str(major)] = {
                **validate_junit(xml),
                "configuration_sha256": hashlib.sha256(config_bytes).hexdigest(),
            }
            if Path(path).read_bytes() != config_bytes or candidate_fingerprint() != candidate:
                raise GateError("验收期间候选或配置变化")
        report["status"] = "passed"
    finally:
        (output / "summary.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pg16", required=True)
    parser.add_argument("--pg17", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    result = run_gate({16: args.pg16, 17: args.pg17}, args.output)
    print(json.dumps({"status": result["status"], "versions": result["versions"]}))
