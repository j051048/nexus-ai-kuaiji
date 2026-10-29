"""Offline N02/N03 evidence preflight; never a deployment or approval command.

PG reports are local evidence, not signed attestations. This rechecks the raw
JUnit and complete source identity without connecting to a server or trusting
summary counts. Release mode checks a human-prepared plan, not remote state.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.n02_n03_postgres_gate import (  # noqa: E402
    DURABILITY,
    GateError,
    contract_digest,
    validate_junit,
)
from scripts.release_candidate import (  # noqa: E402
    CandidateError,
    candidate_fingerprint,
    worktree_clean,
)


class EvidenceError(ValueError):
    pass


def _require(condition, message):
    if not condition:
        raise EvidenceError(message)


def _unique(pairs):
    value = {}
    for key, item in pairs:
        _require(key not in value, "Duplicate JSON keys in release evidence")
        value[key] = item
    return value


def _read(path, *, limit=2_000_000):
    path = Path(path)
    _require(not path.is_symlink() and path.is_file(), "Evidence file is missing or symbolic")
    _require(path.stat().st_size <= limit, "Evidence exceeds the offline size limit")
    return path.read_bytes()


def _json(path):
    try:
        value = json.loads(_read(path).decode("utf-8"), object_pairs_hook=_unique)
    except (UnicodeError, json.JSONDecodeError):
        raise EvidenceError("Evidence is not valid UTF-8 JSON") from None
    _require(type(value) is dict, "Evidence must be a JSON object")
    return value


def _report(directory, major, current, required_digest, migration_count):
    directory = Path(directory)
    _require(
        not directory.is_symlink() and directory.is_dir(),
        "PG evidence directory is missing or symbolic",
    )
    directory = directory.resolve()
    path = directory / "summary.json"
    original_summary = _read(path)
    report = _json(path)
    _require(report.get("schema") == "n02-n03-pg-gate-v1", "Unsupported PG evidence schema")
    _require(report.get("status") == "passed", "PG gate did not pass")
    _require(
        type(report.get("major")) is int and report["major"] == major,
        "PG major does not match its evidence slot",
    )
    _require(
        type(report.get("server_version_num")) is int
        and report["server_version_num"] // 10000 == major,
        "Actual server major does not match",
    )
    _require(report.get("roles_checked") is True, "Restricted roles were not verified")
    _require(
        report.get("durability") == dict.fromkeys(DURABILITY, "on"),
        "Durability was not fully verified",
    )
    _require(
        report.get("candidate") == current,
        "PG evidence is for a different HEAD, source or migration candidate",
    )
    _require(
        report.get("required_cases_sha256") == required_digest, "Required case contract drifted"
    )
    migration = report.get("migrations")
    _require(type(migration) is dict, "Migration evidence is missing")
    _require(
        migration.get("status") == "verified"
        and migration.get("digest") == current["migration_sha256"],
        "Migration checksums were not verified for this candidate",
    )
    _require(
        type(migration.get("count")) is int and migration["count"] == migration_count,
        "Migration inventory is incomplete",
    )
    suites = report.get("suites")
    _require(
        type(suites) is dict and set(suites) == {"n02", "n03"},
        "Both N02 and N03 suites are required",
    )
    results = {}
    for suite in ("n02", "n03"):
        declared = suites[suite]
        _require(
            type(declared) is dict and declared.get("status") == "passed",
            "Required suite did not pass",
        )
        xml = directory / f"{suite}.xml"
        data = _read(xml)
        _require(
            b"<!DOCTYPE" not in data.upper() and b"<!ENTITY" not in data.upper(),
            "DTD/entity declarations are not allowed in JUnit",
        )
        digest = hashlib.sha256(data).hexdigest()
        _require(declared.get("junit_sha256") == digest, "JUnit hash does not match its report")
        actual = validate_junit(xml, suite)
        _require(
            type(declared.get("passed")) is int and declared["passed"] == actual["passed"],
            "Claimed test count differs from raw JUnit",
        )
        _require(
            actual["junit_sha256"] == digest and _read(xml) == data,
            "JUnit changed during verification",
        )
        results[suite] = actual
    _require(_read(path) == original_summary, "PG summary changed during verification")
    return {
        "major": major,
        "status": "passed",
        "suites": results,
        "summary_sha256": hashlib.sha256(original_summary).hexdigest(),
    }


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _release_plan(path, current):
    _require(path is not None, "Release mode requires an explicit local review plan")
    plan = _json(path)
    _require(plan.get("schema") == "controlled-release-plan-v1", "Unsupported release plan schema")
    _require(plan.get("candidate") == current, "Release plan is for a different candidate")
    _require(
        _text(plan.get("reviewed_by")) and _text(plan.get("review_reference")),
        "A named human review and reference are required",
    )
    checks = plan.get("platform_checks")
    _require(
        type(checks) is dict and set(checks) == {"vercel", "zeabur"},
        "Both deployment platforms require recorded checks",
    )
    for record in checks.values():
        _require(
            type(record) is dict
            and record.get("auto_deploy") == "disabled"
            and _text(record.get("evidence_reference")),
            "Automatic deployment status is unknown or enabled",
        )
    migration = plan.get("migration")
    _require(
        type(migration) is dict and migration.get("target_digest") == current["migration_sha256"],
        "Migration plan does not match the candidate",
    )
    _require(
        isinstance(migration.get("prior_digest"), str)
        and re.fullmatch(r"[0-9a-f]{64}", migration["prior_digest"]),
        "A prior migration inventory is required",
    )
    _require(
        all(
            _text(migration.get(field))
            for field in ("backup_reference", "compatibility_review_reference")
        ),
        "Backup and forward/backward compatibility reviews are required",
    )
    runtimes = plan.get("runtime")
    _require(
        type(runtimes) is dict
        and set(runtimes) == {"frontend_gateway", "api", "worker", "assistant"},
        "Frontend gateway, API and both workers must have explicit rollout artifacts",
    )
    for record in runtimes.values():
        _require(
            type(record) is dict
            and record.get("head") == current["head"]
            and _text(record.get("artifact_reference")),
            "Runtime artifact does not bind the candidate",
        )
    for field in ("rollout_steps", "rollback_steps"):
        steps = plan.get(field)
        _require(
            type(steps) is list and bool(steps) and all(_text(step) for step in steps),
            "Ordered rollout and rollback steps are required",
        )
    _require(
        plan.get("unknown_results_preserved") is True,
        "Unknown external results must be retained without automatic resend",
    )


def check_candidate(pg16, pg17, *, mode="technical", release_plan=None, root=None):
    root = Path(root or ROOT).resolve()
    result = {
        "schema": "controlled-release-check-v1",
        "status": "blocked",
        "mode": mode,
        "scope": "n02_n03_only",
        "readiness": "blocked",
        "deployment_authorized": False,
        "remote_state_verified": False,
        "approval_verified": False,
        "errors": [],
        "evidence_trust": "local_reports_not_signed_attestations",
    }
    try:
        _require(mode in {"technical", "release"}, "Unknown check mode")
        current = candidate_fingerprint(root)
        result["candidate"] = current
        result["worktree_clean"] = worktree_clean(root)
        contract = root / "scripts/n02_n03_postgres_required_cases.json"
        required_digest = hashlib.sha256(_read(contract)).hexdigest()
        _require(
            required_digest == contract_digest(),
            "Checker and candidate use different required case contracts",
        )
        migration_count = len(
            list((root / "nexus_backend/migrations/postgres").glob("[0-9][0-9][0-9]_*.sql"))
        )
        _require(migration_count > 0, "PostgreSQL migration inventory is empty")
        result["postgres"] = {
            "16": _report(pg16, 16, current, required_digest, migration_count),
            "17": _report(pg17, 17, current, required_digest, migration_count),
        }
        result["required_cases_sha256"] = required_digest
        if mode == "release":
            _require(
                result["worktree_clean"], "Release review requires a clean, committed candidate"
            )
            _release_plan(release_plan, current)
        _require(
            candidate_fingerprint(root) == current, "Candidate changed during release preflight"
        )
        _require(
            hashlib.sha256(_read(contract)).hexdigest() == required_digest,
            "Required cases changed during release preflight",
        )
        _require(
            worktree_clean(root) == result["worktree_clean"],
            "Worktree changed during release preflight",
        )
        result.update(
            status="passed",
            readiness="n02_n03_technical_passed"
            if mode == "technical"
            else "local_checklist_complete_for_human_review",
        )
    except (CandidateError, EvidenceError, GateError) as error:
        result["errors"].append(str(error))
    except (OSError, ValueError, TypeError, KeyError):
        # Do not render arbitrary JSON, paths, provider messages or credentials.
        result["errors"].append("Local candidate evidence could not be safely verified")
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pg16", required=True, type=Path)
    parser.add_argument("--pg17", required=True, type=Path)
    parser.add_argument("--mode", choices=("technical", "release"), default="technical")
    parser.add_argument("--release-plan", type=Path)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    result = check_candidate(args.pg16, args.pg17, mode=args.mode, release_plan=args.release_plan)
    output = json.dumps(result, ensure_ascii=False, indent=2)
    if args.out:
        # Only an explicitly requested new local artifact is written. Refuse
        # overwriting sources or evidence that this command has just checked.
        args.out.parent.mkdir(parents=True, exist_ok=True)
        try:
            with args.out.open("x", encoding="utf-8") as stream:
                stream.write(output + "\n")
        except OSError:
            print("Cannot create a new local preflight artifact", file=sys.stderr)
            return 1
    print(output)
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
