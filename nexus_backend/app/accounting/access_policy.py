"""Explicit job/scope/time grants. Templates never promote firm membership."""

from datetime import datetime, timezone
import json
from typing import Literal
from uuid import uuid4

from pydantic import Field, StrictStr, field_validator, model_validator

from .access import AccessControl
from .domain import AccountingError
from .invariants import validate_period
from .models import InputModel
from .service import now

TEMPLATES = {
    "assistant": {
        "label": "会计助理",
        "book_role": "editor",
        "firm_roles": {"operator", "reviewer"},
    },
    "accountant": {
        "label": "主办会计",
        "book_role": "editor",
        "firm_roles": {"operator", "reviewer"},
    },
    "reviewer": {"label": "独立复核", "book_role": "reviewer", "firm_roles": {"reviewer"}},
    "auditor": {
        "label": "质控只读",
        "book_role": "viewer",
        "firm_roles": {"viewer", "operator", "reviewer"},
    },
}


class StaffAssignment(InputModel):
    user_id: StrictStr = Field(min_length=1, max_length=100)
    template: Literal["assistant", "accountant", "reviewer", "auditor"]
    scope: Literal["company", "firm"] = "company"
    company_id: StrictStr | None = Field(default=None, min_length=1, max_length=100)
    valid_from: StrictStr | None = None
    valid_to: StrictStr | None = None
    reason: StrictStr = Field(min_length=3, max_length=1000)

    @field_validator("valid_from", "valid_to")
    @classmethod
    def timestamp(cls, value):
        if value is None:
            return None
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("授权时间须包含时区")
        return parsed.astimezone(timezone.utc).isoformat()

    @model_validator(mode="after")
    def ordered(self):
        if (self.scope == "company" and not self.company_id) or (
            self.scope == "firm" and self.company_id is not None
        ):
            raise ValueError("请明确选择指定企业或全事务所范围")
        if self.valid_from and self.valid_to and self.valid_from >= self.valid_to:
            raise ValueError("授权截止时间必须晚于开始时间")
        if len(self.reason.strip()) < 3:
            raise ValueError("请填写授权理由")
        return self


class AccessReason(InputModel):
    reason: StrictStr = Field(min_length=3, max_length=1000)


class ContactAccess(InputModel):
    user_id: StrictStr = Field(min_length=1, max_length=100)
    valid_to: StrictStr | None = None
    reason: StrictStr = Field(min_length=3, max_length=1000)

    _timestamp = field_validator("valid_to")(StaffAssignment.timestamp.__func__)


