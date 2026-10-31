"""Same-candidate PG16/17 delivery diagnosis gate. Disposable loopback only.

Reuse the existing isolated runner, migration/role checks and candidate identity.
No provisioning, production credentials, real providers, push or deployment.
"""

from __future__ import annotations

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
    sanitized,
    validate_config,
)
from scripts.n02_n03_postgres_gate import _environment, _verified_migrations  # noqa: E402
from scripts.release_candidate import candidate_fingerprint  # noqa: E402

SELECTORS = [
    "nexus_backend/tests/test_assistant_delivery_postgres.py",
    "nexus_backend/tests/test_stage15_conversations_postgres.py",
    "nexus_backend/tests/test_stage15_assistant_meter_postgres.py",
    "nexus_backend/tests/test_intelligence_workflow_postgres.py::test_pg_ai_full_pipeline_masks_proposals_and_requires_independent_accounting_review",
]
CONTRACT = Path(__file__).with_name("assistant_delivery_postgres_required_cases.json")
RLS_TABLES = [
    "books",
    "assistant_conversations",
    "assistant_turns",
    "assistant_messages",
    "service_plan_versions",
    "internal_close_runs",
    "monthly_delivery_packages",
    "monthly_delivery_acknowledgements",
    "model_costs",
]


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_contract(path=CONTRACT):
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
        if (
            value["version"] != 5
            or type(value["version"]) is not int
            or value["selectors"] != SELECTORS
        ):
            raise ValueError
        cases = value["cases"]
        if (
            len(cases) != 47
            or len(set(cases)) != 47
            or any(not isinstance(row, str) or "::test_" not in row for row in cases)
        ):
            raise ValueError
        return value
    except (OSError, KeyError, TypeError, ValueError):
        raise GateError("助手 PG 必验用例清单不完整或版本不符") from None


def validate_junit(path):
    try:
        root = ET.parse(path).getroot()
        cases = list(root.iter("testcase"))
        for node in [root, *root.iter("testsuite")]:
            if any(
                int(node.get(key, "0")) != 0
                for key in ("skipped", "errors", "failures", "disabled", "deselected")
            ):
                raise ValueError
            if "tests" in node.attrib and int(node.attrib["tests"]) != len(
                list(node.iter("testcase"))
            ):
                raise ValueError
        if any(list(root.iter(tag)) for tag in ("failure", "error", "skipped")):
            raise ValueError
        identities = [f"{row.get('classname')}::{row.get('name')}" for row in cases]
        if Counter(identities) != Counter(load_contract()["cases"]):
            raise ValueError
        return {"passed": len(cases), "skipped": 0, "junit_sha256": sha(path)}
    except (OSError, ET.ParseError, KeyError, TypeError, ValueError):
        raise GateError("助手 PG 用例失败、跳过、缺失、重复或被替换") from None


def run_gate(paths, output, timeout=600):
    if set(paths) != {16, 17} or type(timeout) is not int or not 60 <= timeout <= 1800:
        raise GateError("必须同时提供 PG16/17，超时范围为60–1800秒")
    paths = {major: Path(path).resolve() for major, path in paths.items()}
    output = Path(output).resolve()
    if any(path.is_relative_to(output) for path in paths.values()):
        raise GateError("私有配置不得位于报告目录")
    if output.is_relative_to(ROOT) and not output.is_relative_to(ROOT / "tmp_test"):
        raise GateError("仓库内报告只能写入 tmp_test 新目录")
    configs = {
        major: validate_config(json.loads(path.read_text(encoding="utf-8-sig")))
        for major, path in paths.items()
    }
    digests = {major: sha(path) for major, path in paths.items()}
    load_contract()
    output.mkdir(parents=True, exist_ok=False)
    report = {
        "schema": "assistant-delivery-pg-v1",
        "status": "failed",
        "candidate": candidate_fingerprint(),
        "required_cases_sha256": sha(CONTRACT),
        "versions": {},
        "real_model_evaluated": False,
    }

    def unchanged():
        if (
            candidate_fingerprint() != report["candidate"]
            or sha(CONTRACT) != report["required_cases_sha256"]
        ):
            raise GateError("运行中候选或必验用例变化")
        if any(sha(paths[major]) != digests[major] for major in paths):
            raise GateError("运行中私有配置变化")

    try:
        for major, config in configs.items():
            unchanged()
            actual = assert_server_and_roles(config, major)
            migrations = _verified_migrations(config)
            with local_connection(config["admin"]) as conn:
                rows = conn.execute(
                    "SELECT relname,relrowsecurity,relforcerowsecurity FROM pg_class JOIN pg_namespace n ON n.oid=relnamespace WHERE n.nspname='accounting' AND relname=ANY(%s)",
                    (RLS_TABLES,),
                ).fetchall()
            if {row[0] for row in rows} != set(RLS_TABLES) or any(not all(row[1:]) for row in rows):
                raise GateError("助手及交付来源表必须启用并强制 RLS")
            info = {
                "status": "running",
                "server_version_num": actual,
                "configuration_sha256": digests[major],
                "roles_verified": True,
                "force_rls": sorted(RLS_TABLES),
                "durability": dict.fromkeys(
                    ("fsync", "synchronous_commit", "full_page_writes"), "on"
                ),
                "migrations": {"count": len(migrations), "all_checksums_match": True},
            }
            report["versions"][str(major)] = info
            xml = output / f"pg{major}.xml"
            command = [
                sys.executable,
                str(ROOT / "scripts/test_accounting_postgres_isolated.py"),
                "--config",
                str(paths[major]),
                *SELECTORS,
                "-q",
                "-p",
                "no:cacheprovider",
                "--tb=short",
                f"--junitxml={xml}",
            ]
            result = subprocess.run(
                command,
                cwd=ROOT,
                env=_environment(),
                capture_output=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                check=False,
            )
            log = output / f"pg{major}.log"
            log.write_text(sanitized(result.stdout + result.stderr, config), encoding="utf-8")
            if xml.exists():
                xml.write_text(sanitized(xml.read_text(encoding="utf-8"), config), encoding="utf-8")
            if result.returncode:
                raise GateError(f"PG{major} 测试失败，请查看脱敏日志")
            info.update(validate_junit(xml), status="passed", log_sha256=sha(log))
            unchanged()
            if _verified_migrations(config) != migrations:
                raise GateError("运行中迁移台账变化")
        report["status"] = "passed"
    except (GateError, OSError, ValueError, subprocess.TimeoutExpired) as error:
        report["reason"] = str(error) if isinstance(error, GateError) else type(error).__name__
    finally:
        (output / "summary.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pg16", required=True)
    parser.add_argument("--pg17", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--timeout", type=int, default=600)
    args = parser.parse_args()
    result = run_gate({16: args.pg16, 17: args.pg17}, args.out, args.timeout)
    print(result["status"])
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
