#!/usr/bin/env python
"""PLT09-B blank measurement ledger for the five synthetic pilot companies.

This script never invents labour time.  A blank cell means "not collected";
the integer 0 means a known zero.  The two states are kept distinct in every
export and summary call.
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import asdict, dataclass
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
        "status": "未采集"
        if complete == 0 and partial == 0
        else ("已采集" if unknown == 0 and partial == 0 else "部分采集"),
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

# --- 反作弊判定（SCORECARD.md「反作弊判定」1-7 条） -------------------------
#
# 这一段只做判定，不做表扬：任何一条成立就返回「不能判定为成功」，并且
# 永远不计算减员比例、节省工时或节省金额。基线口径不同的两个时期不能
# 直接相减，因此判定要求调用方显式提供同一口径的基线与当期汇总。

VERDICT_CANNOT_JUDGE = "不能判定为成功"
VERDICT_MANUAL_REVIEW = "可提交人工复核"
DEFAULT_COVERAGE_FLOOR = 0.8


@dataclass(frozen=True)
class Aggregate:
    """One period of already-aggregated measurements (same scope as baseline)."""

    period: str
    review_backlog: int | None = None
    error_post_count: int | None = None
    transferred_minutes: int | None = None
    business_volume: int | None = None
    duty_scope: str | None = None
    external_support_minutes: int | None = None
    external_support_recorded: bool = True
    denominator_changed: bool = False
    denominator_note: str = ""


def _per_object(minutes: int | None, volume: int | None) -> float | None:
    """Time per object, used so that a bigger period is not called slower."""
    if minutes is None or volume is None or volume <= 0:
        return None
    return minutes / volume


def coverage(rows: list[Cell]) -> dict[str, float | int]:
    """Collected cells over expected cells; unknown is never treated as zero."""
    expected = len(rows) * len(NUMERIC_FIELDS)
    known = sum(1 for row in rows for field in NUMERIC_FIELDS if getattr(row, field) is not None)
    ratio = (known / expected) if expected else 0.0
    return {
        "expected_cells": expected,
        "known_cells": known,
        "coverage": round(ratio, 4),
    }


def unknown_as_zero(rows: list[Cell]) -> list[str]:
    """Rows that claim to be uncollected but carry a measured zero."""
    offenders: list[str] = []
    for row in rows:
        if row.source != "未采集":
            continue
        if any(getattr(row, field) == 0 for field in NUMERIC_FIELDS):
            offenders.append(f"{row.company_code}/{row.role_code}/{row.period}")
    return offenders


def evaluate(
    current: Aggregate,
    baseline: Aggregate,
    *,
    rows: list[Cell] | None = None,
    coverage_floor: float = DEFAULT_COVERAGE_FLOOR,
) -> dict[str, object]:
    """Apply the seven anti-cheat rules and return a verdict with reasons."""
    reasons: list[dict[str, str]] = []

    def flag(code: str, message: str) -> None:
        reasons.append({"code": code, "message": message})

    if (
        current.review_backlog is not None
        and baseline.review_backlog is not None
        and current.review_backlog > baseline.review_backlog
    ):
        flag(
            "review_backlog_up",
            f"复核积压较基线上升：{baseline.review_backlog} → {current.review_backlog}",
        )

    if (
        current.error_post_count is not None
        and baseline.error_post_count is not None
        and current.error_post_count > baseline.error_post_count
    ):
        flag(
            "post_error_up",
            f"事后发现差错较基线上升：{baseline.error_post_count} → {current.error_post_count}",
        )

    current_per_object = _per_object(current.transferred_minutes, current.business_volume)
    baseline_per_object = _per_object(baseline.transferred_minutes, baseline.business_volume)
    if (
        current_per_object is not None
        and baseline_per_object is not None
        and current_per_object > baseline_per_object
    ):
        flag(
            "transfer_time_up",
            "转移给业务人员的时间上升："
            f"每对象 {round(baseline_per_object, 2)} 分钟 → {round(current_per_object, 2)} 分钟",
        )
    elif (
        current_per_object is None
        and current.transferred_minutes is not None
        and current.transferred_minutes > 0
    ):
        flag("transfer_time_unknown", "转移给业务人员的时间缺少业务量口径，无法比较")

    if current.duty_scope != baseline.duty_scope:
        flag(
            "scope_mismatch",
            f"职责范围口径不同：{baseline.duty_scope!r} → {current.duty_scope!r}",
        )

    if not current.external_support_recorded or current.external_support_minutes is None:
        flag("external_support_unrecorded", "存在未记录的外部援助，或外部投入未采集")

    unknown_cells = 0
    if rows is not None:
        stats = coverage(rows)
        if float(stats["coverage"]) < coverage_floor:
            flag(
                "coverage_insufficient",
                f"采集覆盖不足：已采集 {stats['known_cells']} / 应有 {stats['expected_cells']} 格",
            )
        offenders = unknown_as_zero(rows)
        if offenders:
            unknown_cells = len(offenders)
            flag(
                "unknown_as_zero",
                f"把未采集当零：{unknown_cells} 行标记「未采集」却填了 0（例：{offenders[0]}）",
            )
    else:
        flag("coverage_missing", "没有提供采集明细，无法核对覆盖与未采集当零")

    if current.denominator_changed and not current.denominator_note.strip():
        flag("denominator_change_undisclosed", "变更了分母但报告未披露变更口径")

    verdict = VERDICT_CANNOT_JUDGE if reasons else VERDICT_MANUAL_REVIEW
    return {
        "verdict": verdict,
        "reasons": reasons,
        "current_period": current.period,
        "baseline_period": baseline.period,
        "reduction_reportable": False,
        "note": "记分表不计算减员比例、节省工时或节省金额；结论必须由人工复核。",
    }
