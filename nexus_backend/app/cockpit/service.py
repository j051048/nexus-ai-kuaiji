"""One read-only query surface for firm workload and delivery risk."""

from datetime import date
import re

from ..accounting.domain import AccountingError


class FirmCockpitService:
    def __init__(self, db, accounting):
        self.db, self.access = db, accounting.access

    def snapshot(self, actor, period=""):
        if period and not re.fullmatch(r"20\d{2}-(0[1-9]|1[0-2])", period):
            raise AccountingError("请选择有效的 YYYY-MM 月份", 422)
        with self.db.transaction(actor=actor) as conn:
            self.access.require_manager(conn, actor)
            companies = conn.execute(
                "SELECT c.id,c.name,fc.status FROM companies c JOIN firm_clients fc "
                "ON fc.company_id=c.id AND fc.firm_id=? WHERE fc.status='active' "
                "ORDER BY c.name,c.id",
                (actor.firm_id,),
            ).fetchall()
            book_sql = (
                "SELECT b.id,b.company_id,b.name,b.period,p.status AS period_status "
                "FROM books b LEFT JOIN periods p ON p.book_id=b.id "
                "WHERE b.firm_id=?"
            )
            book_args = [actor.firm_id]
            if period:
                book_sql += " AND b.period=?"
                book_args.append(period)
            else:
                book_sql += " AND b.period=(SELECT MAX(b2.period) FROM books b2 WHERE b2.firm_id=b.firm_id AND b2.company_id=b.company_id)"
            books = {
                row["company_id"]: row
                for row in conn.execute(book_sql + " ORDER BY b.period DESC,b.id", book_args)
            }
            book_ids = [book["id"] for book in books.values()]
            plan_rows = (
                conn.execute(
                    "SELECT p.book_id,p.company_id,p.lead_user_id,p.reviewer_user_id,p.backup_user_id,"
                    "p.due_on,lead.name AS lead_name,reviewer.name AS reviewer_name "
                    "FROM service_plan_versions p JOIN books b ON b.id=p.book_id "
                    "AND b.firm_id=p.firm_id AND b.company_id=p.company_id "
                    "LEFT JOIN users lead ON lead.id=p.lead_user_id "
                    "LEFT JOIN users reviewer ON reviewer.id=p.reviewer_user_id "
                    "WHERE p.firm_id=? AND p.book_id IN ("
                    + ",".join("?" for _ in book_ids)
                    + ") AND p.version=("
                    "SELECT MAX(p2.version) FROM service_plan_versions p2 WHERE p2.book_id=p.book_id "
                    "AND p2.firm_id=p.firm_id AND p2.company_id=p.company_id"
                    ")",
                    (actor.firm_id, *book_ids),
                ).fetchall()
                if book_ids
                else []
            )
            plans = {row["book_id"]: row for row in plan_rows}
            tasks = conn.execute(
                "SELECT company_id,kind,state,due_on,updated_at,assignee_id FROM workspace_task_projections "
                "WHERE firm_id=?",
                (actor.firm_id,),
            ).fetchall()
            projection = conn.execute(
                "SELECT COUNT(*) AS total,"
                "SUM(CASE WHEN s.revision IS NULL OR s.revision>s.built_revision OR s.failures>0 THEN 1 ELSE 0 END) AS stale "
                "FROM firm_clients fc LEFT JOIN workspace_projection_state s "
                "ON s.firm_id=fc.firm_id AND s.company_id=fc.company_id "
                "WHERE fc.firm_id=? AND fc.status='active'",
                (actor.firm_id,),
            ).fetchone()

        grouped = {}
        for task in tasks:
            grouped.setdefault(task["company_id"], []).append(task)
        today = date.today().isoformat()
        rows = []
        workload = {}
        for company in companies:
            book = books.get(company["id"])
            plan = plans.get(book["id"]) if book else None
            company_tasks = grouped.get(company["id"], [])
            counts = {kind: 0 for kind in ("intake", "review", "exception", "delivery")}
            overdue = 0
            last_activity = ""
            for task in company_tasks:
                kind = task["kind"]
                if kind in {"intake", "business", "approval", "finance", "handoff", "material"}:
                    counts["intake"] += 1
                elif kind in {"review", "ocr", "proposal"}:
                    counts["review"] += 1
                elif kind in {"case", "alert"}:
                    counts["exception"] += 1
                elif kind in {"export", "delivery"}:
                    counts["delivery"] += 1
                if task["due_on"] and task["due_on"] < today:
                    overdue += 1
                last_activity = max(last_activity, task["updated_at"] or "")
            period_status = book["period_status"] if book else "missing"
            stage = (
                "delivery"
                if counts["delivery"]
                else "review"
                if counts["review"] or counts["exception"]
                else "intake"
                if counts["intake"]
                else "closed"
                if period_status == "closed"
                else "accounting"
                if book
                else "unopened"
            )
            risk = counts["exception"] * 4 + counts["review"] * 2 + overdue * 2
            if not book:
                risk += 4
            if projection["stale"]:
                risk += 1
            row = {
                "company_id": company["id"],
                "company_name": company["name"],
                "book_id": book["id"] if book else None,
                "book_name": book["name"] if book else "",
                "period": book["period"] if book else period,
                "period_status": period_status,
                "stage": stage,
                "tasks": {
                    "intake": counts["intake"],
                    "review": counts["review"],
                    "exception": counts["exception"],
                    "delivery": counts["delivery"],
                    "overdue": overdue,
                },
                "last_activity_at": last_activity,
                "lead_user_id": plan["lead_user_id"] if plan else None,
                "lead_name": plan["lead_name"] if plan else "",
                "reviewer_user_id": plan["reviewer_user_id"] if plan else None,
                "reviewer_name": plan["reviewer_name"] if plan else "",
                "due_on": plan["due_on"] if plan else "",
                "risk": risk,
            }
            rows.append(row)
            key = row["lead_user_id"] or "unassigned"
            bucket = workload.setdefault(
                key,
                {
                    "user_id": row["lead_user_id"],
                    "name": row["lead_name"] or "未分配",
                    "clients": 0,
                    "pending": 0,
                    "exceptions": 0,
                    "overdue": 0,
                    "risk_clients": 0,
                },
            )
            bucket["clients"] += 1
            bucket["pending"] += (
                counts["intake"] + counts["review"] + counts["exception"] + counts["delivery"]
            )
            bucket["exceptions"] += counts["exception"]
            bucket["overdue"] += overdue
            bucket["risk_clients"] += 1 if risk >= 4 else 0

        stage_counts = {}
        for row in rows:
            stage_counts[row["stage"]] = stage_counts.get(row["stage"], 0) + 1
        risks = sorted(rows, key=lambda row: (-row["risk"], row["company_name"]))[:20]
        return {
            "period": period or (rows[0]["period"] if rows else ""),
            "summary": {
                "clients": len(rows),
                "opened": sum(1 for row in rows if row["book_id"]),
                "closed": sum(1 for row in rows if row["period_status"] == "closed"),
                "overdue": sum(row["tasks"]["overdue"] for row in rows),
                "exceptions": sum(row["tasks"]["exception"] for row in rows),
                "risk_clients": sum(1 for row in rows if row["risk"] >= 4),
                "projection_stale": int(projection["stale"] or 0),
            },
            "stages": stage_counts,
            "companies": rows,
            "workload": sorted(workload.values(), key=lambda row: (-row["pending"], row["name"])),
            "risks": risks,
        }
