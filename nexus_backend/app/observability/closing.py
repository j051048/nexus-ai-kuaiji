"""Read-only continuous close radar.

The radar is a projection over existing service state. It never closes a
period, approves a draft, or sends a request.
"""

from ..accounting.access import AccessControl
from ..accounting.domain import AccountingError


class ClosingRadarService:
    def __init__(self, db, access=None):
        self.db = db
        self.access = access or AccessControl()

    def radar(self, actor, period):
        if len(period) != 7 or period[4] != "-" or not period[:4].isdigit() or not period[5:].isdigit():
            raise AccountingError("期间格式应为 YYYY-MM", 422)
        if not 1 <= int(period[5:]) <= 12:
            raise AccountingError("期间月份无效", 422)
        with self.db.transaction(actor=actor) as conn:
            self.access.require_manager(conn, actor)
            books = conn.execute(
                "SELECT b.id,b.company_id,c.name AS company_name,b.period,"
                "COALESCE(p.status,'open') AS period_status FROM books b "
                "JOIN companies c ON c.id=b.company_id "
                "LEFT JOIN periods p ON p.book_id=b.id AND p.period=? "
                "WHERE b.firm_id=? AND b.period=? ORDER BY c.name,b.id",
                (period, actor.firm_id, period),
            ).fetchall()
            items = []
            for book in books:
                drafts = conn.execute(
                    "SELECT count(*) FROM drafts WHERE book_id=? AND status='draft'",
                    (book["id"],),
                ).fetchone()[0]
                materials = conn.execute(
                    "SELECT count(*) FROM material_requests r WHERE r.book_id=? AND r.period=? "
                    "AND r.required=1 AND NOT EXISTS ("
                    "SELECT 1 FROM material_request_actions a WHERE a.request_id=r.id AND a.state='accepted')",
                    (book["id"], period),
                ).fetchone()[0]
                basis = conn.execute(
                    "SELECT count(*) FROM tax_basis_reviews WHERE book_id=? AND period=? AND status='draft'",
                    (book["id"], period),
                ).fetchone()[0]
                blockers = []
                if materials:
                    blockers.append(
                        {
                            "code": "materials_pending",
                            "label": "必需资料未验收",
                            "count": materials,
                            "owner_role": "client_finance",
                            "next_action": "打开月度资料清单，补齐并验收",
                        }
                    )
                if drafts:
                    blockers.append(
                        {
                            "code": "drafts_pending",
                            "label": "凭证待复核",
                            "count": drafts,
                            "owner_role": "reviewer",
                            "next_action": "打开待复核，按当前版本独立复核",
                        }
                    )
                if basis:
                    blockers.append(
                        {
                            "code": "tax_basis_pending",
                            "label": "税费核定待复核",
                            "count": basis,
                            "owner_role": "reviewer",
                            "next_action": "打开税务底稿，复核基数与人工依据",
                        }
                    )
                items.append(
                    {
                        "book_id": book["id"],
                        "company_id": book["company_id"],
                        "company_name": book["company_name"],
                        "period": period,
                        "period_status": book["period_status"],
                        "closable": not blockers and book["period_status"] == "open",
                        "blockers": blockers,
                    }
                )
        return {
            "period": period,
            "books": items,
            "counts": {
                "books": len(items),
                "blocked": sum(1 for item in items if item["blockers"]),
                "closable": sum(1 for item in items if item["closable"]),
                "closed": sum(1 for item in items if item["period_status"] == "closed"),
            },
            "note": "只读雷达：不自动关账、不自动复核、不替代项目经理判断。",
        }
