"""FSC03 organization projection: existing access, plans and case load only.

The projection never grants access. It reports the effective book roles that the
server already enforces, the latest service plan, and explicit gaps so a shared
centre can reassign work without turning an assignment into a permission.
"""

from __future__ import annotations

from ..accounting.access import AccessControl
from ..accounting.service import now


class FscOrganizationService:
    def __init__(self, db):
        self.db = db
        self.access = AccessControl()

    def matrix(self, actor):
        with self.db.transaction(actor=actor) as conn:
            self.access.require_manager(conn, actor)
            companies = conn.execute(
                "SELECT c.id,c.name,fc.status FROM firm_clients fc JOIN companies c ON c.id=fc.company_id "
                "WHERE fc.firm_id=? ORDER BY c.name,c.id",
                (actor.firm_id,),
            ).fetchall()
            people = conn.execute(
                "SELECT u.id,u.name,u.active,m.role FROM memberships m JOIN users u ON u.id=m.user_id "
                "WHERE m.firm_id=? ORDER BY u.name,u.id",
                (actor.firm_id,),
            ).fetchall()
            grouped = []
            for company in companies:
                books = conn.execute(
                    "SELECT id,name,period FROM books WHERE firm_id=? AND company_id=? ORDER BY period DESC,id",
                    (actor.firm_id, company["id"]),
                ).fetchall()
                book_rows, warnings = [], []
                for book in books:
                    plan = conn.execute(
                        "SELECT * FROM service_plan_versions WHERE book_id=? ORDER BY version DESC LIMIT 1",
                        (book["id"],),
                    ).fetchone()
                    book_access = {
                        row["user_id"]: row["role"]
                        for row in conn.execute(
                            "SELECT user_id,role FROM book_access WHERE book_id=? AND firm_id=?",
                            (book["id"], actor.firm_id),
                        )
                    }
                    if not plan:
                        warnings.append({"kind": "missing_service_plan", "book_id": book["id"]})
                    else:
                        if not plan["lead_user_id"]:
                            warnings.append({"kind": "missing_lead", "book_id": book["id"]})
                        if (
                            plan["lead_user_id"]
                            and plan["lead_user_id"] == plan["reviewer_user_id"]
                        ):
                            warnings.append({"kind": "lead_reviewer_same", "book_id": book["id"]})
                        if plan["backup_user_id"] and plan["backup_user_id"] not in book_access:
                            warnings.append(
                                {
                                    "kind": "backup_without_book_access",
                                    "book_id": book["id"],
                                    "user_id": plan["backup_user_id"],
                                }
                            )
                    assigned = set(book_access)
                    if plan:
                        assigned |= {
                            value
                            for value in (
                                plan["lead_user_id"],
                                plan["reviewer_user_id"],
                                plan["backup_user_id"],
                                plan["contact_user_id"],
                            )
                            if value
                        }
                    book_rows.append(
                        {
                            "book_id": book["id"],
                            "book_name": book["name"],
                            "period": book["period"],
                            "status": None,
                            "plan": {
                                "version": plan["version"],
                                "lead_user_id": plan["lead_user_id"],
                                "reviewer_user_id": plan["reviewer_user_id"],
                                "backup_user_id": plan["backup_user_id"],
                                "contact_user_id": plan["contact_user_id"],
                                "due_on": plan["due_on"],
                                "notes": plan["notes"],
                            }
                            if plan
                            else None,
                            "book_roles": book_access,
                            "unplanned_access": sorted(
                                assigned
                                - (
                                    {
                                        plan["lead_user_id"],
                                        plan["reviewer_user_id"],
                                        plan["backup_user_id"],
                                        plan["contact_user_id"],
                                    }
                                    if plan
                                    else set()
                                )
                            ),
                        }
                    )
                grouped.append(
                    {
                        "company_id": company["id"],
                        "company_name": company["name"],
                        "client_status": company["status"],
                        "books": book_rows,
                        "warnings": warnings,
                    }
                )
            return {
                "as_of": now(),
                "scope": "firm",
                "company_count": len(grouped),
                "people": [
                    {
                        "user_id": p["id"],
                        "name": p["name"],
                        "active": bool(p["active"]),
                        "firm_role": p["role"],
                    }
                    for p in people
                ],
                "companies": grouped,
                "summary": {
                    "companies": len(grouped),
                    "books": sum(len(c["books"]) for c in grouped),
                    "warnings": sum(len(c["warnings"]) for c in grouped),
                    "unstaffed": sum(
                        1
                        for c in grouped
                        for b in c["books"]
                        if not b["plan"] and not b["book_roles"]
                    ),
                },
            }
