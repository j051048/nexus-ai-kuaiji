"""Phase 2/3 read-only drill-downs for the firm insights dashboard."""

from __future__ import annotations

import json
from datetime import date
from typing import Any

from ..accounting.access import AccessControl
from ..accounting.domain import AccountingError


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


def _previous_period(period: str) -> str:
    year, month = int(period[:4]), int(period[5:7])
    month -= 1
    if month == 0:
        year, month = year - 1, 12
    return f"{year:04d}-{month:02d}"


class FirmInsightsExtraService:
    def __init__(self, db, access=None):
        self.db = db
        self.access = access or AccessControl()

    @staticmethod
    def _latest(conn, firm_id: str, book_id: str, period: str):
        row = conn.execute(
            "SELECT rr.version,rr.status,rr.content_json FROM report_runs rr "
            "JOIN books b ON b.id=rr.book_id WHERE b.firm_id=? AND rr.book_id=? "
            "AND rr.period=? AND rr.status IN ('reviewed','published') "
            "ORDER BY rr.version DESC LIMIT 1",
            (firm_id, book_id, period),
        ).fetchone()
        if not row:
            return None
        return {
            "version": row["version"],
            "status": row["status"],
            "content": _json(row["content_json"]),
        }

    def company(self, actor, book_id: str):
        with self.db.transaction(actor=actor) as conn:
            self.access.require_manager(conn, actor)
            book = conn.execute(
                "SELECT b.id,b.company_id,b.period,c.name AS company_name FROM books b "
                "JOIN companies c ON c.id=b.company_id WHERE b.id=? AND b.firm_id=?",
                (book_id, actor.firm_id),
            ).fetchone()
            if not book:
                raise AccountingError("账套不存在或不在当前事务所", 404)
            report = self._latest(conn, actor.firm_id, book["id"], book["period"])
            aging_rows = conn.execute(
                "SELECT si.id,si.amount_cents,si.created_at,si.counterparty_original,"
                "COALESCE((SELECT SUM(a.amount_cents) FROM settlement_allocations a "
                "LEFT JOIN settlement_reversals r ON r.allocation_id=a.id "
                "WHERE a.item_id=si.id AND r.id IS NULL),0) AS applied "
                "FROM settlement_items si WHERE si.book_id=? AND si.clearing_account IN ('1122','1221')",
                (book_id,),
            ).fetchall()
            history_books = conn.execute(
                "SELECT id,period FROM books WHERE company_id=? AND firm_id=? "
                "ORDER BY period DESC LIMIT 6",
                (book["company_id"], actor.firm_id),
            ).fetchall()
            history = []
            for row in reversed(history_books):
                entry = self._latest(conn, actor.firm_id, row["id"], row["period"])
                profit = _json(entry["content"].get("profit_statement")) if entry else {}
                history.append(
                    {
                        "period": row["period"],
                        "known": bool(entry),
                        "revenue_cents": _int(profit.get("revenue_cents")),
                        "profit_cents": _int(profit.get("profit_cents")),
                    }
                )

        content = report["content"] if report else {}
        balance = _json(content.get("balance_sheet"))
        profit = _json(content.get("profit_statement"))
        cash = _json(content.get("cash_flow"))
        checks = _json(content.get("checks"))
        items = balance.get("items") if isinstance(balance.get("items"), list) else []
        profit_items = profit.get("items") if isinstance(profit.get("items"), list) else []
        buckets = {"0-30": 0, "31-60": 0, "61-90": 0, "90+": 0}
        aging_items = []
        for row in aging_rows:
            open_cents = max(0, _int(row["amount_cents"]) - _int(row["applied"]))
            if not open_cents:
                continue
            try:
                age = (date.today() - date.fromisoformat(str(row["created_at"])[:10])).days
            except ValueError:
                age = 0
            bucket = "90+" if age > 90 else "61-90" if age > 60 else "31-60" if age > 30 else "0-30"
            buckets[bucket] += open_cents
            aging_items.append(
                {
                    "counterparty": row["counterparty_original"],
                    "amount_cents": open_cents,
                    "age_days": age,
                    "bucket": bucket,
                }
            )
        return {
            "book_id": book["id"],
            "company_id": book["company_id"],
            "company_name": book["company_name"],
            "period": book["period"],
            "known": bool(report),
            "report_status": report["status"] if report else "missing",
            "report_version": report["version"] if report else None,
            "balance": {
                "assets_cents": _int(balance.get("assets")),
                "liabilities_cents": _int(balance.get("liabilities")),
                "equity_cents": _int(balance.get("equity")),
                "unclosed_profit_cents": _int(balance.get("unclosed_profit")),
                "items": items,
            },
            "profit": {
                "revenue_cents": _int(profit.get("revenue_cents")),
                "expenses_cents": _int(profit.get("expense_cents")),
                "profit_cents": _int(profit.get("profit_cents")),
                "items": profit_items,
            },
            "cash": {
                "closing_cash_cents": _int(cash.get("closing_cash_cents")),
                "opening_cash_cents": _int(cash.get("opening_cash_cents")),
                "net_change_cents": _int(cash.get("net_change_cents")),
                "unclassified_cents": _int(
                    checks.get("unclassified_cash_cents", cash.get("unclassified_cents"))
                ),
            },
            "working_capital": {
                "receivables_cents": sum(
                    _int(item.get("amount_cents"))
                    for item in items
                    if str(item.get("code", "")).startswith(("1122", "1221"))
                ),
                "payables_cents": sum(
                    _int(item.get("amount_cents"))
                    for item in items
                    if str(item.get("code", "")).startswith(("2202", "2241"))
                ),
            },
            "aging": {"known": bool(aging_rows), "buckets": buckets, "items": aging_items},
            "history": history,
        }

    def _latest_for_company_period(self, conn, firm_id: str, company_id: str, period: str):
        row = conn.execute(
            "SELECT r.id,r.status,r.content_json,r.version FROM report_runs r "
            "JOIN books b ON b.id=r.book_id WHERE b.firm_id=? AND b.company_id=? AND r.period=? "
            "AND r.status IN ('reviewed','published') ORDER BY r.version DESC,r.created_at DESC LIMIT 1",
            (firm_id, company_id, period),
        ).fetchone()
        if not row:
            return None
        content = _json(row["content_json"])
        return {
            "id": row["id"],
            "status": row["status"],
            "version": row["version"],
            "content": content,
        }

    def comparison(self, actor, period: str):
        period = _period(period)
        previous_period = _previous_period(period)
        with self.db.transaction(actor=actor) as conn:
            self.access.require_manager(conn, actor)
            books = conn.execute(
                "SELECT b.id,b.name AS book_name,b.company_id,c.name AS company_name FROM books b "
                "JOIN companies c ON c.id=b.company_id WHERE b.firm_id=? AND b.period=? "
                "ORDER BY c.name,b.id",
                (actor.firm_id, period),
            ).fetchall()
            rows = []
            for book in books:
                report = self._latest(conn, actor.firm_id, book["id"], period)
                content = report["content"] if report else {}
                balance = _json(content.get("balance_sheet"))
                profit = _json(content.get("profit_statement"))
                cash = _json(content.get("cash_flow"))
                items = balance.get("items") if isinstance(balance.get("items"), list) else []
                previous_report = self._latest_for_company_period(
                    conn, actor.firm_id, book["company_id"], previous_period
                )
                previous_content = previous_report["content"] if previous_report else {}
                previous_profit = _json(previous_content.get("profit_statement"))
                previous_known = bool(previous_report)
                rows.append(
                    {
                        "book_id": book["id"],
                        "book_name": book["book_name"],
                        "company_id": book["company_id"],
                        "company_name": book["company_name"],
                        "known": bool(report),
                        "report_status": report["status"] if report else "missing",
                        "previous_known": previous_known,
                        "previous_revenue_cents": _int(previous_profit.get("revenue_cents"))
                        if previous_known
                        else None,
                        "previous_profit_cents": _int(previous_profit.get("profit_cents"))
                        if previous_known
                        else None,
                        "revenue_delta_cents": _int(profit.get("revenue_cents"))
                        - _int(previous_profit.get("revenue_cents"))
                        if previous_known and report
                        else None,
                        "profit_delta_cents": _int(profit.get("profit_cents"))
                        - _int(previous_profit.get("profit_cents"))
                        if previous_known and report
                        else None,
                        "assets_cents": _int(balance.get("assets")),
                        "liabilities_cents": _int(balance.get("liabilities")),
                        "equity_cents": _int(balance.get("equity")),
                        "revenue_cents": _int(profit.get("revenue_cents")),
                        "expenses_cents": _int(profit.get("expense_cents")),
                        "profit_cents": _int(profit.get("profit_cents")),
                        "cash_closing_cents": _int(cash.get("closing_cash_cents")),
                        "receivables_cents": sum(
                            _int(item.get("amount_cents"))
                            for item in items
                            if str(item.get("code", "")).startswith(("1122", "1221"))
                        ),
                        "payables_cents": sum(
                            _int(item.get("amount_cents"))
                            for item in items
                            if str(item.get("code", "")).startswith(("2202", "2241"))
                        ),
                    }
                )
        return {"period": period, "compare_period": previous_period, "rows": rows}

    def timeline(self, actor, period: str):
        period = _period(period)
        with self.db.transaction(actor=actor) as conn:
            self.access.require_manager(conn, actor)
            rows = conn.execute(
                "SELECT b.id AS book_id,b.period,c.name AS company_name,"
                "COALESCE((SELECT lead_user_id FROM service_plan_versions sp WHERE sp.book_id=b.id "
                "ORDER BY sp.version DESC LIMIT 1),NULL) AS lead_user_id,"
                "COALESCE((SELECT due_on FROM service_plan_versions sp WHERE sp.book_id=b.id "
                "ORDER BY sp.version DESC LIMIT 1),NULL) AS due_on "
                "FROM books b JOIN companies c ON c.id=b.company_id "
                "WHERE b.firm_id=? AND b.period=? ORDER BY c.name,b.id",
                (actor.firm_id, period),
            ).fetchall()
        return {"period": period, "rows": [dict(row) for row in rows]}
