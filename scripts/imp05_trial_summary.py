#!/usr/bin/env python3
"""Summarise the local IMP05 trial record without inventing human results.

The input is a UTF-8 CSV with one row per participant attempt. Missing records,
automation rows, duplicate attempts and missing candidate/device fields are
reported as incomplete; an empty file never becomes a green result.
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path

OUTCOMES = {"completed", "assisted", "abandoned", "not_started"}
REQUIRED = (
    "candidate",
    "participant_id",
    "role",
    "company",
    "device",
    "network",
    "task_id",
    "attempt",
    "outcome",
    "seconds",
    "assists",
    "wrong_turns",
    "state_understood",
    "duplicate_write",
)


def _truth(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "y"}


def summarise(rows: list[dict[str, str]]) -> dict:
    problems: list[str] = []
    if not rows:
        return {
            "schema": "imp05-trial-v1",
            "status": "incomplete",
            "reason": "no_records",
            "denominator": 0,
            "counts": {},
            "rates": {},
            "problems": ["no records: do not report 0 errors"],
        }

    for index, row in enumerate(rows, start=2):
        missing = [name for name in REQUIRED if not row.get(name, "").strip()]
        if missing:
            problems.append(f"row {index}: missing {','.join(missing)}")
        if row.get("outcome") not in OUTCOMES:
            problems.append(f"row {index}: invalid outcome {row.get('outcome')!r}")
        if row.get("outcome") == "abandoned" and not row.get("failure_step", "").strip():
            problems.append(f"row {index}: abandoned attempt needs failure_step")
        if row.get("device", "").strip().lower() in {"automation", "playwright", "ci"}:
            problems.append(f"row {index}: automation is not a human trial")
        try:
            if int(row.get("seconds", "")) < 0:
                problems.append(f"row {index}: negative seconds")
        except ValueError:
            problems.append(f"row {index}: seconds must be an integer")

    keys = [(r.get("participant_id"), r.get("task_id"), r.get("attempt")) for r in rows]
    for key, count in Counter(keys).items():
        if count > 1:
            problems.append(f"duplicate attempt {key}")

    started = [r for r in rows if r.get("outcome") in {"completed", "assisted", "abandoned"}]
    completed = [r for r in started if r.get("outcome") == "completed"]
    assisted = [r for r in started if r.get("outcome") == "assisted"]
    abandoned = [r for r in started if r.get("outcome") == "abandoned"]
    independent = [r for r in completed if int(r.get("assists", "0") or 0) == 0]
    duplicates = sum(1 for r in started if _truth(r.get("duplicate_write", "")))
    understood = sum(1 for r in started if _truth(r.get("state_understood", "")))

    counts = {
        "records": len(rows),
        "participants": len({r.get("participant_id") for r in rows}),
        "tasks": len({r.get("task_id") for r in rows}),
        "completed": len(completed),
        "assisted": len(assisted),
        "abandoned": len(abandoned),
        "not_started": len(rows) - len(started),
        "completed_independently": len(independent),
        "duplicate_writes": duplicates,
        "state_understood": understood,
    }
    # Fixed denominator: every started attempt, including abandoned attempts.
    denominator = len(started)
    status = "incomplete" if problems or denominator == 0 else "recorded"
    return {
        "schema": "imp05-trial-v1",
        "status": status,
        "reason": "invalid_records"
        if problems
        else ("no_started_attempts" if denominator == 0 else None),
        "denominator": denominator,
        "counts": counts,
        "rates": {
            "independent_completion": round(len(independent) / denominator, 4)
            if denominator
            else None,
            "assisted": round(len(assisted) / denominator, 4) if denominator else None,
            "abandoned": round(len(abandoned) / denominator, 4) if denominator else None,
            "duplicate_write": round(duplicates / denominator, 4) if denominator else None,
        },
        "problems": problems,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv", type=Path)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    with args.csv.open(newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    result = summarise(rows)
    text = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.out:
        args.out.write_text(text, encoding="utf-8")
    print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
