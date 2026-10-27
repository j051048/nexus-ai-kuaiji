"""Run the frozen inner-strength matrix and write machine-readable evidence.

Usage:
  .venv\\Scripts\\python.exe scripts/check_inner_strength.py
  .venv\\Scripts\\python.exe scripts/check_inner_strength.py --groups p0,ak05
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
GROUPS = {
    "p0": [
        "nexus_backend/tests/test_stage7_proposal_queue.py",
        "nexus_backend/tests/test_queue_workflow.py",
    ],
    "ak05": [
        "nexus_backend/tests/test_ak05_unified_concurrency.py",
        "nexus_backend/tests/test_ak05_persistent.py",
        "nexus_backend/tests/test_ak05_two_process.py",
        "nexus_backend/tests/test_ak05_send_kill_v4.py",
        "nexus_backend/tests/test_unified_worker.py",
    ],
    "ak04": [
        "nexus_backend/tests/test_ak04_assistant_execution.py",
        "nexus_backend/tests/test_stage15_conversations.py",
    ],
    "attempt": [
        "nexus_backend/tests/test_ak01_attempt_matrix.py",
        "nexus_backend/tests/test_batch2_concurrency.py",
    ],
    "invariants": [
        "nexus_backend/tests/test_posting_service.py",
        "nexus_backend/tests/test_intelligence_ledger.py",
        "nexus_backend/tests/test_stage10_closing.py",
        "nexus_backend/tests/test_stage9_delivery.py",
    ],
    "offline": [
        "nexus_backend/tests/test_ag02_context_pack.py",
        "nexus_backend/tests/test_ag03_answers.py",
        "nexus_backend/tests/test_ag04_investigation.py",
        "nexus_backend/tests/test_ag05_examples.py",
        "nexus_backend/tests/test_intelligence_workflow.py",
    ],
}
FILES = [
    "nexus_backend/app/unified_worker.py",
    "nexus_backend/app/jobs/worker.py",
    "nexus_backend/app/assistant/monitor.py",
    "nexus_backend/app/execution/records.py",
    "nexus_backend/app/assistant/service.py",
]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--groups", default="p0,ak05,ak04,invariants,attempt,offline")
    parser.add_argument("--out", default="")
    args = parser.parse_args()
    selected = [item.strip() for item in args.groups.split(",") if item.strip()]
    unknown = set(selected) - set(GROUPS)
    if unknown:
        raise SystemExit("未知分组：" + ",".join(sorted(unknown)))
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    files = {
        path: sha256((ROOT / path).read_bytes()).hexdigest()
        for path in FILES
        if (ROOT / path).is_file()
    }
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = Path(args.out) if args.out else ROOT / "tmp_test" / "inner-gate" / stamp
    out.mkdir(parents=True, exist_ok=True)
    results = {}
    for group in selected:
        command = [
            sys.executable,
            "scripts/test_accounting_offline.py",
            *GROUPS[group],
            "-q",
        ]
        completed = subprocess.run(
            command,
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=900,
        )
        log = out / f"{group}.log"
        log.write_text(completed.stdout + completed.stderr, encoding="utf-8")
        results[group] = {
            "returncode": completed.returncode,
            "log": str(log.relative_to(ROOT)),
            "tail": (completed.stdout + completed.stderr).strip().splitlines()[-3:],
        }
    report = {
        "schema": "inner-strength-v1",
        "head": head,
        "groups": selected,
        "files": files,
        "results": results,
    }
    (out / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False))
    return 0 if all(item["returncode"] == 0 for item in results.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
