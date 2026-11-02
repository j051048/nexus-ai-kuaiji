"""Explicit local-only RSI gate. Does not provision, deploy or publish anything."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys
from urllib.parse import urlsplit
import xml.etree.ElementTree as ET

from .snapshots import atomic_create, canonical, dependencies, digest

ROOT = Path(__file__).resolve().parents[3]
OFFLINE = [
    "test_rsi_snapshots.py",
    "test_rsi_experiment.py",
    "test_rsi_rules.py",
    "test_ocr_replay_engine.py",
    "test_ocr_field_evaluation.py",
    "test_ag05_examples.py",
    "test_assistant_task_baseline.py",
    "test_stage8_learning.py",
    "test_stage11_rule_governance.py",
    "test_stage11_rule_governance_api.py",
    "test_stage8_rule_queue_integration.py",
    "test_stage10_settlement_learning_scope.py",
    "test_stage10_settlements.py",
    "test_stage13_legacy_learning.py",
]
POSTGRES = [
    "test_rsi_rules_postgres.py",
    "test_stage8_learning_postgres.py",
    "test_stage11_rule_governance_postgres.py",
    "test_stage10_settlement_learning_scope_postgres.py",
    "test_stage10_settlements_postgres.py",
    "test_settlement_company_lock_postgres.py",
    "test_stage13_legacy_learning_postgres.py",
]


def fingerprint():
    from tools.ocr_replay.replay import sha256_path

    paths = {
        p
        for group in (
            "learning",
            "accounting",
            "business",
            "ledgers",
            "jobs",
            "intelligence",
            "settlements",
        )
        for p in (ROOT / "nexus_backend/app" / group).glob("*.py")
    }
    paths |= set((ROOT / "nexus_backend/migrations").rglob("*.sql"))
    paths |= set((ROOT / "tools/assistant_replay").glob("*.py"))
    for fixture in ("assistant_task", "ocr_replay", "rsi"):
        paths |= {
            p for p in (ROOT / "nexus_backend/tests/fixtures" / fixture).rglob("*") if p.is_file()
        }
    paths |= {ROOT / "nexus_backend/tests" / name for name in OFFLINE + POSTGRES}
    paths |= {
        ROOT / "nexus_backend/tests" / name
        for name in (
            "conftest.py",
            "review_policy_fixtures.py",
            "test_catalog.py",
            "test_business.py",
            "test_business_postgres.py",
            "test_intelligence_workflow.py",
            "test_intelligence_workflow_postgres.py",
            "period_fixtures.py",
        )
    }
    paths |= {
        ROOT / "scripts" / name
        for name in ("test_accounting_offline.py", "test_accounting_postgres_isolated.py")
    }
    return {
        **dependencies(ROOT),
        **{p.relative_to(ROOT).as_posix(): sha256_path(p) for p in sorted(paths)},
    }


def validate_local(config, major):
    import psycopg

    targets = [urlsplit(config[k]) for k in ("admin", "app", "worker")]
    if any(
        t.hostname != "127.0.0.1"
        or t.port in (None, 5432)
        or not t.path.startswith("/ak01_local_ak05_")
        or t.query
        or t.fragment
        for t in targets
    ):
        raise ValueError("explicit_isolated_local_database_required")
    if len({(t.hostname, t.port, t.path) for t in targets}) != 1:
        raise ValueError("database_targets_differ")
    with psycopg.connect(config["app"], connect_timeout=5) as conn:
        actual = int(conn.execute("SHOW server_version_num").fetchone()[0]) // 10000
        if actual != major:
            raise ValueError("postgres_major_mismatch")
        role = conn.execute(
            "SELECT rolsuper,rolbypassrls FROM pg_roles WHERE rolname=session_user"
        ).fetchone()
        if any(role):
            raise ValueError("privileged_runtime_login")
        for setting in ("fsync", "synchronous_commit", "full_page_writes"):
            if conn.execute("SHOW " + setting).fetchone()[0] != "on":
                raise ValueError("durability_disabled")
    return {"major": actual, "loopback": True, "runtime_unprivileged": True, "durability": True}


def junit(path, *, rsi_only=False):
    cases = list(ET.parse(path).getroot().iter("testcase"))
    if rsi_only:
        cases = [c for c in cases if "test_rsi_" in c.get("classname", "")]
    result = {
        "tests": len(cases),
        **{
            tag: sum(c.find(tag) is not None for c in cases)
            for tag in ("failure", "error", "skipped")
        },
    }
    result["passed"] = bool(cases) and not any(
        result[tag] for tag in ("failure", "error", "skipped")
    )
    return result


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--pg16-config", type=Path, required=True)
    parser.add_argument("--pg17-config", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=False)
    before = fingerprint()
    report = {
        "schema_version": "rsi-acceptance-v1",
        "dependencies": before,
        "candidate_sha256": digest(before),
        "head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "phases": {},
        "model_verified": False,
        "publication_authorized": False,
    }
    try:
        for major in (16, 17):
            config = json.loads(getattr(args, f"pg{major}_config").read_text(encoding="utf-8"))
            report[f"pg{major}"] = validate_local(config, major)
        for phase, names, runner in [
            ("offline", OFFLINE, ["scripts/test_accounting_offline.py"]),
            (
                "pg16",
                POSTGRES,
                ["scripts/test_accounting_postgres_isolated.py", "--config", str(args.pg16_config)],
            ),
            (
                "pg17",
                POSTGRES,
                ["scripts/test_accounting_postgres_isolated.py", "--config", str(args.pg17_config)],
            ),
        ]:
            print(f"RSI gate: {phase}", flush=True)
            xml = (args.out / (phase + ".xml")).resolve()
            command = [
                sys.executable,
                *runner,
                *("nexus_backend/tests/" + name for name in names),
                "-q",
                "--junitxml=" + str(xml),
            ]
            with (args.out / (phase + ".log")).open("w", encoding="utf-8") as log:
                process = subprocess.run(
                    command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, timeout=1800
                )
            result = junit(xml)
            result["rsi_contracts"] = junit(xml, rsi_only=True)
            result["exit_code"] = process.returncode
            result["passed"] &= process.returncode == 0
            report["phases"][phase] = result
            print(f"{phase}: {result}", flush=True)
        report["same_candidate"] = before == fingerprint()
        report["rsi_contracts_passed"] = report["same_candidate"] and all(
            p["rsi_contracts"]["passed"] for p in report["phases"].values()
        )
        report["passed"] = report["same_candidate"] and all(
            p["passed"] for p in report["phases"].values()
        )
    except Exception as error:
        report["passed"] = False
        report["failure_kind"] = type(error).__name__
    finally:
        atomic_create(args.out / "acceptance.json", canonical(report))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
