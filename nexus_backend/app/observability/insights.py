"""Read-only firm management insights for the leadership dashboard.

This projection never creates accounting facts, approves a report, or sends a
message. Financial totals come only from the latest reviewed/published report
snapshot of each book; a book without one is reported as unknown, never as
zero.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from ..accounting.access import AccessControl
from ..accounting.domain import AccountingError
from ..reports.service import ReportService

STANDARD_LABELS = {
    "enterprise": "企业会计准则",
    "small_enterprise": "小企业会计准则",
    "unassessed": "口径未核定",
}
MODE_LABELS = {
    "formal": "正式",
    "legacy": "门槛建立前发布",
    "trial": "试算，不可对外",
    "missing": "缺报/未复核",
}


STAGE_ORDER = ("intake", "reconcile", "prepare", "review", "delivery", "archive")
STAGE_LABELS = {
    "intake": "收资料",
    "reconcile": "核差异",
    "prepare": "备凭证",
    "review": "会计复核",
    "delivery": "交付核查",
    "archive": "确认归档",
}
BLOCKER_LABELS = {
    "materials_pending": "必需资料未验收",
    "drafts_pending": "凭证待复核",
    "tax_basis_pending": "税费核定待复核",
    "open_cases": "未结异常/补件",
}
FINANCIAL_KEYS = (
    "assets_cents",
    "liabilities_cents",
    "equity_cents",
    "revenue_cents",
    "expenses_cents",
    "profit_cents",
    "cash_closing_cents",
    "unclassified_cash_cents",
)


def _period(value: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 7
        or value[4] != "-"
        or not value[:4].isdigit()
        or not value[5:].isdigit()
        or not 1 <= int(value[5:]) <= 12
    ):
        raise AccountingError("期间格式应为 YYYY-MM", 422)
    return value


def _months(end: str, count: int = 6) -> list[str]:
    year, month = map(int, end.split("-"))
    values = []
    for offset in range(count - 1, -1, -1):
        cursor = year * 12 + month - 1 - offset
        values.append(f"{cursor // 12:04d}-{cursor % 12 + 1:02d}")
    return values


def _json(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


class FirmInsightsService:
    """Aggregate existing firm state for the first management dashboard."""

    def __init__(self, db, access=None):
        self.db = db
        self.access = access or AccessControl()

    def overview(self, actor, period: str):
        period = _period(period)
        months = _months(period)
        first = months[0]
        with self.db.transaction(actor=actor) as conn:
            self.access.require_manager(conn, actor)
            # 分母基准：先取事务所全部应报/纳管法人，再关联当期账套与已复核报表。
            # 未开账法人必须留在分母里并点名，不能因为“本期没有账套”而从看板消失。
            managed = conn.execute(
                "SELECT c.id,c.name FROM companies c JOIN firm_clients fc ON fc.company_id=c.id "
                "WHERE fc.firm_id=? AND fc.status='active' ORDER BY c.name",
                (actor.firm_id,),
            ).fetchall()
            books = conn.execute(
                "SELECT b.id,b.company_id,c.name AS company_name,b.period,"
                "COALESCE(p.status,'open') AS period_status FROM books b "
                "JOIN companies c ON c.id=b.company_id "
                "LEFT JOIN periods p ON p.book_id=b.id AND p.period=? "
                "WHERE b.firm_id=? AND b.period=? ORDER BY c.name,b.id",
                (period, actor.firm_id, period),
            ).fetchall()
            book_ids = [row["id"] for row in books]
            if not book_ids:
                return self._empty(actor.firm_id, period, managed)

            def grouped(sql: str, params: tuple = ()) -> dict[str, int]:
                return {
                    row["book_id"]: _int(row["count"])
                    for row in conn.execute(sql, params).fetchall()
                }

            material = grouped(
                "SELECT r.book_id,COUNT(*) AS count FROM material_requests r "
                "WHERE r.firm_id=? AND r.period=? AND r.required=1 AND NOT EXISTS("
                "SELECT 1 FROM material_request_actions a WHERE a.request_id=r.id "
                "AND a.state='accepted') GROUP BY r.book_id",
                (actor.firm_id, period),
            )
            drafts = grouped(
                "SELECT d.book_id,COUNT(*) AS count FROM drafts d JOIN books b ON b.id=d.book_id "
                "WHERE b.firm_id=? AND b.period=? AND d.status='draft' GROUP BY d.book_id",
                (actor.firm_id, period),
            )
            basis = grouped(
                "SELECT book_id,COUNT(*) AS count FROM tax_basis_reviews "
                "WHERE book_id IN (SELECT id FROM books WHERE firm_id=? AND period=?) "
                "AND period=? AND status='draft' GROUP BY book_id",
                (actor.firm_id, period, period),
            )
            cases = grouped(
                "SELECT book_id,COUNT(*) AS count FROM case_tasks "
                "WHERE firm_id=? AND state IN ('open','awaiting_info','response_submitted','in_review') "
                "GROUP BY book_id",
                (actor.firm_id,),
            )
            plans = {
                row["book_id"]: dict(row)
                for row in conn.execute(
                    "SELECT sp.book_id,sp.lead_user_id,u.name AS lead_name,sp.due_on "
                    "FROM service_plan_versions sp JOIN (SELECT book_id,MAX(version) AS version "
                    "FROM service_plan_versions WHERE firm_id=? GROUP BY book_id) latest "
                    "ON latest.book_id=sp.book_id AND latest.version=sp.version "
                    "LEFT JOIN users u ON u.id=sp.lead_user_id WHERE sp.firm_id=?",
                    (actor.firm_id, actor.firm_id),
                ).fetchall()
            }
            reports = self._latest_reports(conn, actor.firm_id, period)
            draft_trial_books = {
                row["book_id"]: row["mode"]
                for row in conn.execute(
                    "SELECT rr.book_id,rr.mode FROM report_runs rr JOIN books b ON b.id=rr.book_id "
                    "WHERE b.firm_id=? AND rr.period=? AND rr.status NOT IN ('reviewed','published') "
                    "ORDER BY rr.book_id,rr.version DESC",
                    (actor.firm_id, period),
                ).fetchall()
            }
            trend_reports = self._reports_between(conn, actor.firm_id, first, period)
            trend_periods = {
                row["period"]: dict(row)
                for row in conn.execute(
                    "SELECT b.period,COUNT(*) AS books,"
                    "COUNT(DISTINCT b.company_id) AS opened_companies,"
                    "SUM(CASE WHEN p.status='closed' THEN 1 ELSE 0 END) AS closed "
                    "FROM books b LEFT JOIN periods p ON p.book_id=b.id AND p.period=b.period "
                    "WHERE b.firm_id=? AND b.period BETWEEN ? AND ? GROUP BY b.period",
                    (actor.firm_id, first, period),
                ).fetchall()
            }
            trend_costs = {
                row["period"]: _int(row["micros"])
                for row in conn.execute(
                    "SELECT period,SUM(COALESCE(confirmed_micro,estimated_micro,0)) AS micros "
                    "FROM model_costs WHERE firm_id=? AND period BETWEEN ? AND ? GROUP BY period",
                    (actor.firm_id, first, period),
                ).fetchall()
            }
            cost = conn.execute(
                "SELECT COUNT(*) AS sample_count,"
                "SUM(CASE WHEN state='released' THEN 0 ELSE COALESCE(confirmed_micro,estimated_micro,0) END) AS micros,"
                "SUM(CASE WHEN state<>'released' AND confirmed_micro IS NULL AND estimated_micro IS NULL THEN 1 ELSE 0 END) AS unknown_count "
                "FROM model_costs WHERE firm_id=? AND period=?",
                (actor.firm_id, period),
            ).fetchone()
            unknown = conn.execute(
                "SELECT COUNT(*) AS count FROM delivery_notifications "
                "WHERE firm_id=? AND state='unknown'",
                (actor.firm_id,),
            ).fetchone()["count"]

        today = datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
        items = []
        for book in books:
            blockers = []
            if material.get(book["id"]):
                blockers.append(
                    {
                        "code": "materials_pending",
                        "label": BLOCKER_LABELS["materials_pending"],
                        "count": material[book["id"]],
                    }
                )
            if drafts.get(book["id"]):
                blockers.append(
                    {
                        "code": "drafts_pending",
                        "label": BLOCKER_LABELS["drafts_pending"],
                        "count": drafts[book["id"]],
                    }
                )
            if basis.get(book["id"]):
                blockers.append(
                    {
                        "code": "tax_basis_pending",
                        "label": BLOCKER_LABELS["tax_basis_pending"],
                        "count": basis[book["id"]],
                    }
                )
            if cases.get(book["id"]):
                blockers.append(
                    {
                        "code": "open_cases",
                        "label": BLOCKER_LABELS["open_cases"],
                        "count": cases[book["id"]],
                    }
                )
            report = reports.get(book["id"])
            plan = plans.get(book["id"])
            if (
                report
                and report["status"] in {"reviewed", "published"}
                and report["status"] == "published"
            ):
                stage = "archive" if book["period_status"] == "closed" else "delivery"
            elif book["period_status"] == "closed":
                stage = "archive"
            elif drafts.get(book["id"]) or basis.get(book["id"]):
                stage = "review"
            elif material.get(book["id"]):
                stage = "intake"
            else:
                stage = "prepare"
            report_mode = (
                report["effective_mode"]
                if report
                else ("trial" if book["id"] in draft_trial_books else "missing")
            )
            standard_kind = (
                (report.get("standard_kind") or "unassessed") if report else "unassessed"
            )
            items.append(
                {
                    "book_id": book["id"],
                    "company_id": book["company_id"],
                    "company_name": book["company_name"],
                    "period": book["period"],
                    "period_status": book["period_status"],
                    "stage": stage,
                    "stage_label": STAGE_LABELS[stage],
                    "owner_name": (plan or {}).get("lead_name") or "待安排",
                    "lead_user_id": (plan or {}).get("lead_user_id"),
                    "due_on": (plan or {}).get("due_on"),
                    "overdue": bool(
                        (plan or {}).get("due_on")
                        and (plan or {}).get("due_on") < today
                        and book["period_status"] != "closed"
                    ),
                    "blockers": blockers,
                    "top_blocker": blockers[0]["label"] if blockers else None,
                    "financial_known": bool(report),
                    "report_status": report["status"] if report else "missing",
                    "report_mode": report_mode,
                    "report_mode_label": MODE_LABELS.get(report_mode, report_mode),
                    "standard_kind": standard_kind,
                    "standard_label": STANDARD_LABELS.get(standard_kind, standard_kind),
                    "report_as_of": report.get("created_at") if report else None,
                    "report_version": report["version"] if report else None,
                }
            )
        stage_counts = {key: 0 for key in STAGE_ORDER}
        for item in items:
            stage_counts[item["stage"]] += 1
        exception_counts: dict[str, int] = {}
        for item in items:
            for blocker in item["blockers"]:
                exception_counts[blocker["code"]] = (
                    exception_counts.get(blocker["code"], 0) + blocker["count"]
                )
        if unknown:
            exception_counts["notification_unknown"] = int(unknown)
        financial = {key: 0 for key in FINANCIAL_KEYS}
        known = 0
        legacy_books = 0
        included_companies = []
        missing_companies = []
        by_standard: dict[str, dict[str, Any]] = {}
        item_by_book = {item["book_id"]: item for item in items}
        # 缺报按“法人”点名（一家只出现一次）；同一法人已报部分账套时，其余全部未复核账套
        # 逐条记入 pending_book_reports，不吞掉其余账套，也不把未全齐的期间误标成 complete。
        missing_by_company: dict[str, list[dict]] = {}
        pending_book_reports: list[dict] = []
        for item in items:
            report = reports.get(item["book_id"])
            if not report or report["status"] not in {"reviewed", "published"}:
                entry = {
                    "company_id": item["company_id"],
                    "company_name": item["company_name"],
                    "book_id": item["book_id"],
                    "owner_name": item["owner_name"],
                    "top_blocker": item["top_blocker"] or "报表尚未复核",
                    "has_trial_draft": item["book_id"] in draft_trial_books,
                }
                missing_by_company.setdefault(item["company_id"], []).append(entry)
                continue
            known += 1
            eff_mode = report["effective_mode"]
            if eff_mode == "legacy":
                legacy_books += 1
            std = report.get("standard_kind") or "unassessed"
            included_companies.append(
                {
                    "company_id": item["company_id"],
                    "company_name": item["company_name"],
                    "book_id": item["book_id"],
                    "period": item["period"],
                    "report_status": report["status"],
                    "report_mode": eff_mode,
                    "report_mode_label": MODE_LABELS.get(eff_mode, eff_mode),
                    "standard_kind": std,
                    "standard_label": STANDARD_LABELS.get(std, std),
                    "as_of": report.get("created_at"),
                }
            )
            content = report["content"]
            balance = _json(content.get("balance_sheet"))
            profit = _json(content.get("profit_statement"))
            cash = _json(content.get("cash_flow"))
            checks = _json(content.get("checks"))
            values = {
                "assets_cents": _int(balance.get("assets")),
                "liabilities_cents": _int(balance.get("liabilities")),
                "equity_cents": _int(balance.get("equity")),
                "revenue_cents": _int(profit.get("revenue_cents")),
                "expenses_cents": _int(profit.get("expense_cents")),
                "profit_cents": _int(profit.get("profit_cents")),
                "cash_closing_cents": _int(cash.get("closing_cash_cents")),
                "unclassified_cash_cents": _int(
                    checks.get("unclassified_cash_cents", cash.get("unclassified_cents"))
                ),
            }
            bucket = by_standard.setdefault(
                std,
                {
                    "standard_kind": std,
                    "standard_label": STANDARD_LABELS.get(std, std),
                    "books": 0,
                    **{k: 0 for k in FINANCIAL_KEYS},
                },
            )
            bucket["books"] += 1
            for k, v in values.items():
                financial[k] += v
                bucket[k] += v
        distinct_standards = sorted(by_standard.keys())
        mixed_standards = len(distinct_standards) > 1
        # 法人分母：事务所全部纳管法人 ∪ 当期有账套的法人（历史账套也不能丢分母）。
        company_names = {row["id"]: row["name"] for row in managed}
        for item in items:
            company_names.setdefault(item["company_id"], item["company_name"])
        opened_ids = {item["company_id"] for item in items}
        reported_ids = {
            item["company_id"]
            for item in items
            if (reports.get(item["book_id"]) or {}).get("status") in {"reviewed", "published"}
        }
        missing_companies = [
            entries[0] for cid, entries in missing_by_company.items() if cid not in reported_ids
        ]
        pending_book_reports = [
            {**entry, "reason": "book_report_missing"}
            for cid, entries in missing_by_company.items()
            if cid in reported_ids
            for entry in entries
        ]
        # 未开账法人必须留在分母里并点名，不能因为“本期没有账套”而从看板消失。
        for company_id, company_name in company_names.items():
            if company_id in opened_ids:
                continue
            missing_companies.append(
                {
                    "company_id": company_id,
                    "company_name": company_name,
                    "book_id": None,
                    "owner_name": "待安排",
                    "top_blocker": "本期未开账",
                    "has_trial_draft": False,
                    "reason": "not_opened",
                }
            )
        for row in missing_companies:
            row.setdefault("reason", "report_missing")
        complete = bool(items) and not missing_companies and not pending_book_reports
        missing_names = [m["company_name"] for m in missing_companies]
        if not items:
            coverage_label = "本期无在办账套"
        elif complete:
            coverage_label = f"已纳入 {len(reported_ids)}/{len(company_names)} 家"
        elif missing_companies:
            missing_preview = "、".join(missing_names[:3]) + (
                f"等 {len(missing_names)} 家" if len(missing_names) > 3 else ""
            )
            coverage_label = f"已纳入 {len(reported_ids)}/{len(company_names)} 家（不完整：缺 {missing_preview}）"
        else:
            coverage_label = (
                f"已纳入 {len(reported_ids)}/{len(company_names)} 家"
                f"（不完整：另有 {len(pending_book_reports)} 套账套未复核）"
            )
        if legacy_books:
            coverage_label += f" · 含 {legacy_books} 家门槛建立前历史报表"
        totals_comparable = not mixed_standards and known > 0
        if mixed_standards:
            totals_notice = (
                "已纳入单位存在多种会计准则口径，不建议直接混算合计，请查看下方分口径汇总。"
            )
            # 顶层合计不再混算异准则金额：置空表示“不可比”，数字只保留在分准则小计里。
            financial = {key: None for key in FINANCIAL_KEYS}
        elif not complete:
            totals_notice = "存在未复核或缺报单位，当前合计不完整（未按 0 元补齐）。"
        else:
            totals_notice = "管理汇总，非合并财务报表（无内部往来抵销）。"
        _ = item_by_book
        trends = []
        managed_total = len(company_names)
        for month in months:
            reviewed = trend_reports.get(month, [])
            counts = trend_periods.get(month, {})
            month_books = _int(counts.get("books"))
            month_opened = _int(counts.get("opened_companies"))
            month_reported_companies = len({row["company_id"] for row in reviewed})
            month_standards = {row["standard_kind"] for row in reviewed}
            month_mixed = len(month_standards) > 1
            if not reviewed:
                trend_status = "missing"
                trend_status_label = "缺报"
                revenue_cents = None
                profit_cents = None
                trend_comparable = False
            elif month_mixed:
                trend_status = "mixed_standards"
                trend_status_label = "异准则不可混加"
                revenue_cents = None
                profit_cents = None
                trend_comparable = False
            else:
                revenue_cents = sum(
                    _int(_json(row["content"].get("profit_statement")).get("revenue_cents"))
                    for row in reviewed
                )
                profit_cents = sum(
                    _int(_json(row["content"].get("profit_statement")).get("profit_cents"))
                    for row in reviewed
                )
                trend_comparable = True
                if (
                    managed_total > 0
                    and month_reported_companies >= managed_total
                    and len(reviewed) >= month_books
                ):
                    trend_status = "complete"
                    trend_status_label = "已报齐"
                else:
                    trend_status = "partial"
                    trend_status_label = f"部分缺报（已报 {month_reported_companies}/{max(managed_total, month_opened)} 家）"
            trends.append(
                {
                    "period": month,
                    "books": month_books,
                    "closed": _int(counts.get("closed")),
                    "reviewed_reports": len(reviewed),
                    "managed_companies": max(managed_total, month_opened),
                    "opened_companies": month_opened,
                    "reported_companies": month_reported_companies,
                    "mixed_standards": month_mixed,
                    "comparable": trend_comparable,
                    "status": trend_status,
                    "status_label": trend_status_label,
                    "revenue_cents": revenue_cents,
                    "profit_cents": profit_cents,
                    "ai_cost_micro": trend_costs.get(month, 0),
                }
            )
        risk = sorted(
            [item for item in items if item["blockers"] or item["overdue"]],
            key=lambda item: (
                -len(item["blockers"]),
                item["due_on"] or "9999-99-99",
                item["company_name"],
            ),
        )[:12]
        staff: dict[str, dict] = {}
        for item in items:
            key = item["lead_user_id"] or "unassigned"
            row = staff.setdefault(
                key,
                {
                    "user_id": item["lead_user_id"],
                    "name": item["owner_name"],
                    "books": 0,
                    "blocked": 0,
                    "overdue": 0,
                },
            )
            row["books"] += 1
            row["blocked"] += 1 if item["blockers"] else 0
            row["overdue"] += 1 if item["overdue"] else 0
        staff_rows = sorted(
            staff.values(), key=lambda row: (-row["blocked"], -row["books"], row["name"])
        )[:12]
        return {
            "period": period,
            "generated_on": today,
            "stage_projection": True,
            "note": "经营看板是只读投影；阶段按现有资料、草稿、复核和报表状态推断，不自动关账或复核。",
            "kpis": {
                "companies": len(company_names),
                "managed_companies": len(company_names),
                "opened_companies": len(opened_ids),
                "reported_companies": len(reported_ids),
                "books": len(items),
                "blocked": sum(1 for item in items if item["blockers"]),
                "overdue": sum(1 for item in items if item["overdue"]),
                "closed": sum(1 for item in items if item["period_status"] == "closed"),
                "review_pending": sum(int(drafts.get(item["book_id"], 0)) for item in items),
                "materials_pending": sum(int(material.get(item["book_id"], 0)) for item in items),
                "exceptions": sum(int(cases.get(item["book_id"], 0)) for item in items),
                "notification_unknown": int(unknown),
                "known_reports": known,
                "missing_reports": len(items) - known,
                "ai_cost_micro": _int(cost["micros"]),
                "ai_cost_unknown": _int(cost["unknown_count"]),
            },
            "financial": {
                **financial,
                "known_books": known,
                "missing_books": len(items) - known,
                "total_books": len(items),
                "managed_companies": len(company_names),
                "opened_companies": len(opened_ids),
                "reported_companies": len(reported_ids),
                "companies_without_books": len(company_names) - len(opened_ids),
                "complete": complete,
                "coverage_label": coverage_label,
                "mixed_standards": mixed_standards,
                "totals_comparable": totals_comparable,
                "totals_notice": totals_notice,
                "legacy_books": legacy_books,
                "trial_books": len(draft_trial_books),
                "management_summary_notice": "管理汇总，非合并财务报表（无内部往来抵销）。",
                "included_companies": included_companies,
                "missing_companies": missing_companies,
                "pending_book_reports": pending_book_reports,
                "standards_breakdown": [by_standard[k] for k in distinct_standards],
            },
            "stages": [
                {"code": key, "label": STAGE_LABELS[key], "count": stage_counts[key]}
                for key in STAGE_ORDER
            ],
            "exceptions": [
                {"code": code, "label": BLOCKER_LABELS.get(code, "通知结果未知"), "count": count}
                for code, count in sorted(
                    exception_counts.items(), key=lambda pair: (-pair[1], pair[0])
                )
            ],
            "trends": trends,
            "risk": risk,
            "staff": staff_rows,
        }

    @staticmethod
    def _latest_reports(conn, firm_id: str, period: str) -> dict[str, dict]:
        rows = conn.execute(
            "SELECT rr.book_id,rr.version,rr.status,rr.mode,rr.standard_kind,"
            "rr.policy_version,rr.policy_approver,rr.created_at,rr.content_json "
            "FROM report_runs rr JOIN books b ON b.id=rr.book_id "
            "WHERE b.firm_id=? AND rr.period=? AND rr.status IN ('reviewed','published') "
            "ORDER BY rr.book_id,rr.version DESC",
            (firm_id, period),
        ).fetchall()
        result = {}
        for row in rows:
            if row["book_id"] not in result:
                eff_mode, _ = ReportService.effective_mode(row)
                result[row["book_id"]] = {
                    "version": row["version"],
                    "status": row["status"],
                    "mode": row["mode"],
                    "effective_mode": eff_mode,
                    "standard_kind": row["standard_kind"] or "unassessed",
                    "created_at": row["created_at"],
                    "content": _json(row["content_json"]),
                }
        return result

    @staticmethod
    def _reports_between(conn, firm_id: str, first: str, last: str) -> dict[str, list[dict]]:
        rows = conn.execute(
            "SELECT rr.period,rr.book_id,b.company_id,rr.version,rr.status,"
            "rr.standard_kind,rr.content_json FROM report_runs rr "
            "JOIN books b ON b.id=rr.book_id WHERE b.firm_id=? AND rr.period BETWEEN ? AND ? "
            "AND rr.status IN ('reviewed','published') ORDER BY rr.book_id,rr.version DESC",
            (firm_id, first, last),
        ).fetchall()
        latest: dict[tuple[str, str], dict] = {}
        for row in rows:
            latest.setdefault(
                (row["book_id"], row["period"]),
                {
                    "book_id": row["book_id"],
                    "company_id": row["company_id"],
                    "version": row["version"],
                    "status": row["status"],
                    "standard_kind": row["standard_kind"] or "unassessed",
                    "content": _json(row["content_json"]),
                },
            )
        result: dict[str, list[dict]] = {}
        for (_book_id, period), value in latest.items():
            result.setdefault(period, []).append(value)
        return result

    @staticmethod
    def _empty(firm_id: str, period: str, managed=()) -> dict:
        today = datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
        names = {row["id"]: row["name"] for row in managed}
        # 没有在办账套时，纳管法人仍留在分母里并逐家点名「本期未开账」。
        missing = [
            {
                "company_id": company_id,
                "company_name": company_name,
                "book_id": None,
                "owner_name": "待安排",
                "top_blocker": "本期未开账",
                "has_trial_draft": False,
                "reason": "not_opened",
            }
            for company_id, company_name in names.items()
        ]
        coverage_label = (
            "本期无在办账套" if not names else f"已纳入 0/{len(names)} 家（不完整：全部未开账）"
        )
        return {
            "period": period,
            "generated_on": today,
            "stage_projection": True,
            "note": "当前月份没有可汇总的账套；看板不生成业务数据。",
            "kpis": {
                "companies": len(names),
                "managed_companies": len(names),
                "opened_companies": 0,
                "reported_companies": 0,
                "books": 0,
                "blocked": 0,
                "overdue": 0,
                "closed": 0,
                "review_pending": 0,
                "materials_pending": 0,
                "exceptions": 0,
                "notification_unknown": 0,
                "known_reports": 0,
                "missing_reports": 0,
                "ai_cost_micro": 0,
                "ai_cost_unknown": 0,
            },
            "financial": {
                **{key: 0 for key in FINANCIAL_KEYS},
                "known_books": 0,
                "missing_books": 0,
                "total_books": 0,
                "managed_companies": len(names),
                "opened_companies": 0,
                "reported_companies": 0,
                "companies_without_books": len(names),
                "complete": False,
                "coverage_label": coverage_label,
                "mixed_standards": False,
                "totals_comparable": False,
                "totals_notice": "本期无已复核报表。",
                "legacy_books": 0,
                "trial_books": 0,
                "management_summary_notice": "管理汇总，非合并财务报表（无内部往来抵销）。",
                "included_companies": [],
                "missing_companies": missing,
                "pending_book_reports": [],
                "standards_breakdown": [],
            },
            "stages": [
                {"code": key, "label": STAGE_LABELS[key], "count": 0} for key in STAGE_ORDER
            ],
            "exceptions": [],
            "trends": [],
            "risk": [],
            "staff": [],
        }
