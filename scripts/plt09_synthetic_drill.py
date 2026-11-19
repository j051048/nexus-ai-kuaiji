#!/usr/bin/env python
"""Generate an explicitly synthetic PLT09 measurement drill.

This file exists only to prove the measurement tool can distinguish unknown,
measured zero and populated cells.  Every number below is fabricated and must
never be used as a customer baseline, productivity claim or headcount input.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from plt09_measurement import COMPANIES, ROLES, Cell, summarize, write_csv  # noqa: E402

OUT = ROOT / "docs/pilot/templates/measurement_synthetic_drill.csv"
SUMMARY = ROOT / "docs/pilot/templates/measurement_synthetic_drill.json"


def main() -> int:
    company_code, company_name = COMPANIES[0]
    rows: list[Cell] = []
    for index, (role_code, role_label) in enumerate(ROLES, start=1):
        volume = index % 5 + 1
        rows.append(
            Cell(
                company_code=company_code,
                company_name=company_name,
                role_code=role_code,
                role_label=role_label,
                period="2026-10",
                business_volume=volume,
                handled_minutes=volume * 8,
                review_minutes=volume * 3,
                waiting_internal_minutes=12,
                waiting_external_minutes=0,
                rework_count=1 if index % 4 == 0 else 0,
                line_excel_count=0,
                expedite_count=1 if index % 6 == 0 else 0,
                first_pass_numerator=volume - (1 if index % 4 == 0 else 0),
                first_pass_denominator=volume,
                error_review_count=1 if index % 6 == 0 else 0,
                error_post_count=0,
                transferred_minutes=0,
                external_support_minutes=0,
                source="合成演练-禁止用于基线或定编",
            )
        )
    write_csv(rows, OUT)
    summary = summarize(rows)
    summary["notice"] = "合成演练，数字全部虚构，禁止作为客户基线、人效或定编依据"
    SUMMARY.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
