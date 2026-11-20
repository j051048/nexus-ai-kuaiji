"""Offline routing-contract evaluation. Never calls a provider."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from nexus_backend.app.assistant.routing import AIRoutingService  # noqa: E402

DEFAULT_DATASET = ROOT / "docs" / "assistant_routing_eval_v1.json"


def evaluate(cases: list[dict]) -> dict:
    service = AIRoutingService.__new__(AIRoutingService)
    results = []
    for case in cases:
        level, rule, _ = service.classify(case["question"])
        results.append(
            {
                "id": case["id"],
                "split": case["split"],
                "expected": case["level"],
                "actual": level,
                "matched_rule": rule,
                "passed": level == case["level"],
            }
        )
    by_split = {}
    for split in ("dev", "holdout"):
        rows = [row for row in results if row["split"] == split]
        by_split[split] = {
            "total": len(rows),
            "passed": sum(1 for row in rows if row["passed"]),
            "failed": [row["id"] for row in rows if not row["passed"]],
        }
    critical = [
        row["id"] for row in results if row["expected"] == "blocked" and row["actual"] != "blocked"
    ]
    return {
        "schema": "assistant-routing-eval-v1",
        "denominator": len(results),
        "passed": sum(1 for row in results if row["passed"]),
        "by_split": by_split,
        "critical_write_misses": critical,
        "results": results,
        "boundary": "只评估 L1 路由契约，不评估模型回答质量，不调用真实模型。",
    }


def main() -> int:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_DATASET
    dataset = json.loads(path.read_text(encoding="utf-8"))
    report = evaluate(dataset["cases"])
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if report["critical_write_misses"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
