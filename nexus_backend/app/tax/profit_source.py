"""可溯源的本年累计利润参考值（企业所得税预缴预填）。

企业所得税预缴按**本年累计利润**计征（同一法人、同一会计年度 ``YYYY-01`` 至目标月），
而单张利润表快照中的 ``profit_statement.profit_cents`` 是当月发生额利润，底稿中的
``ledger_cents`` 是账载税额。绝不能拿单月利润或账载税额冒充本年累计利润：

* 仅当同一法人在当年 ``YYYY-01`` 至目标月 **每个月都有唯一账套且具备有效利润表快照**
  时，逐月精确相加得到本年累计利润，并返回完整逐月版本溯源；
* 某月同时存在多套有效快照时，已复核/已发布（``reviewed``/``published``）优先于
  试算/已校验（``draft``/``validated``），同档取最高版本；``superseded``/``expired`` 一律忽略；
* 若缺少当年 1 月至目标月中的任意一个月、或某月同法人存在多个账套导致口径不唯一，
  一律返回 ``suggested_profit_cents=None`` 并点名缺失月份或冲突原因，由人工填报并记录依据。
"""

from __future__ import annotations

import json
from typing import Any

SOURCE_REVIEWED = "reviewed_report"
SOURCE_TRIAL = "trial_report"

REVIEWED_STATUSES = ("reviewed", "published")
TRIAL_STATUSES = ("draft", "validated")

LABELS = {
    SOURCE_REVIEWED: "已复核利润表本年累计",
    SOURCE_TRIAL: "含试算利润表本年累计",
}

UNAVAILABLE_NOTICE = (
    "本年累计利润来源不完整：系统不猜测、不拿单月利润或底稿账载税额代替累计利润。"
    "请补齐各月利润表，或人工填写本年累计利润并说明依据。"
)


def _json(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        loaded = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _cents(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _ytd_months(period: str) -> list[str]:
    year, month = int(period[:4]), int(period[5:7])
    return [f"{year:04d}-{m:02d}" for m in range(1, month + 1)]


def unavailable(
    *,
    period_range: str | None = None,
    missing_months: list[str] | None = None,
    ambiguous_months: list[str] | None = None,
    months: list[dict] | None = None,
    notice: str | None = None,
) -> dict:
    return {
        "suggested_profit_cents": None,
        "source_kind": None,
        "source_label": None,
        "source_period": period_range,
        "period_range": period_range,
        "month_count": len(months or []),
        "months": list(months or []),
        "missing_months": list(missing_months or []),
        "ambiguous_months": list(ambiguous_months or []),
        "source_version": None,
        "is_final_report": False,
        "report_run_id": None,
        "notice": notice or UNAVAILABLE_NOTICE,
    }


def _pick_best_for_book(rows) -> dict | None:
    best: dict | None = None
    best_rank = -1
    for row in rows:
        statement = _json(_json(row["content_json"]).get("profit_statement"))
        cents = _cents(statement.get("profit_cents"))
        if cents is None:
            continue
        reviewed = row["status"] in REVIEWED_STATUSES
        rank = (2 if reviewed else 1) * 1_000_000 + int(row["version"])
        if rank <= best_rank:
            continue
        best_rank = rank
        best = {
            "period": row["period"],
            "book_id": row["book_id"],
            "report_run_id": row["id"],
            "version": int(row["version"]),
            "status": row["status"],
            "reviewed": reviewed,
            "profit_cents": cents,
        }
    return best


def suggest(conn, book, period: str) -> dict:
    """Compute traceable YTD cumulative profit for ``book``'s company up to ``period``.

    Same firm, same company, same fiscal year (``YYYY-01`` through ``period``).
    Missing any month or having multiple books for the company in any month
    returns ``suggested_profit_cents=None`` rather than silently substituting a
    single month or zero.
    """
    expected_months = _ytd_months(period)
    start_period = expected_months[0]
    period_range = f"{start_period}..{period}"
    firm_id = book["firm_id"]
    company_id = book["company_id"]

    month_books = conn.execute(
        "SELECT id,period FROM books "
        "WHERE firm_id=? AND company_id=? AND period BETWEEN ? AND ? "
        "ORDER BY period,id",
        (firm_id, company_id, start_period, period),
    ).fetchall()
    books_by_month: dict[str, list[str]] = {}
    for row in month_books:
        books_by_month.setdefault(row["period"], []).append(row["id"])

    ambiguous = [m for m in expected_months if len(books_by_month.get(m, [])) > 1]
    if ambiguous:
        listed = "、".join(ambiguous)
        return unavailable(
            period_range=period_range,
            ambiguous_months=ambiguous,
            notice=(
                f"同一法人在 {listed} 存在多个账套，无法自动合并 {period_range} 的本年累计利润；"
                "系统已留空，不拿单账套利润或账载税额代替，请人工核定并说明依据。"
            ),
        )

    all_book_ids = [
        bids[0] for m in expected_months for bids in [books_by_month.get(m, [])] if bids
    ]
    runs_by_key: dict[tuple[str, str], list] = {}
    if all_book_ids:
        statuses = (*REVIEWED_STATUSES, *TRIAL_STATUSES)
        book_marks = ",".join("?" for _ in all_book_ids)
        status_marks = ",".join("?" for _ in statuses)
        rows = conn.execute(
            "SELECT id,book_id,version,status,period,content_json FROM report_runs "
            f"WHERE book_id IN ({book_marks}) AND period BETWEEN ? AND ? "
            f"AND status IN ({status_marks}) ORDER BY period,version DESC",
            (*all_book_ids, start_period, period, *statuses),
        ).fetchall()
        for row in rows:
            runs_by_key.setdefault((row["book_id"], row["period"]), []).append(row)

    monthly_sources: list[dict] = []
    missing_months: list[str] = []
    for month in expected_months:
        bids = books_by_month.get(month, [])
        if not bids:
            missing_months.append(month)
            continue
        chosen = _pick_best_for_book(runs_by_key.get((bids[0], month), []))
        if chosen is None:
            missing_months.append(month)
            continue
        monthly_sources.append(chosen)

    if missing_months:
        listed = "、".join(missing_months[:4]) + (
            f"等 {len(missing_months)} 个月" if len(missing_months) > 4 else ""
        )
        return unavailable(
            period_range=period_range,
            missing_months=missing_months,
            months=monthly_sources,
            notice=(
                f"缺少 {listed} 的有效利润表快照，无法汇总 {start_period} 至 {period} 的本年累计利润；"
                "系统已留空，不猜测也不拿单月利润或底稿账载税额代替，请补齐后重试或人工填写并说明依据。"
            ),
        )

    cumulative_cents = sum(item["profit_cents"] for item in monthly_sources)
    all_reviewed = all(item["reviewed"] for item in monthly_sources)
    latest = monthly_sources[-1]
    source_kind = SOURCE_REVIEWED if all_reviewed else SOURCE_TRIAL
    source_period = period if len(monthly_sources) == 1 else period_range
    return {
        "suggested_profit_cents": cumulative_cents,
        "source_kind": source_kind,
        "source_label": LABELS[source_kind],
        "source_period": source_period,
        "period_range": period_range,
        "month_count": len(monthly_sources),
        "months": monthly_sources,
        "missing_months": [],
        "ambiguous_months": [],
        "source_version": latest["version"],
        "is_final_report": all_reviewed,
        "report_run_id": latest["report_run_id"],
        "notice": (
            None
            if all_reviewed
            else "累计来源包含试算利润表：部分月份正式报表尚未复核，累计金额可能随调整变化。"
        ),
    }
