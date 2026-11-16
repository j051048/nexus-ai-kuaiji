"""Deterministic FDE value baseline.

Only system-observed elapsed time is reported. Human effort is never inferred
from timestamps, so the response explicitly marks labour time as unverified.
"""

from datetime import datetime

from ..accounting.access import AccessControl
from ..accounting.domain import AccountingError


class ValueBaselineService:
    def __init__(self, db, access=None):
        self.db = db
        self.access = access or AccessControl()

    def _month(self, column):
        return f"substr(CAST({column} AS TEXT), 1, 7)"

    @staticmethod
    def _rate(numerator, denominator):
        return round(numerator / denominator, 4) if denominator else None

    def overview(self, actor, company_id, period):
        if (
            len(period) != 7
            or period[4] != "-"
            or not period[:4].isdigit()
            or not period[5:].isdigit()
        ):
            raise AccountingError("期间格式应为 YYYY-MM", 422)
        if not 1 <= int(period[5:]) <= 12:
            raise AccountingError("期间月份无效", 422)
        with self.db.transaction(actor=actor) as conn:
            self.access.company(conn, actor, company_id)
            event_month = self._month("e.created_at")
            action_month = self._month("a.created_at")
            mapping_month = self._month("m.created_at")
            approved_month = self._month("d.approved_at")
            submitted = conn.execute(
                "SELECT count(*) FROM business_events e WHERE e.firm_id=? AND e.company_id=? "
                f"AND e.status<>'draft' AND {event_month}=?",
                (actor.firm_id, company_id, period),
            ).fetchone()[0]
            mapped = conn.execute(
                "SELECT count(*) FROM business_mappings m JOIN business_events e ON e.id=m.event_id "
                f"WHERE e.firm_id=? AND e.company_id=? AND {mapping_month}=?",
                (actor.firm_id, company_id, period),
            ).fetchone()[0]
            approved = conn.execute(
                "SELECT count(*) FROM drafts d JOIN books b ON b.id=d.book_id "
                f"WHERE b.firm_id=? AND b.company_id=? AND b.period=? AND d.status='approved' "
                f"AND d.approved_at IS NOT NULL AND {approved_month}=?",
                (actor.firm_id, company_id, period, period),
            ).fetchone()[0]
            returned = conn.execute(
                "SELECT count(*) FROM business_actions a JOIN business_events e ON e.id=a.event_id "
                f"WHERE e.firm_id=? AND e.company_id=? AND a.decision='return' AND {action_month}=?",
                (actor.firm_id, company_id, period),
            ).fetchone()[0]
            cost = conn.execute(
                "SELECT coalesce(sum(coalesce(c.confirmed_micro,c.estimated_micro,0)),0) "
                "FROM model_costs c WHERE c.firm_id=? AND c.company_id=? AND c.period=?",
                (actor.firm_id, company_id, period),
            ).fetchone()[0]
            elapsed_rows = conn.execute(
                "SELECT m.created_at,d.approved_at FROM business_mappings m "
                "JOIN business_events e ON e.id=m.event_id JOIN drafts d ON d.id=m.draft_id "
                f"WHERE e.firm_id=? AND e.company_id=? AND {mapping_month}=? "
                "AND d.status='approved' AND d.approved_at IS NOT NULL",
                (actor.firm_id, company_id, period),
            ).fetchall()
        elapsed = []
        for row in elapsed_rows:
            try:
                start = datetime.fromisoformat(str(row[0]).replace("Z", "+00:00"))
                end = datetime.fromisoformat(str(row[1]).replace("Z", "+00:00"))
            except ValueError:
                continue
            hours = (end - start).total_seconds() / 3600
            if hours >= 0:
                elapsed.append(round(hours, 2))
        elapsed.sort()
        median = elapsed[len(elapsed) // 2] if elapsed else None
        return {
            "company_id": company_id,
            "period": period,
            "counts": {
                "submitted_events": submitted,
                "mapped_drafts": mapped,
                "approved_drafts": approved,
                "returned_events": returned,
            },
            "rates": {
                "draft_mapping_rate": self._rate(mapped, submitted),
                "first_pass_rate": self._rate(max(submitted - returned, 0), submitted),
                "exception_rate": self._rate(returned, submitted),
                "review_conversion_rate": self._rate(approved, mapped),
            },
            "elapsed": {
                "system_elapsed_hours_median": median,
                "system_elapsed_hours_samples": len(elapsed),
                "labour_time_verified": False,
                "note": "仅系统从映射到复核的时间，不代表人工工时，不能用于人效考核。",
            },
            "cost": {
                "ai_model_cost_micro": cost,
                "ai_cost_per_approved_micro": round(cost / approved) if approved else None,
                "scope": "ai_model_only",
                "note": "仅为 AI 模型费用，不含人工、基础设施和客户费用。",
            },
        }
