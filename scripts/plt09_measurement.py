#!/usr/bin/env python
"""PLT09-B blank measurement ledger for the five synthetic pilot companies.

This script never invents labour time.  A blank cell means "not collected";
the integer 0 means a known zero.  The two states are kept distinct in every
export and summary call.
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass, asdict
from pathlib import Path

COMPANIES = (
    ("taxi", "出租车公司（合成）"),
    ("hospital", "内部医院（合成）"),
    ("property", "物业与食材采购（合成）"),
    ("advertising", "广告传媒（合成）"),
    ("trading", "贸易（合成）"),
)

ROLES = (
    ("R01", "原始凭证接收与完整性"),
    ("R02", "业务事项录入与补件"),
    ("R03", "票据识别与原件核对"),
    ("R04", "科目映射与制单"),
    ("R05", "独立复核与退回"),
    ("R06", "银行流水与余额对账"),
    ("R07", "往来与子账核销"),
    ("R08", "规费与收入计费"),
    ("R09", "成本费用归集"),
    ("R10", "税费计算与底稿"),
    ("R11", "申报缴款与回执登记"),
    ("R12", "月结与期末处理"),
    ("R13", "报表编制与发布"),
    ("R14", "月度包交付与客户签收"),
    ("R15", "归档、审计与追溯"),
)

FIELDS = (
    "company_code",
    "company_name",
    "role_code",
    "role_label",
    "period",
    "business_volume",
    "handled_minutes",
    "review_minutes",
    "waiting_internal_minutes",
    "waiting_external_minutes",
    "rework_count",
    "line_excel_count",
    "expedite_count",
    "first_pass_numerator",
    "first_pass_denominator",
    "error_review_count",
    "error_post_count",
    "transferred_minutes",
    "external_support_minutes",
    "source",
)

NUMERIC_FIELDS = (
    "business_volume",
    "handled_minutes",
    "review_minutes",
    "waiting_internal_minutes",
    "waiting_external_minutes",
    "rework_count",
    "line_excel_count",
    "expedite_count",
    "first_pass_numerator",
    "first_pass_denominator",
    "error_review_count",
    "error_post_count",
    "transferred_minutes",
    "external_support_minutes",
)


@dataclass(frozen=True)
class Cell:
    company_code: str
    company_name: str
    role_code: str
    role_label: str
    period: str
    business_volume: int | None = None
    handled_minutes: int | None = None
    review_minutes: int | None = None
    waiting_internal_minutes: int | None = None
    waiting_external_minutes: int | None = None
    rework_count: int | None = None
    line_excel_count: int | None = None
    expedite_count: int | None = None
    first_pass_numerator: int | None = None
    first_pass_denominator: int | None = None
    error_review_count: int | None = None
    error_post_count: int | None = None
    transferred_minutes: int | None = None
    external_support_minutes: int | None = None
    source: str = "未采集"


def blank_rows(periods: tuple[str, ...]) -> list[Cell]:
    """Return five companies x fifteen responsibilities x requested periods."""
    rows: list[Cell] = []
    for company_code, company_name in COMPANIES:
        for period in periods:
            for role_code, role_label in ROLES:
                rows.append(
                    Cell(
                        company_code=company_code,
                        company_name=company_name,
                        role_code=role_code,
                        role_label=role_label,
                        period=period,
                    )
                )
    return rows


def summarize(rows: list[Cell]) -> dict[str, int | str]:
    """Keep unknowns and measured zeroes distinct.

    A cell is `未采集` only when every numeric field is None.  A cell with at
    least one known value is `部分采集`; a fully populated cell is `已采集`.
    """
    unknown = 0
    partial = 0
    complete = 0
    measured_zeroes = 0
    for row in rows:
        values = [getattr(row, field) for field in NUMERIC_FIELDS]
        known = [value for value in values if value is not None]
        if not known:
            unknown += 1
        elif len(known) == len(values):
            complete += 1
        else:
            partial += 1
        measured_zeroes += sum(1 for value in known if value == 0)
    return {
        "rows": len(rows),
        "unknown_rows": unknown,
        "partial_rows": partial,
        "complete_rows": complete,
        "measured_zeroes": measured_zeroes,
        "status": "未采集" if complete == 0 and partial == 0 else (
            "已采集" if unknown == 0 and partial == 0 else "部分采集"
        ),
    }


def write_csv(rows: list[Cell], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow(asdict(row))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="docs/pilot/templates/measurement_baseline.csv")
    parser.add_argument("--period", action="append", default=None)
    args = parser.parse_args()
    periods = tuple(args.period or ("2026-10", "2026-11"))
    rows = blank_rows(periods)
    write_csv(rows, Path(args.out))
    summary = summarize(rows)
    print(f"wrote {args.out}: {summary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
