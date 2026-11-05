"""Bounded firm overview. Read-model observations never grant authorization."""

from datetime import datetime
import re
from zoneinfo import ZoneInfo

from ..accounting.domain import AccountingError

# Aggregate in SQL. Never load every task or every client into Python.
CLIENTS = """
WITH clients AS (
 SELECT c.id,c.name FROM companies c JOIN firm_clients f ON f.company_id=c.id
 WHERE f.firm_id=? AND f.status='active' AND c.status='active'
), chosen_books AS (
 SELECT b.*,p.status AS period_status FROM books b
 JOIN clients c ON c.id=b.company_id LEFT JOIN periods p ON p.book_id=b.id
 WHERE b.firm_id=? AND (b.period=? OR (?='' AND b.period=(
 SELECT MAX(b2.period) FROM books b2 WHERE b2.firm_id=b.firm_id AND b2.company_id=b.company_id)))
), book_totals AS (
 SELECT company_id,COUNT(*) AS book_count,MIN(period) AS period,
 CASE WHEN COUNT(*)=1 THEN MIN(id) END AS book_id,
 CASE WHEN COUNT(*)=1 THEN MIN(name) ELSE '' END AS book_name,
 CASE WHEN MIN(period_status)='closed' AND MAX(period_status)='closed' THEN 'closed'
 ELSE 'open' END AS period_status FROM chosen_books GROUP BY company_id
), task_totals AS (
 SELECT t.company_id,
 SUM(CASE WHEN kind IN ('intake','business','approval','finance','handoff','material') THEN 1 ELSE 0 END) AS intake,
 SUM(CASE WHEN kind IN ('review','ocr','proposal') THEN 1 ELSE 0 END) AS review,
 SUM(CASE WHEN kind IN ('case','alert') THEN 1 ELSE 0 END) AS exception,
 SUM(CASE WHEN kind IN ('export','delivery') THEN 1 ELSE 0 END) AS delivery,
 SUM(CASE WHEN due_on<>'' AND due_on < ? THEN 1 ELSE 0 END) AS overdue,MAX(updated_at) AS last_activity_at
 FROM workspace_task_projections t JOIN clients c ON c.id=t.company_id
 WHERE t.firm_id=? GROUP BY t.company_id
), base AS (
 SELECT c.id AS company_id,c.name AS company_name,b.book_id,COALESCE(b.book_count,0) AS book_count,
 COALESCE(b.book_name,'') AS book_name,COALESCE(b.period,?) AS period,
 COALESCE(b.period_status,'missing') AS period_status,
 COALESCE(t.intake,0) AS intake,COALESCE(t.review,0) AS review,
 COALESCE(t.exception,0) AS exception,COALESCE(t.delivery,0) AS delivery,
 COALESCE(t.overdue,0) AS overdue,COALESCE(t.last_activity_at,'') AS last_activity_at,
 p.lead_user_id,p.reviewer_user_id,COALESCE(lead.name,'') AS lead_name,
 COALESCE(reviewer.name,'') AS reviewer_name,COALESCE(p.due_on,'') AS due_on,
 CASE WHEN s.revision IS NULL OR s.revision>s.built_revision OR s.failures>0 THEN 1 ELSE 0 END AS stale
 FROM clients c LEFT JOIN book_totals b ON b.company_id=c.id
 LEFT JOIN task_totals t ON t.company_id=c.id
 LEFT JOIN service_plan_versions p ON p.book_id=b.book_id AND p.company_id=c.id AND p.firm_id=?
 AND p.version=(SELECT MAX(p2.version) FROM service_plan_versions p2
 WHERE p2.book_id=p.book_id AND p2.firm_id=p.firm_id AND p2.company_id=p.company_id)
 LEFT JOIN users lead ON lead.id=p.lead_user_id LEFT JOIN users reviewer ON reviewer.id=p.reviewer_user_id
 LEFT JOIN workspace_projection_state s ON s.firm_id=? AND s.company_id=c.id
), scored AS (
 SELECT base.*,exception*4+review*2+overdue*2+stale+CASE WHEN book_count=0 THEN 4 ELSE 0 END AS risk,
 CASE WHEN delivery>0 THEN 'delivery' WHEN review>0 OR exception>0 THEN 'review'
 WHEN intake>0 THEN 'intake' WHEN period_status='closed' THEN 'closed'
 WHEN book_count>0 THEN 'accounting' ELSE 'unopened' END AS stage FROM base
)
"""


def client(row):
    result = dict(row)
    result["tasks"] = {
        key: int(result.pop(key))
        for key in ("intake", "review", "exception", "delivery", "overdue")
    }
    result.pop("stale")
    result.pop("_total", None)
    return result