class AccessPolicyService:
    def __init__(self, db):
        self.db, self.access = db, AccessControl()

    def _audit(self, conn, actor, action, identifier, details):
        conn.execute(
            "INSERT INTO audit_events(firm_id,user_id,action,object_id,details,created_at) VALUES(?,?,?,?,?,?)",
            (
                actor.firm_id,
                actor.user_id,
                action,
                identifier,
                json.dumps(details, ensure_ascii=False),
                now(),
            ),
        )

    def overview(self, actor):
        with self.db.transaction(actor=actor) as conn:
            self.access.require_manager(conn, actor)
            rows = conn.execute(
                "SELECT g.*,u.name FROM staff_access_assignments g JOIN users u ON u.id=g.user_id WHERE g.firm_id=? ORDER BY g.created_at DESC,g.id",
                (actor.firm_id,),
            ).fetchall()
            return {
                "assignments": [dict(row) for row in rows],
                "templates": [
                    {"id": key, "label": value["label"]} for key, value in TEMPLATES.items()
                ],
            }

    def assign(self, actor, request: StaffAssignment):
        with self.db.transaction(write=True, actor=actor) as conn:
            self.access.require_manager(conn, actor)
            self.access.require_user(conn, request.user_id)
            role = conn.execute(
                "SELECT role FROM memberships WHERE firm_id=? AND user_id=?",
                (actor.firm_id, request.user_id),
            ).fetchone()
            if not role or role["role"] not in TEMPLATES[request.template]["firm_roles"]:
                raise AccountingError("岗位模板不能提升事务所身份，请先核对成员职责", 403)
            if request.company_id:
                self.access.company(conn, actor, request.company_id)
            existing = conn.execute(
                "SELECT * FROM staff_access_assignments WHERE firm_id=? AND user_id=? AND template=? AND COALESCE(company_id,'')=COALESCE(?,'') AND COALESCE(valid_from,'')=COALESCE(?,'') AND COALESCE(valid_to,'')=COALESCE(?,'') AND state='active'",
                (
                    actor.firm_id,
                    request.user_id,
                    request.template,
                    request.company_id,
                    request.valid_from,
                    request.valid_to,
                ),
            ).fetchone()
            if existing:
                return dict(existing)
            identifier = str(uuid4())
            conn.execute(
                "INSERT INTO staff_access_assignments(id,firm_id,user_id,template,company_id,valid_from,valid_to,state,reason,created_by,created_at) VALUES(?,?,?,?,?,?,?,'active',?,?,?)",
                (
                    identifier,
                    actor.firm_id,
                    request.user_id,
                    request.template,
                    request.company_id,
                    request.valid_from,
                    request.valid_to,
                    request.reason.strip(),
                    actor.user_id,
                    now(),
                ),
            )
            self._audit(conn, actor, "access.assignment.create", identifier, request.model_dump())
            return {"id": identifier, "state": "active", **request.model_dump()}

    def revoke(self, actor, identifier, reason):
        if len(reason.strip()) < 3:
            raise AccountingError("请填写撤销理由", 422)
        with self.db.transaction(write=True, actor=actor) as conn:
            self.access.require_manager(conn, actor)
            row = conn.execute(
                "SELECT * FROM staff_access_assignments WHERE id=? AND firm_id=?",
                (identifier, actor.firm_id),
            ).fetchone()
            if not row:
                raise AccountingError("授权不存在或无权访问", 404)
            conn.execute(
                "UPDATE staff_access_assignments SET state='revoked' WHERE id=? AND firm_id=?",
                (identifier, actor.firm_id),
            )
            self._audit(
                conn,
                actor,
                "access.assignment.revoke",
                identifier,
                {"reason": reason.strip(), "user_id": row["user_id"]},
            )
            return {"id": identifier, "state": "revoked"}

    def department_expenses(self, actor, company_id, period, department_id):
        validate_period(period)
        with self.db.transaction(actor=actor) as conn:
            self.access.require_user(conn, actor.user_id)
            if self.db.is_postgres:
                raw = conn.execute(
                    "SELECT accounting.department_expense_summary(?,?,?)",
                    (company_id, department_id, period),
                ).fetchone()[0]
                if raw is None:
                    raise AccountingError("部门不存在或无权查看", 404)
                return json.loads(raw)
            allowed = conn.execute(
                "SELECT d.id,d.name FROM departments d JOIN firm_clients fc ON fc.company_id=d.company_id AND fc.firm_id=? AND fc.status='active' JOIN companies c ON c.id=d.company_id AND c.status='active' JOIN department_approvers a ON a.department_id=d.id AND a.company_id=d.company_id AND a.firm_id=fc.firm_id AND a.user_id=? JOIN company_memberships cm ON cm.company_id=d.company_id AND cm.user_id=a.user_id WHERE d.company_id=? AND d.id=?",
                (actor.firm_id, actor.user_id, company_id, department_id),
            ).fetchone()
            if not allowed:
                raise AccountingError("部门不存在或无权查看", 404)
            rows = conn.execute(
                "SELECT l.debit_cents,l.credit_cents,l.aux_json FROM voucher_lines l JOIN vouchers v ON v.id=l.voucher_id JOIN books b ON b.id=v.book_id JOIN ledger_account_versions av ON av.account_id=l.account_id AND av.version=l.account_version WHERE b.firm_id=? AND b.company_id=? AND v.period=? AND av.category='expense'",
                (actor.firm_id, company_id, period),
            ).fetchall()
            total = sum(
                row["debit_cents"] - row["credit_cents"]
                for row in rows
                if json.loads(row["aux_json"]).get("department") == department_id
            )
            return {
                "company_id": company_id,
                "department_id": department_id,
                "department_name": allowed["name"],
                "period": period,
                "expense_cents": total,
                "basis": "正式凭证中明确归属本部门的费用净额；不含未分配费用，不代表企业全部费用",
            }

    def contact_access(self, actor, book_id, request: ContactAccess, revoke=False):
        if len(request.reason.strip()) < 3:
            raise AccountingError("请填写授权或撤销理由", 422)
        with self.db.transaction(write=True, actor=actor) as conn:
            book = self.access.book(conn, actor, book_id, "review")
            if not conn.execute(
                "SELECT 1 FROM company_memberships cm JOIN users u ON u.id=cm.user_id AND u.active=1 WHERE cm.company_id=? AND cm.user_id=?",
                (book["company_id"], request.user_id),
            ).fetchone() and not (
                self.db.is_postgres
                and conn.execute(
                    "SELECT accounting.delivery_recipient_member(?,?)", (book_id, request.user_id)
                ).fetchone()[0]
            ):
                raise AccountingError("签收人必须是当前企业的有效成员", 403)
            conn.execute(
                "INSERT INTO delivery_contact_access(book_id,user_id,firm_id,company_id,valid_to,state,reason,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(book_id,user_id) DO UPDATE SET valid_to=excluded.valid_to,state=excluded.state,reason=excluded.reason,created_by=excluded.created_by,created_at=excluded.created_at",
                (
                    book_id,
                    request.user_id,
                    actor.firm_id,
                    book["company_id"],
                    request.valid_to,
                    "revoked" if revoke else "active",
                    request.reason,
                    actor.user_id,
                    now(),
                ),
            )
            self._audit(
                conn,
                actor,
                "access.contact.revoke" if revoke else "access.contact.grant",
                book_id,
                request.model_dump(),
            )
            return {
                "book_id": book_id,
                "user_id": request.user_id,
                "state": "revoked" if revoke else "active",
            }

    def recipient_packages(self, actor):
        with self.db.transaction(actor=actor) as conn:
            self.access.require_user(conn, actor.user_id)
            if self.db.is_postgres:
                return {
                    "packages": [
                        dict(row)
                        for row in conn.execute(
                            "SELECT * FROM accounting.recipient_package_index()"
                        )
                    ]
                }
            rows = conn.execute(
                "SELECT p.id,p.book_id,p.company_id,p.period,p.version,p.manifest_sha256,c.name AS company_name FROM monthly_delivery_packages p JOIN companies c ON c.id=p.company_id JOIN monthly_delivery_eligible_contacts e ON e.book_id=p.book_id AND e.user_id=p.contact_user_id WHERE p.firm_id=? AND p.contact_user_id=? AND p.created_by<>? ORDER BY p.created_at DESC,p.id LIMIT 100",
                (actor.firm_id, actor.user_id, actor.user_id),
            ).fetchall()
            return {"packages": [dict(row) for row in rows]}
