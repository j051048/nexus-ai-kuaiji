"""Administrative metadata access never opens suspended companies' ledgers."""

import json
from urllib.parse import urlsplit

from .access import AccessControl, FIRM_MANAGERS
from .directory import DirectoryService
from .domain import AccountingError
from .service import now


class AdminService:
    def __init__(self, db):
        self.db = db
        self.access = AccessControl()

    def onboarding(self, actor, company_id, period):
        from .onboarding import company_onboarding

        return company_onboarding(self, actor, company_id, period)

    def require_admin(self, conn, actor):
        role = self.access.firm_role(conn, actor)
        if role in FIRM_MANAGERS:
            return True
        allowed = conn.execute(
            "SELECT 1 FROM company_memberships cm JOIN firm_clients fc ON fc.company_id=cm.company_id "
            "WHERE fc.firm_id=? AND fc.status='active' AND cm.user_id=? AND cm.role IN ('owner','admin') LIMIT 1",
            (actor.firm_id, actor.user_id),
        ).fetchone()
        if not allowed:
            raise AccountingError("需要事务所或企业管理员权限", 403)
        return False

    def require_admin_company(self, conn, actor, company_id):
        manager = self.require_admin(conn, actor)
        row = conn.execute(
            "SELECT c.*,fc.status AS client_status FROM companies c JOIN firm_clients fc ON fc.company_id=c.id "
            "WHERE c.id=? AND fc.firm_id=?",
            (company_id, actor.firm_id),
        ).fetchone()
        if not row:
            raise AccountingError("企业不存在或无权管理", 404)
        if not manager:
            if row["status"] != "active" or row["client_status"] != "active":
                raise AccountingError("企业不存在或无权管理", 404)
            self.access.company(conn, actor, company_id, manage=True)
        return {key: value for key, value in dict(row).items() if key != "client_status"}

    @staticmethod
    def _scope(actor, manager, alias="c"):
        if manager:
            return "1=1", []
        return (
            f"{alias}.status='active' AND fc.status='active' AND EXISTS(SELECT 1 FROM company_memberships cm "
            f"WHERE cm.company_id={alias}.id AND cm.user_id=? AND cm.role IN ('owner','admin'))",
            [actor.user_id],
        )

    def _company(self, conn, actor, row, manager):
        company = dict(row)
        if "member_count" in company and "book_count" in company:
            pass
        elif self.db.is_postgres:
            counts = conn.execute(
                "SELECT * FROM accounting.admin_company_counts(?)", (company["id"],)
            ).fetchone()
            company.update(
                {"member_count": counts["member_count"], "book_count": counts["book_count"]}
            )
        else:
            company["member_count"] = conn.execute(
                "SELECT COUNT(*) FROM company_memberships WHERE company_id=?", (company["id"],)
            ).fetchone()[0]
            company["book_count"] = conn.execute(
                "SELECT COUNT(*) FROM books WHERE firm_id=? AND company_id=?",
                (actor.firm_id, company["id"]),
            ).fetchone()[0]
        company.update(can_manage=True, can_change_status=manager)
        return company

    def list_companies(self, actor, *, search="", status=None, limit=50, offset=0):
        with self.db.transaction(actor=actor) as conn:
            manager = self.require_admin(conn, actor)
            scope, scope_params = self._scope(actor, manager)
            where = f"fc.firm_id=? AND ({scope})"
            params = [actor.firm_id, *scope_params]
            if search.strip():
                # Treat wildcard characters literally; company search is not a SQL pattern UI.
                value = (
                    search.strip()
                    .lower()
                    .replace("\\", "\\\\")
                    .replace("%", "\\%")
                    .replace("_", "\\_")
                )
                where += (
                    " AND (lower(c.name) LIKE ? ESCAPE '\\' OR lower(c.tax_id) LIKE ? ESCAPE '\\')"
                )
                params += [f"%{value}%", f"%{value}%"]
            if status:
                where += " AND c.status=?"
                params.append(status)
            source = "FROM companies c JOIN firm_clients fc ON fc.company_id=c.id "
            base = source + "WHERE " + where
            total = conn.execute("SELECT COUNT(*) " + base, params).fetchone()[0]
            if self.db.is_postgres:
                listing = (
                    "SELECT c.*,counts.member_count,counts.book_count "
                    + source
                    + "LEFT JOIN LATERAL accounting.admin_company_counts(c.id) counts ON true WHERE "
                    + where
                )
            else:
                listing = (
                    "SELECT c.*,(SELECT COUNT(*) FROM company_memberships cm WHERE cm.company_id=c.id) AS member_count,(SELECT COUNT(*) FROM books b WHERE b.company_id=c.id AND b.firm_id=fc.firm_id) AS book_count "
                    + base
                )
            rows = conn.execute(
                listing + " ORDER BY c.name,c.id LIMIT ? OFFSET ?", [*params, limit, offset]
            ).fetchall()
            return {
                "companies": [self._company(conn, actor, row, manager) for row in rows],
                "total": total,
                "limit": limit,
                "offset": offset,
            }

    def get_company(self, actor, company_id):
        with self.db.transaction(actor=actor) as conn:
            row = self.require_admin_company(conn, actor, company_id)
            manager = self.access.firm_role(conn, actor) in FIRM_MANAGERS
            company = self._company(conn, actor, row, manager)
            if self.db.is_postgres:
                # This narrow SECURITY DEFINER returns metadata, never original documents or ledger rows.
                metadata = conn.execute(
                    "SELECT accounting.admin_company_metadata(?) AS data", (company_id,)
                ).fetchone()["data"]
                return {
                    "company": company,
                    **metadata,
                    "pending_counts": self._pending_counts(conn, actor, company_id),
                }
            members = [
                dict(item)
                for item in conn.execute(
                    "SELECT cm.user_id,u.name,u.email,u.active,cm.role,cm.department_id FROM company_memberships cm JOIN users u ON u.id=cm.user_id WHERE cm.company_id=? ORDER BY u.name,u.id",
                    (company_id,),
                )
            ]
            departments = [
                dict(item)
                for item in conn.execute(
                    "SELECT id,name FROM departments WHERE company_id=? ORDER BY name,id",
                    (company_id,),
                )
            ]
            books = [
                dict(item)
                for item in conn.execute(
                    "SELECT id,name,period FROM books WHERE firm_id=? AND company_id=? ORDER BY period DESC,name,id",
                    (actor.firm_id, company_id),
                )
            ]
            pending = self._pending_counts(conn, actor, company_id)
            return {
                "company": company,
                "members": members,
                "departments": departments,
                "books": books,
                "pending_counts": pending,
            }

    @staticmethod
    def _ai_control_metadata(company, settings, intelligence, chat, *, active):
        from ..intelligence.provider import effective_settings

        stored = dict(settings) if settings else {}
        provider = getattr(intelligence.provider, "settings", None)
        ocr_ready = bool(getattr(provider, "extraction_available", False))
        journal_ready = bool(getattr(provider, "suggestion_available", False))
        chat_ready = bool(getattr(chat, "available", False))
        labels = []
        for scenario, available, source in (
            ("ocr", ocr_ready, provider),
            ("journal", journal_ready, provider),
            ("chat", chat_ready, getattr(chat, "settings", None)),
        ):
            if not available:
                continue
            try:
                label = urlsplit(effective_settings(source, scenario).base_url).hostname
            except (AttributeError, TypeError, ValueError):
                label = None
            if label and label not in labels:
                labels.append(label)
        return {
            "company": {key: company[key] for key in ("id", "name", "status", "version")},
            "active": active,
            "ai": {
                "enabled": bool(stored.get("enabled", False)),
                "updated_at": stored.get("updated_at"),
                "can_manage": active,
                "provider_label": " / ".join(labels)
                or (
                    "已配置模型服务"
                    if ocr_ready or journal_ready or chat_ready
                    else "尚未配置模型服务"
                ),
                "ocr_ready": ocr_ready,
                "journal_ready": journal_ready,
                "chat_ready": chat_ready,
                "configured": ocr_ready or journal_ready or chat_ready,
            },
        }

    def ai_control(self, actor, company_id, intelligence, operations, chat=None, *, period=None):
        # Suspended company access is metadata-only for firm managers. It must
        # never reopen operations, private evidence, costs or ledger queries.
        with self.db.transaction(actor=actor) as conn:
            company = self.require_admin_company(conn, actor, company_id)
            link = conn.execute(
                "SELECT status FROM firm_clients WHERE firm_id=? AND company_id=?",
                (actor.firm_id, company_id),
            ).fetchone()
            active = company["status"] == "active" and link["status"] == "active"
            if active:
                self.access.company(conn, actor, company_id, manage=True)
            settings = conn.execute(
                "SELECT enabled,updated_at FROM ai_company_settings WHERE firm_id=? AND company_id=?",
                (actor.firm_id, company_id),
            ).fetchone()
            metadata = self._ai_control_metadata(
                company, settings, intelligence, chat, active=active
            )
        if not active:
            return {
                **metadata,
                "period": period,
                "currency": "CNY",
                "policy": None,
                "usage": None,
                "queue": None,
                "exceptions": None,
                "workers": None,
                "assistant": None,
                "alerts": [],
                "costs": [],
                "can_manage_budget": False,
                "monitor": None,
            }
        overview = operations.overview(actor, company_id, period=period)
        usage = dict(overview["usage"])
        input_tokens, output_tokens = usage.get("input_tokens"), usage.get("output_tokens")
        usage["total_tokens"] = (
            input_tokens + output_tokens
            if input_tokens is not None and output_tokens is not None
            else None
        )
        return {**overview, **metadata, "usage": usage}

    def update_ai_control(self, actor, company_id, request, intelligence, chat=None):
        with self.db.transaction(write=True, actor=actor, company_id=company_id) as conn:
            company = self.access.company(conn, actor, company_id, manage=True)
            if company["version"] != request.expected_company_version:
                raise AccountingError("企业资料或状态已改变，请刷新后重试", 409)
            row = conn.execute(
                "SELECT enabled,updated_at FROM ai_company_settings WHERE firm_id=? AND company_id=?",
                (actor.firm_id, company_id),
            ).fetchone()
            enabled, updated_at = bool(row and row["enabled"]), row["updated_at"] if row else None
            if request.expected_enabled != enabled or request.expected_updated_at != updated_at:
                raise AccountingError("企业 AI 开关已更新，请刷新后重试", 409)
            stamp = (
                conn.execute(
                    "SELECT to_char(clock_timestamp() AT TIME ZONE 'UTC','YYYY-MM-DD\"T\"HH24:MI:SS.US\"+00:00\"')"
                ).fetchone()[0]
                if self.db.is_postgres
                else now()
            )
            conn.execute(
                "INSERT INTO ai_company_settings(firm_id,company_id,enabled,updated_by,updated_at) "
                "VALUES(?,?,?,?,?) ON CONFLICT(firm_id,company_id) DO UPDATE SET "
                "enabled=excluded.enabled,updated_by=excluded.updated_by,updated_at=excluded.updated_at",
                (actor.firm_id, company_id, int(request.enabled), actor.user_id, stamp),
            )
            DirectoryService._audit(
                conn,
                actor,
                "admin.ai.configure",
                company_id,
                {
                    "company_id": company_id,
                    "before_enabled": enabled,
                    "enabled": request.enabled,
                    "reason": request.reason,
                    "company_version": company["version"],
                },
            )
            return self._ai_control_metadata(
                company,
                {"enabled": request.enabled, "updated_at": stamp},
                intelligence,
                chat,
                active=True,
            )

    def update_ai_policy(self, actor, company_id, request, operations):
        from ..operations.requests import PolicyRequest

        with self.db.transaction(actor=actor) as conn:
            self.access.company(conn, actor, company_id, manage=True)
        # Reason is audit data, never a model_company_policies storage field.
        policy = PolicyRequest.model_validate(request.model_dump(exclude={"reason"}))
        return operations.policy(actor, company_id, policy, reason=request.reason)

    def _pending_counts(self, conn, actor, company_id):
        from ..business.submission import SubmissionPolicyService
        from ..cases.service import CaseService
        from ..jobs.visibility import book_edit_scope, event_scope

        event_sql, event_values = event_scope(actor)
        book_sql, book_values = book_edit_scope(actor)
        period = SubmissionPolicyService(self.db).period_sql("e")
        direct = (
            "(e.status='submitted' AND e.submission_mode='direct' AND e.applicant_id<>? AND EXISTS(SELECT 1 FROM books b WHERE b.company_id=e.company_id AND b.period="
            + period
            + " AND "
            + book_sql
            + "))"
        )
        events = f"e.firm_id=? AND e.company_id=? AND e.status NOT IN ('draft','returned') AND ({event_sql} OR {direct})"
        event_params = [actor.firm_id, company_id, *event_values, actor.user_id, *book_values]
        case_sql, case_values = CaseService(self.db, None)._scope_sql(actor)
        count = lambda sql, values: conn.execute(sql, values).fetchone()[0]
        return {
            "business_events": count(
                "SELECT COUNT(*) FROM business_events e WHERE "
                + events
                + " AND e.status IN ('submitted','department_approved','finance_approved')",
                event_params,
            ),
            "ocr_jobs": count(
                "SELECT COUNT(*) FROM ocr_jobs j JOIN business_events e ON e.id=j.event_id AND e.firm_id=j.firm_id AND e.company_id=j.company_id WHERE j.status IN ('queued','running','retry_wait') AND "
                + events,
                event_params,
            ),
            "proposal_jobs": count(
                "SELECT COUNT(*) FROM proposal_jobs j JOIN books b ON b.id=j.book_id AND b.firm_id=j.firm_id AND b.company_id=j.company_id WHERE b.company_id=? AND j.status IN ('waiting','queued','running','retry_wait') AND "
                + book_sql,
                [company_id, *book_values],
            ),
            "cases": count(
                "SELECT COUNT(*) FROM case_tasks t JOIN firm_clients fc ON fc.firm_id=t.firm_id AND fc.company_id=t.company_id WHERE fc.status='active' AND t.firm_id=? AND t.company_id=? AND t.state<>'closed' AND "
                + case_sql,
                [actor.firm_id, company_id, *case_values],
            ),
        }

    def update_company(self, actor, company_id, request):
        with self.db.transaction(write=True, actor=actor) as conn:
            before = self.require_admin_company(conn, actor, company_id)
            if before["version"] != request.expected_version:
                raise AccountingError("企业资料已更新，请刷新后重试", 409)
            if (
                request.tax_id
                and conn.execute(
                    "SELECT id FROM companies WHERE tax_id=? AND id<>?",
                    (request.tax_id, company_id),
                ).fetchone()
            ):
                raise AccountingError("该税号已存在，请核对企业资料", 409)
            result = conn.execute(
                "UPDATE companies SET name=?,tax_id=?,version=version+1 WHERE id=? AND version=?",
                (request.name, request.tax_id, company_id, request.expected_version),
            )
            if result.rowcount != 1:
                raise AccountingError("企业资料已更新，请刷新后重试", 409)
            after = {
                **before,
                "name": request.name,
                "tax_id": request.tax_id,
                "version": before["version"] + 1,
            }
            DirectoryService._audit(
                conn,
                actor,
                "company.update",
                company_id,
                {
                    "company_id": company_id,
                    "before": before,
                    "after": after,
                    "reason": request.reason,
                },
            )
            return self._company(
                conn, actor, after, self.access.firm_role(conn, actor) in FIRM_MANAGERS
            )

    def change_status(self, actor, company_id, request):
        with self.db.transaction(write=True, actor=actor) as conn:
            self.access.require_manager(conn, actor)
            before = self.require_admin_company(conn, actor, company_id)
            if before["version"] != request.expected_version:
                raise AccountingError("企业状态已更新，请刷新后重试", 409)
            if before["status"] == request.status:
                raise AccountingError("企业已处于该状态，无需重复操作", 409)
            result = conn.execute(
                "UPDATE companies SET status=?,version=version+1 WHERE id=? AND version=?",
                (request.status, company_id, request.expected_version),
            )
            if result.rowcount != 1:
                raise AccountingError("企业状态已更新，请刷新后重试", 409)
            after = {**before, "status": request.status, "version": before["version"] + 1}
            if request.status == "active":
                from .ai_defaults import ensure_company_ai_default

                ensure_company_ai_default(conn, actor, company_id, source="company_reactivated")
            DirectoryService._audit(
                conn,
                actor,
                "company.status",
                company_id,
                {
                    "company_id": company_id,
                    "before": before,
                    "after": after,
                    "reason": request.reason,
                },
            )
            return self._company(conn, actor, after, True)

    def audit(self, actor, *, company_id=None, action=None, limit=50, offset=0):
        with self.db.transaction(actor=actor) as conn:
            manager = self.require_admin(conn, actor)
            if company_id:
                self.require_admin_company(conn, actor, company_id)
            conditions = [
                "a.firm_id=?",
                "(a.action LIKE ? OR a.action LIKE ? OR a.action LIKE ? OR a.action LIKE ?)",
            ]
            params = [actor.firm_id, "company.%", "firm.member.%", "book.member.%", "admin.%"]
            if not manager:
                conditions.append(
                    "EXISTS(SELECT 1 FROM company_memberships cm JOIN firm_clients fc ON fc.company_id=cm.company_id WHERE cm.company_id=a.company_id AND cm.user_id=? AND cm.role IN ('owner','admin') AND fc.firm_id=a.firm_id AND fc.status='active')"
                )
                params.append(actor.user_id)
            if company_id:
                conditions.append("a.company_id=?")
                params.append(company_id)
            if action:
                conditions.append("a.action=?")
                params.append(action)
            base = "FROM audit_events a WHERE " + " AND ".join(conditions)
            total = conn.execute("SELECT COUNT(*) " + base, params).fetchone()[0]
            rows = conn.execute(
                "SELECT a.*,(SELECT u.name FROM users u WHERE u.id=a.user_id) AS user_name "
                + base
                + " ORDER BY a.id DESC LIMIT ? OFFSET ?",
                [*params, limit, offset],
            ).fetchall()
            events = [{**dict(row), "details": json.loads(row["details"])} for row in rows]
            return {"events": events, "total": total, "limit": limit, "offset": offset}
