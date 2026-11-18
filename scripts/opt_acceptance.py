#!/usr/bin/env python
"""PLT01-D: reproducible, read-only OPT acceptance ledger.

The script never writes to the application database and never edits a source
file.  It writes only its own report directory under tmp_test/pilot/PLT01-D/.
Commands are the curated OPT acceptance probes maintained in this table; PG
tests are skipped by their own fixtures when no isolated DSN is present and are
reported as skipped, never as passed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable
BASELINE = "767f7cdf7c717c98d3516a0e31b53bd8cd9ae1a9"


def _run(argv: list[str], *, cwd: Path = ROOT, timeout: int = 600) -> tuple[int, str, float]:
    started = time.monotonic()
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.setdefault("TMP", str(ROOT / "tmp_test/build-tmp"))
    env.setdefault("TEMP", str(ROOT / "tmp_test/build-tmp"))
    try:
        proc = subprocess.run(
            argv,
            cwd=str(cwd),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        output = (proc.stdout or "") + (proc.stderr or "")
        return proc.returncode, output, time.monotonic() - started
    except subprocess.TimeoutExpired as exc:
        output = (exc.stdout or "") + (exc.stderr or "")
        return 124, output + "\nTIMEOUT", time.monotonic() - started
    except FileNotFoundError as exc:
        return 127, str(exc), time.monotonic() - started


def _digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _source_files() -> list[Path]:
    tracked = subprocess.run(
        ["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True
    ).stdout.split(b"\0")
    untracked = subprocess.run(
        ["git", "ls-files", "-z", "--others", "--exclude-standard"],
        cwd=ROOT,
        capture_output=True,
        check=True,
    ).stdout.split(b"\0")
    paths = []
    for raw in tracked + untracked:
        if not raw:
            continue
        path = ROOT / raw.decode()
        if not path.is_file():
            continue
        rel_parts = path.relative_to(ROOT).parts
        if rel_parts[0] not in {"nexus_backend", "src", "scripts", "e2e"} and len(rel_parts) > 1:
            continue
        if any(part in {"node_modules", ".git", "tmp_test", "dist", "data", "docs", "artifacts"} for part in path.parts):
            continue
        if path.suffix.lower() not in {".py", ".ts", ".tsx", ".css", ".sql", ".json", ".md", ".toml", ".yml", ".yaml", ".sh", ".mjs"}:
            continue
        paths.append(path)
    return sorted(set(paths))


def _migrations() -> tuple[int, str, list[dict]]:
    rows = []
    for dialect in ("postgres", "sqlite"):
        for path in sorted((ROOT / "nexus_backend/migrations" / dialect).glob("*.sql")):
            rows.append(
                {
                    "dialect": dialect,
                    "version": int(path.name.split("_", 1)[0]),
                    "name": path.name,
                    "sha256": _digest(path),
                }
            )
    rows.sort(key=lambda item: (item["dialect"], item["version"]))
    aggregate = hashlib.sha256(
        json.dumps(rows, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return len(rows), aggregate, rows


def _skip_counts() -> tuple[int, int, list[str], list[dict]]:
    pattern = r"pytest\.mark\.(skip|skipif|xfail)"
    current_by_file: dict[str, int] = {}
    for path in (ROOT / "nexus_backend/tests").rglob("test_*.py"):
        text = path.read_text(encoding="utf-8", errors="replace")
        count = len(re.findall(pattern, text))
        if count:
            current_by_file[path.relative_to(ROOT).as_posix()] = count
    baseline_by_file: dict[str, int] = {}
    proc = subprocess.run(
        ["git", "grep", "-c", "-E", pattern, BASELINE, "--", "nexus_backend/tests"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode == 0:
        for line in proc.stdout.splitlines():
            try:
                _rev, path, count = line.rsplit(":", 2)
                baseline_by_file[path] = int(count)
            except (IndexError, ValueError):
                pass
    increases = []
    for path, count in sorted(current_by_file.items()):
        previous = baseline_by_file.get(path, 0)
        if count > previous:
            increases.append({"path": path, "baseline": previous, "current": count})
    files = [f"{path}:{count}" for path, count in sorted(current_by_file.items())]
    return sum(current_by_file.values()), sum(baseline_by_file.values()), files, increases


def _commands() -> list[dict]:
    pytest = [PY, "-m", "pytest"]
    return [
        {
            "module": "OPT01",
            "source": "OPT01-C 金额取整 + OPT01-A 64 位金额",
            "display": "pytest test_tax_rounding_consistency + test_tax14_basis + test_tax_estimate + test_migration_money_types",
            "argv": pytest
            + [
                "tests/test_tax_rounding_consistency.py",
                "tests/test_tax14_basis.py",
                "tests/test_tax_estimate.py",
                "tests/test_migration_money_types.py",
                "-q",
            ],
            "cwd": ROOT / "nexus_backend",
        },
        {
            "module": "OPT02",
            "source": "OPT02 批量复核/零用例/写入范围",
            "display": "pytest test_batch_review",
            "argv": pytest + ["tests/test_batch_review.py", "-q"],
            "cwd": ROOT / "nexus_backend",
        },
        {
            "module": "OPT03",
            "source": "OPT03 审计不可改删/哈希链/下载留痕",
            "display": "pytest test_opt03_audit_chain + immutability + download_audit",
            "argv": pytest
            + [
                "tests/test_opt03_audit_chain.py",
                "tests/test_opt03_audit_immutability.py",
                "tests/test_opt03_download_audit.py",
                "-q",
            ],
            "cwd": ROOT / "nexus_backend",
        },
        {
            "module": "OPT04",
            "source": "OPT04 企业作用域/发布前资料",
            "display": "pytest test_opt04_profile_scope",
            "argv": pytest + ["tests/test_opt04_profile_scope.py", "-q"],
            "cwd": ROOT / "nexus_backend",
        },
        {
            "module": "OPT05",
            "source": "OPT05 批量租约/队列开账/调度器",
            "display": "pytest test_opt05_batch_lease + queue_open + scheduler",
            "argv": pytest
            + [
                "tests/test_opt05_batch_lease.py",
                "tests/test_opt05_queue_open.py",
                "tests/test_opt05_scheduler.py",
                "-q",
            ],
            "cwd": ROOT / "nexus_backend",
        },
        {
            "module": "OPT06",
            "source": "OPT06 复核控制/授权替补/断号",
            "display": "pytest test_opt06_review_control + delegation + voucher_gaps",
            "argv": pytest
            + [
                "tests/test_opt06_review_control.py",
                "tests/test_opt06_review_delegation.py",
                "tests/test_opt06_voucher_gaps.py",
                "-q",
            ],
            "cwd": ROOT / "nexus_backend",
        },
        {
            "module": "OPT07",
            "source": "OPT07 科目角色/税额政策/别名",
            "display": "pytest test_opt07_account_roles + tax_policy_items + subject_aliases + alias_http",
            "argv": pytest
            + [
                "tests/test_opt07_account_roles.py",
                "tests/test_opt07_tax_policy_items.py",
                "tests/test_opt07_subject_aliases.py",
                "tests/test_opt07_alias_http.py",
                "-q",
            ],
            "cwd": ROOT / "nexus_backend",
        },
        {
            "module": "OPT08",
            "source": "OPT08 金额/科目选择器/表格",
            "display": "vitest money + account-picker",
            "argv": [
                str(ROOT / "node_modules/.bin/vitest.cmd")
                if os.name == "nt"
                else str(ROOT / "node_modules/.bin/vitest"),
                "run",
                "src/money.test.ts",
                "src/account-picker.test.ts",
            ],
            "cwd": ROOT,
        },
        {
            "module": "OPT09",
            "source": "OPT09 风险分档 + 年度矩阵",
            "display": "pytest test_opt09_review_risk + test_opt09_annual_matrix",
            "argv": pytest
            + ["tests/test_opt09_review_risk.py", "tests/test_opt09_annual_matrix.py", "-q"],
            "cwd": ROOT / "nexus_backend",
        },
        {
            "module": "OPT10",
            "source": "OPT10 加密备份/脱敏/限流",
            "display": "pytest test_opt10_backup_encryption + sensitive_redaction + rate_limit + rate_limit_app",
            "argv": pytest
            + [
                "tests/test_opt10_backup_encryption.py",
                "tests/test_opt10_sensitive_redaction.py",
                "tests/test_opt10_rate_limit.py",
                "tests/test_opt10_rate_limit_app.py",
                "-q",
            ],
            "cwd": ROOT / "nexus_backend",
        },
    ]


def _pg_commands() -> list[dict]:
    pytest = [PY, "-m", "pytest"]
    backend = ROOT / "nexus_backend"
    rows = [
        ("OPT02", "test_batch_review_postgres.py"),
        ("OPT03", "test_opt03_audit_chain_postgres.py"),
        ("OPT04", "test_opt04_posting_postgres.py"),
        ("OPT05", "test_opt05_batch_lease_postgres.py"),
        ("OPT06", "test_opt06_voucher_gaps_postgres.py"),
        ("OPT07", "test_opt07_subject_aliases_postgres.py"),
        ("OPT10", "test_opt10_backup_encryption_postgres.py"),
    ]
    return [
        {
            "module": module,
            "source": "isolated PostgreSQL acceptance",
            "display": f"PG: {name}",
            "argv": pytest + [f"tests/{name}", "-q"],
            "cwd": backend,
        }
        for module, name in rows
    ]


def _classify(exit_code: int, output: str) -> str:
    if exit_code == 124:
        return "timeout"
    if re.search(r"\bno tests ran\b|\b0 tests? collected\b", output, re.I):
        return "zero-tests"
    if exit_code != 0:
        return "failed"
    if re.search(r"\bskipped\b", output, re.I):
        return "passed-with-skips"
    return "passed"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=None)
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--sqlite", default=None, help="read-only SQLite DB for audit-chain verification")
    args = parser.parse_args()
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8]
    out = Path(args.out) if args.out else ROOT / "tmp_test/pilot/PLT01-D" / run_id
    out.mkdir(parents=True, exist_ok=False)

    sources = _source_files()
    source_rows = [
        {"path": str(path.relative_to(ROOT)), "sha256": _digest(path)} for path in sources
    ]
    (out / "sources.json").write_text(
        json.dumps(source_rows, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True
    ).stdout.strip()
    branch = subprocess.run(
        ["git", "branch", "--show-current"], cwd=ROOT, capture_output=True, text=True
    ).stdout.strip()
    migration_count, migration_digest, migration_rows = _migrations()
    current_skips, baseline_skips, skip_files, skip_increases = _skip_counts()

    tracked_modified = subprocess.run(
        ["git", "diff", "--name-only", "HEAD", "--", "nexus_backend/migrations"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout.splitlines()
    status_lines = subprocess.run(
        ["git", "status", "--porcelain=v1", "--", "nexus_backend/migrations"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout.splitlines()
    untracked_migrations = [line[3:].strip() for line in status_lines if line.startswith("??")]

    results = []
    for entry in _commands():
        code, output, seconds = _run(entry["argv"], cwd=entry["cwd"], timeout=args.timeout)
        match = re.search(r"(\d+)\s+skipped", output)
        results.append(
            {
                "module": entry["module"],
                "source": entry["source"],
                "command": entry["display"],
                "exit_code": code,
                "seconds": round(seconds, 3),
                "status": _classify(code, output),
                "skipped_tests": int(match.group(1)) if match else 0,
                "tail": output[-4000:],
            }
        )

    pg_dsn = os.getenv("ACCOUNTING_TEST_POSTGRES_URL")
    for entry in _pg_commands():
        if not pg_dsn:
            results.append(
                {
                    "module": entry["module"],
                    "source": entry["source"],
                    "command": entry["display"],
                    "exit_code": None,
                    "seconds": 0,
                    "status": "skipped",
                    "skipped_tests": 0,
                    "skip_reason": "no ACCOUNTING_TEST_POSTGRES_URL",
                    "tail": "",
                }
            )
            continue
        code, output, seconds = _run(entry["argv"], cwd=entry["cwd"], timeout=args.timeout)
        match = re.search(r"(\d+)\s+skipped", output)
        results.append(
            {
                "module": entry["module"],
                "source": entry["source"],
                "command": entry["display"],
                "exit_code": code,
                "seconds": round(seconds, 3),
                "status": _classify(code, output),
                "skipped_tests": int(match.group(1)) if match else 0,
                "tail": output[-4000:],
            }
        )

    audit = {"status": "skipped", "reason": "no local SQLite accounting database"}
    local_db = Path(args.sqlite) if args.sqlite else ROOT / "data/accounting.db"
    if args.sqlite or local_db.exists():
        if not local_db.exists():
            audit = {"status": "failed", "reason": f"SQLite database not found: {local_db}"}
        else:
            code, output, seconds = _run(
                [PY, "scripts/verify_audit_chain.py", "--sqlite", str(local_db)], timeout=120
            )
            audit = {
                "status": _classify(code, output),
                "exit_code": code,
                "seconds": round(seconds, 3),
                "tail": output[-2000:],
            }

    boundary = {
        "modified_tracked_migrations": tracked_modified,
        "untracked_migrations": untracked_migrations,
        "skip_marks_current": current_skips,
        "skip_marks_baseline": baseline_skips,
        "skip_marks_delta": current_skips - baseline_skips,
        "skip_increases": skip_increases,
        "skip_files": skip_files,
        "audit_chain": audit,
    }
    failures = [r for r in results if r["status"] in {"failed", "timeout", "zero-tests"}]
    status = (
        "passed"
        if not failures and not tracked_modified and audit["status"] != "failed"
        else "failed"
    )
    module_counts = []
    by_module: dict[str, dict[str, int]] = {}
    for row in results:
        bucket = by_module.setdefault(
            row["module"],
            {"commands": 0, "passed": 0, "failed": 0, "skipped": 0, "skipped_tests": 0},
        )
        bucket["commands"] += 1
        bucket["skipped_tests"] += row["skipped_tests"]
        if row["status"] in {"passed", "passed-with-skips"}:
            bucket["passed"] += 1
        elif row["status"] in {"failed", "timeout", "zero-tests"}:
            bucket["failed"] += 1
        elif row["status"] == "skipped":
            bucket["skipped"] += 1
    for module, counts in by_module.items():
        module_counts.append({"module": module, **counts})
    summary = {
        "run_id": run_id,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "head": head,
        "branch": branch,
        "source_files": len(source_rows),
        "source_digest": hashlib.sha256(
            json.dumps(source_rows, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
        "migration_count": migration_count,
        "migration_digest": migration_digest,
        "migrations": migration_rows,
        "commands": results,
        "module_counts": module_counts,
        "boundary": boundary,
        "status": status,
    }
    (out / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    lines = [
        f"# PLT01-D acceptance {run_id}",
        "",
        f"- status: **{status}**",
        f"- HEAD: `{head}`",
        f"- source: {len(source_rows)} files, `{summary['source_digest']}`",
        f"- migration: {migration_count} files, `{migration_digest}`",
        f"- skip marks: current {current_skips}, baseline {baseline_skips}, delta {current_skips - baseline_skips}",
        f"- modified tracked migrations: {tracked_modified or 'none'}",
        f"- untracked migrations: {len(untracked_migrations)}",
        f"- audit chain: {audit['status']}",
        "",
        "| module | commands | passed | failed | skipped commands | skipped tests |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for row in module_counts:
        lines.append(
            f"| {row['module']} | {row['commands']} | {row['passed']} | {row['failed']} | {row['skipped']} | {row['skipped_tests']} |"
        )
    lines.extend(
        [
            "",
            "| command | status | exit | seconds | skipped |",
            "| --- | --- | --- | --- | --- |",
        ]
    )
    for row in results:
        exit_text = "n/a" if row["exit_code"] is None else row["exit_code"]
        lines.append(
            f"| {row['command']} | {row['status']} | {exit_text} | {row['seconds']} | {row['skipped_tests']} |"
        )
    if skip_increases:
        lines.extend(["", "## Skip increases", ""])
        for item in skip_increases:
            lines.append(
                f"- `{item['path']}`: {item['baseline']} -> {item['current']} (+{item['current'] - item['baseline']})"
            )
    (out / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    print(f"artifacts: {out}")
    return 0 if status == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
