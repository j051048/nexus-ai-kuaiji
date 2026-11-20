"""OPT07-F: firm-scoped aliases with effective windows."""

from __future__ import annotations

from uuid import uuid4

from pydantic import Field, StrictStr

from .accounting.access import AccessControl
from .accounting.domain import AccountingError
from .accounting.models import InputModel
from .accounting.service import now


class AliasCreate(InputModel):
    subject_type: StrictStr = Field(pattern=r"^(transport_subject|counterparty)$")
    subject_id: StrictStr
    alias: StrictStr = Field(min_length=1, max_length=120)
    source: StrictStr = Field(min_length=1, max_length=200)
    effective_from: StrictStr
    effective_to: StrictStr | None = None


class AliasService:
    def __init__(self, db):
        self.db = db
        self.access = AccessControl()

    @staticmethod
    def _date(value: str, name: str) -> str:
        from datetime import date

        try:
            return date.fromisoformat(value).isoformat()
        except ValueError as error:
            raise AccountingError(f"{name}必须是日期", 422) from error

    def _subject(self, conn, actor, company_id, subject_type, subject_id):
        if subject_type == "transport_subject":
            row = conn.execute(
                "SELECT id FROM transport_subjects WHERE id=? AND firm_id=? AND company_id=?",
                (subject_id, actor.firm_id, company_id),
            ).fetchone()
        elif subject_type == "counterparty":
            row = conn.execute(
                "SELECT id FROM company_master_entities WHERE id=? AND firm_id=? AND company_id=? AND kind='counterparty' AND active=1",
                (subject_id, actor.firm_id, company_id),
            ).fetchone()
        else:
            row = None
        if not row:
            raise AccountingError("别名主体不存在或无权访问", 404)

    def add(self, actor, company_id, request: AliasCreate):
        start = self._date(request.effective_from, "生效日")
        end = self._date(request.effective_to, "失效日") if request.effective_to else None
        if end and end <= start:
            raise AccountingError("失效日必须晚于生效日", 422)
        with self.db.transaction(
            write=True, actor=actor, company_id=company_id, write_scope="company"
        ) as conn:
            self.access.company(conn, actor, company_id, manage=True)
            self._subject(conn, actor, company_id, request.subject_type, request.subject_id)
            rows = conn.execute(
                "SELECT id,subject_id,effective_from,effective_to FROM subject_aliases "
                "WHERE firm_id=? AND company_id=? AND subject_type=? AND alias=? AND status='approved'",
                (actor.firm_id, company_id, request.subject_type, request.alias),
            ).fetchall()
            for row in rows:
                other_end = row["effective_to"] or "9999-12-31"
                if start <= other_end and (end or "9999-12-31") >= row["effective_from"]:
                    if row["subject_id"] == request.subject_id:
                        raise AccountingError("该主体在相同有效期已有此别名", 409)
                    raise AccountingError("该别名在相同有效期已属于其他主体", 409)
            alias_id = uuid4().hex
            stamp = now()
            conn.execute(
                "INSERT INTO subject_aliases(id,firm_id,company_id,subject_type,subject_id,alias,source,status,effective_from,effective_to,created_by,created_at) "
                "VALUES(?,?,?,?,?,?,?,'approved',?,?,?,?)",
                (
                    alias_id,
                    actor.firm_id,
                    company_id,
                    request.subject_type,
                    request.subject_id,
                    request.alias,
                    request.source,
                    start,
                    end,
                    actor.user_id,
                    stamp,
                ),
            )
            conn.execute(
                "INSERT INTO audit_events(firm_id,company_id,user_id,action,object_id,details,created_at) VALUES(?,?,?,?,?,?,?)",
                (
                    actor.firm_id,
                    company_id,
                    actor.user_id,
                    "alias.create",
                    alias_id,
                    '{"subject_type":"%s","subject_id":"%s","alias":"%s"}'
                    % (request.subject_type, request.subject_id, request.alias),
                    stamp,
                ),
            )
            return {"id": alias_id, "status": "approved"}

    def retire(self, actor, company_id, alias_id, reason: str):
        if len(reason.strip()) < 5:
            raise AccountingError("停用原因至少 5 个字", 422)
        with self.db.transaction(
            write=True, actor=actor, company_id=company_id, write_scope="company"
        ) as conn:
            self.access.company(conn, actor, company_id, manage=True)
            row = conn.execute(
                "SELECT id FROM subject_aliases WHERE id=? AND firm_id=? AND company_id=? AND status='approved'",
                (alias_id, actor.firm_id, company_id),
            ).fetchone()
            if not row:
                raise AccountingError("别名不存在或已停用", 404)
            conn.execute(
                "UPDATE subject_aliases SET status='retired',retired_by=?,retired_at=?,retire_reason=? WHERE id=?",
                (actor.user_id, now(), reason.strip(), alias_id),
            )
            conn.execute(
                "INSERT INTO audit_events(firm_id,company_id,user_id,action,object_id,details,created_at) VALUES(?,?,?,?,?,?,?)",
                (
                    actor.firm_id,
                    company_id,
                    actor.user_id,
                    "alias.retire",
                    alias_id,
                    '{"reason":"%s"}' % reason.strip(),
                    now(),
                ),
            )
            return {"id": alias_id, "status": "retired"}

    def list(self, actor, company_id, subject_type=None):
        if subject_type and subject_type not in {"transport_subject", "counterparty"}:
            raise AccountingError("不支持的别名主体类型", 422)
        with self.db.transaction(actor=actor) as conn:
            self.access.company(conn, actor, company_id)
            where = "a.firm_id=? AND a.company_id=?"
            params = [actor.firm_id, company_id]
            if subject_type:
                where += " AND a.subject_type=?"
                params.append(subject_type)
            rows = conn.execute(
                "SELECT a.id,a.subject_type,a.subject_id,a.alias,a.source,a.status,"
                "a.effective_from,a.effective_to,a.created_at,"
                "COALESCE(t.display_name,e.name) AS subject_name "
                "FROM subject_aliases a "
                "LEFT JOIN transport_subjects t ON t.id=a.subject_id AND t.firm_id=a.firm_id AND t.company_id=a.company_id "
                "LEFT JOIN company_master_entities e ON e.id=a.subject_id AND e.firm_id=a.firm_id AND e.company_id=a.company_id AND e.kind='counterparty' "
                f"WHERE {where} ORDER BY a.alias,a.effective_from DESC",
                tuple(params),
            ).fetchall()
        return {"items": [dict(row) for row in rows]}

    def resolve(self, actor, company_id, subject_type, alias, on_date):
        if subject_type not in {"transport_subject", "counterparty"}:
            raise AccountingError("不支持的别名主体类型", 422)
        day = self._date(on_date, "解析日期")
        with self.db.transaction(actor=actor) as conn:
            self.access.company(conn, actor, company_id)
            rows = conn.execute(
                "SELECT subject_id FROM subject_aliases WHERE firm_id=? AND company_id=? AND subject_type=? AND alias=? AND status='approved' "
                "AND effective_from<=? AND (effective_to IS NULL OR effective_to>=?)",
                (actor.firm_id, company_id, subject_type, alias, day, day),
            ).fetchall()
            subjects = sorted({row["subject_id"] for row in rows})
            if subjects and subject_type == "counterparty":
                placeholders = ",".join("?" for _ in subjects)
                active = {
                    row["id"]
                    for row in conn.execute(
                        f"SELECT id FROM company_master_entities WHERE id IN ({placeholders}) AND firm_id=? AND company_id=? AND kind='counterparty' AND active=1",
                        (*subjects, actor.firm_id, company_id),
                    ).fetchall()
                }
                subjects = sorted(active)
        if not subjects:
            return {"status": "not_found", "subject_id": None}
        if len(subjects) > 1:
            return {"status": "ambiguous", "subject_id": None, "candidates": subjects}
        return {"status": "resolved", "subject_id": subjects[0]}