class FirmCockpitService:
    def __init__(self, db, accounting):
        self.db, self.access = db, accounting.access

    def snapshot(self, actor, period="", *, limit=20, offset=0, search=""):
        if period and not re.fullmatch(r"20\d{2}-(0[1-9]|1[0-2])", period):
            raise AccountingError("请选择有效的 YYYY-MM 月份", 422)
        if (
            type(limit) is not int
            or not 1 <= limit <= 100
            or type(offset) is not int
            or offset < 0
            or not isinstance(search, str)
            or len(search) > 200
        ):
            raise AccountingError("客户分页参数无效", 422)
        search = search.strip()
        pattern = "%" + search.replace("!", "!!").replace("%", "!%").replace("_", "!_") + "%"
        today = datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
        args = (
            actor.firm_id,
            actor.firm_id,
            period,
            period,
            today,
            actor.firm_id,
            period,
            actor.firm_id,
            actor.firm_id,
        )
        with self.db.transaction(actor=actor) as conn:
            self.access.require_manager(conn, actor)
            summary = dict(
                conn.execute(
                    CLIENTS
                    + """
              SELECT COUNT(*) AS clients,
              COALESCE(SUM(CASE WHEN book_count>0 THEN 1 ELSE 0 END),0) AS opened,
              COALESCE(SUM(CASE WHEN period_status='closed' THEN 1 ELSE 0 END),0) AS closed,
              COALESCE(SUM(overdue),0) AS overdue,COALESCE(SUM(exception),0) AS exceptions,
              COALESCE(SUM(CASE WHEN risk>=4 THEN 1 ELSE 0 END),0) AS risk_clients,
              COALESCE(SUM(stale),0) AS projection_stale FROM scored
            """,
                    args,
                ).fetchone()
            )
            page = conn.execute(
                CLIENTS
                + """
              , matching AS (SELECT * FROM scored WHERE company_name LIKE ? ESCAPE '!'),
              total AS (SELECT COUNT(*) AS _total FROM matching),
              page AS (SELECT * FROM matching ORDER BY company_name,company_id LIMIT ? OFFSET ?)
              SELECT page.*,total._total FROM total LEFT JOIN page ON 1=1 ORDER BY company_name,company_id
            """,
                (*args, pattern, limit, offset),
            ).fetchall()
            coordination = conn.execute(
                CLIENTS
                + """SELECT * FROM scored
              WHERE overdue>0 OR exception>0 OR book_count=0 OR book_count>1 OR lead_user_id IS NULL
              ORDER BY overdue DESC,exception DESC,company_name,company_id LIMIT 6""",
                args,
            ).fetchall()
            workload = conn.execute(
                CLIENTS
                + """SELECT lead_user_id AS user_id,
              COALESCE(NULLIF(lead_name,''),'未分配') AS name,COUNT(*) AS clients,
              SUM(intake+review+exception+delivery) AS pending,SUM(exception) AS exceptions,
              SUM(overdue) AS overdue,SUM(CASE WHEN risk>=4 THEN 1 ELSE 0 END) AS risk_clients
              FROM scored GROUP BY lead_user_id,lead_name ORDER BY pending DESC,name LIMIT 50""",
                args,
            ).fetchall()
            stages = conn.execute(
                CLIENTS + "SELECT stage,COUNT(*) AS total FROM scored GROUP BY stage", args
            ).fetchall()
            risks = conn.execute(
                CLIENTS
                + "SELECT * FROM scored ORDER BY risk DESC,company_name,company_id LIMIT 20",
                args,
            ).fetchall()
            # Assignment counts are distinct from the lead accountant's client totals.
            # A detached assignment is not live work; assigning never grants access.
            assigned = conn.execute(
                """WITH grouped AS (
              SELECT a.assignee_id AS user_id,COALESCE(u.name,'待确认成员') AS name,
              COUNT(*) AS pending,SUM(CASE WHEN COALESCE(a.due_on,t.due_on)<>'' AND COALESCE(a.due_on,t.due_on)<? THEN 1 ELSE 0 END) AS overdue
              FROM firm_task_assignments a JOIN workspace_task_projections t
              ON t.firm_id=a.firm_id AND t.kind=a.kind AND t.ref=a.ref
              JOIN firm_clients f ON f.firm_id=t.firm_id AND f.company_id=t.company_id AND f.status='active'
              JOIN companies c ON c.id=t.company_id AND c.status='active'
              LEFT JOIN users u ON u.id=a.assignee_id WHERE a.firm_id=? AND a.assignee_id IS NOT NULL
              GROUP BY a.assignee_id,u.name)
              SELECT *,SUM(pending) OVER () AS total,COUNT(*) OVER () AS people_total
              FROM grouped ORDER BY pending DESC,user_id LIMIT 20""",
                (today, actor.firm_id),
            ).fetchall()
        total = int(page[0]["_total"])
        return {
            "scope": {
                "firm_id": actor.firm_id,
                "user_id": actor.user_id,
                "period": period,
                "search": search,
            },
            "period": period,
            "summary": {k: int(v) for k, v in summary.items()},
            "stages": {r["stage"]: int(r["total"]) for r in stages},
            "companies": [client(r) for r in page if r["company_id"] is not None],
            "pagination": {
                "limit": limit,
                "offset": offset,
                "total": total,
                "has_more": offset + limit < total,
            },
            "coordination": [client(r) for r in coordination],
            "workload": [dict(r) for r in workload],
            "risks": [client(r) for r in risks],
            "assignments": {
                "basis": "active_task_assignments",
                "total": int(assigned[0]["total"]) if assigned else 0,
                "people_total": int(assigned[0]["people_total"]) if assigned else 0,
                "stale": bool(summary["projection_stale"]),
                "people": [
                    {
                        "user_id": r["user_id"],
                        "name": r["name"],
                        "pending": int(r["pending"]),
                        "overdue": int(r["overdue"]),
                    }
                    for r in assigned
                ],
            },
        }
