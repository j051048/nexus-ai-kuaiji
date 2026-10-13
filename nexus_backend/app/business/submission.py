"""Company-authorized intake channels, frozen on each newly created event."""

from ..accounting.access import AccessControl
from ..accounting.domain import AccountingError
from ..accounting.service import now


class SubmissionPolicyService:
    def __init__(self, db):
        self.db, self.access = db, AccessControl()

    @staticmethod
    def current(conn, firm_id, company_id):
        row = conn.execute(
            "SELECT mode,version,reason,updated_at FROM business_submission_policies "
            "WHERE firm_id=? AND company_id=?",
            (firm_id, company_id),
        ).fetchone()
        return (
            dict(row)
            if row
            else {"mode": "department", "version": 0, "reason": "", "updated_at": None}
        )

    def get(self, actor, company_id):
        with self.db.transaction(actor=actor) as conn:
            self.access.company(conn, actor, company_id, manage=True)
            return self.current(conn, actor.firm_id, company_id)

    def update(self, actor, company_id, request):
        from .service import encode

        with self.db.transaction(write=True, actor=actor) as conn:
            self.access.company(conn, actor, company_id, manage=True)
            previous = self.current(conn, actor.firm_id, company_id)
            if previous["version"] != request.expected_version:
                raise AccountingError("提交方式已变化，请刷新后重试", 409)
            stamp = now()
            conn.execute(
                "INSERT INTO business_submission_policies(firm_id,company_id,mode,version,reason,updated_by,updated_at) "
                "VALUES(?,?,?,?,?,?,?) ON CONFLICT(firm_id,company_id) DO UPDATE SET "
                "mode=excluded.mode,version=excluded.version,reason=excluded.reason,updated_by=excluded.updated_by,updated_at=excluded.updated_at",
                (
                    actor.firm_id,
                    company_id,
                    request.mode,
                    previous["version"] + 1,
                    request.reason,
                    actor.user_id,
                    stamp,
                ),
            )
            conn.execute(
                "INSERT INTO audit_events(firm_id,user_id,action,object_id,details,created_at) VALUES(?,?,?,?,?,?)",
                (
                    actor.firm_id,
                    actor.user_id,
                    "business.submission_policy.update",
                    company_id,
                    encode(
                        {
                            "previous": previous["mode"],
                            "mode": request.mode,
                            "version": previous["version"] + 1,
                            "reason": request.reason,
                        }
                    ),
                    stamp,
                ),
            )
            return self.current(conn, actor.firm_id, company_id)

    def direct_books(self, conn, actor, company_id, period=None):
        """Return only books already editable by this accounting identity."""
        from ..jobs.visibility import book_edit_scope

        where, values = book_edit_scope(actor)
        sql = "SELECT b.id FROM books b WHERE " + where + " AND b.company_id=?"
        values.append(company_id)
        if period:
            sql += " AND b.period=?"
            values.append(period)
        return [row["id"] for row in conn.execute(sql, values)]

    def period_sql(self, alias="e"):
        """Trusted internal aliases only; both dialects read the same saved date."""
        return (
            f"left({alias}.payload::jsonb->>'occurred_on',7)"
            if self.db.is_postgres
            else f"substr(json_extract({alias}.payload,'$.occurred_on'),1,7)"
        )

    def can_receive(self, conn, actor, event):
        if event["submission_mode"] != "direct" or event["applicant_id"] == actor.user_id:
            return False
        import json

        period = json.loads(event["payload"]).get("occurred_on", "")[:7]
        return bool(period and self.direct_books(conn, actor, event["company_id"], period))
