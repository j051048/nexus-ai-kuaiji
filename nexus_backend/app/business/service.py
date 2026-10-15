"""固定业务审批管道；每次操作实时核对权限，在同一事务中移交会计草稿。"""

import json
from uuid import uuid4

from app.accounting.access import AccessControl, FIRM_MANAGERS
from app.accounting.domain import Actor, AccountingError
from app.accounting.models import money_to_cents
from app.accounting.service import fingerprint, now
from app.accounting.telemetry import tag_authorized_company
from .models import BusinessEventCreate, BusinessEventUpdate, HandoffRequest, ReturnRequest
from .templates import TEMPLATES, validate_payload


EDITABLE = {"draft", "returned"}
STATUSES = {
    "draft",
    "submitted",
    "department_approved",
    "finance_approved",
    "returned",
    "handed_off",
}
EXPENSE_ACCOUNTS = {
    "rent": "6602.01",
    "office": "6602.02",
    "travel": "6602.03",
    "communication": "6602.04",
}


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class BusinessService:
    def __init__(self, db):
        self.db = db
        self.access = AccessControl()
        from .submission import SubmissionPolicyService

        self.submission = SubmissionPolicyService(db)

    def _membership(self, conn, actor, company_id):
        self.access.require_user(conn, actor.user_id)
        return conn.execute(
            "SELECT cm.* FROM company_memberships cm JOIN firm_clients fc ON fc.company_id=cm.company_id "
            "WHERE cm.company_id=? AND cm.user_id=? AND fc.firm_id=? AND fc.status='active'",
            (company_id, actor.user_id, actor.firm_id),
        ).fetchone()

    def _departments(self, conn, company_id):
        return [
            dict(row)
            for row in conn.execute(
                "SELECT id,name FROM departments WHERE company_id=? ORDER BY name,id", (company_id,)
            )
        ]

    def _approver_departments(self, conn, actor, company_id=None):
        sql = (
            "SELECT da.department_id FROM department_approvers da "
            "JOIN company_memberships cm ON cm.company_id=da.company_id AND cm.user_id=da.user_id "
            "JOIN firm_clients fc ON fc.company_id=da.company_id "
            "WHERE da.user_id=? AND fc.firm_id=? AND da.firm_id=? AND fc.status='active'"
        )
        params = [actor.user_id, actor.firm_id, actor.firm_id]
        if company_id:
            sql += " AND da.company_id=?"
            params.append(company_id)
        return [row["department_id"] for row in conn.execute(sql, params)]

    def _ledger_books(self, conn, actor):
        role = self.access.firm_role(conn, actor)
        return [row["id"] for row in self._ledger_book_rows(conn, actor, role)]

    @staticmethod
    def _ledger_book_rows(conn, actor, role):
        sql = """SELECT b.id,b.company_id FROM books b
            JOIN companies c ON c.id=b.company_id AND c.status='active'
            JOIN firm_clients fc ON fc.company_id=b.company_id AND fc.firm_id=b.firm_id AND fc.status='active'
            LEFT JOIN book_access a ON a.book_id=b.id AND a.firm_id=b.firm_id AND a.user_id=?
            LEFT JOIN company_memberships cm ON cm.company_id=b.company_id AND cm.user_id=?
            WHERE b.firm_id=?"""
        values = [actor.user_id, actor.user_id, actor.firm_id]
        if role not in FIRM_MANAGERS:
            sql += " AND a.role IS NOT NULL AND (?=1 OR cm.role IN ('owner','admin','finance'))"
            values.append(1 if role else 0)
        return conn.execute(sql, values).fetchall()

    def context(self, actor: Actor):
        with self.db.transaction(actor=actor) as conn:
            firm_role = self.access.firm_role(conn, actor)
            companies = []
            ledger_rows = self._ledger_book_rows(conn, actor, firm_role)
            ledger_companies = {row["company_id"] for row in ledger_rows}
            visible = []
            for row in conn.execute(
                "SELECT c.id,c.name,cm.role,cm.department_id,p.mode,p.version,p.reason,p.updated_at FROM companies c "
                "JOIN firm_clients fc ON fc.company_id=c.id AND fc.firm_id=? AND fc.status='active' "
                "LEFT JOIN company_memberships cm ON cm.company_id=c.id AND cm.user_id=? "
                "LEFT JOIN business_submission_policies p ON p.company_id=c.id AND p.firm_id=fc.firm_id "
                "WHERE c.status='active' ORDER BY c.name,c.id",
                (actor.firm_id, actor.user_id),
            ):
                if (
                    not row["role"]
                    and firm_role not in FIRM_MANAGERS
                    and row["id"] not in ledger_companies
                ):
                    continue
                visible.append(row)

            # Only fetch details for authorized companies. Ledger-only readers
            # retain their narrower view: no employee department directory.
            department_companies = [
                row["id"] for row in visible if row["role"] or firm_role in FIRM_MANAGERS
            ]
            departments_by_company, approvers_by_company = {}, {}
            if department_companies:
                marks = ",".join("?" for _ in department_companies)
                for department in conn.execute(
                    "SELECT company_id,id,name FROM departments WHERE company_id IN ("
                    + marks
                    + ") ORDER BY name,id",
                    department_companies,
                ):
                    departments_by_company.setdefault(department["company_id"], []).append(
                        {"id": department["id"], "name": department["name"]}
                    )
                for approver in conn.execute(
                    "SELECT da.company_id,da.department_id FROM department_approvers da "
                    "JOIN company_memberships cm ON cm.company_id=da.company_id AND cm.user_id=da.user_id "
                    "WHERE da.firm_id=? AND da.user_id=? AND da.company_id IN (" + marks + ")",
                    [actor.firm_id, actor.user_id, *department_companies],
                ):
                    approvers_by_company.setdefault(approver["company_id"], []).append(
                        approver["department_id"]
                    )
            for row in visible:
                departments = departments_by_company.get(row["id"], [])
                approver_ids = approvers_by_company.get(row["id"], [])
                policy = (
                    {key: row[key] for key in ("mode", "version", "reason", "updated_at")}
                    if row["mode"] is not None
                    else {"mode": "department", "version": 0, "reason": "", "updated_at": None}
                )
                companies.append(
                    {
                        **{key: row[key] for key in ("id", "name", "role", "department_id")},
                        "ledger_available": row["id"] in ledger_companies,
                        "departments": departments,
                        "approver_department_ids": approver_ids,
                        "submission_policy": policy,
                        "can_submit": bool(
                            row["role"] in {"owner", "admin", "finance"}
                            if policy["mode"] == "direct"
                            else row["role"]
                            and (row["department_id"] or row["role"] != "employee" and departments)
                        ),
                        "can_finance": row["role"] == "finance",
                        "can_manage_approvers": firm_role in FIRM_MANAGERS
                        or row["role"] in {"owner", "admin"},
                    }
                )
            return {
                "companies": companies,
                "templates": list(TEMPLATES.values()),
                "scopes": {
                    "own": True,
                    "department": any(row["approver_department_ids"] for row in companies),
                    "company": any(row["can_finance"] for row in companies),
                    "accounting": bool(ledger_rows),
                },
            }

    def _event(self, conn, actor, event_id):
        self.access.require_user(conn, actor.user_id)
        event = conn.execute(
            "SELECT e.* FROM business_events e JOIN firm_clients fc ON fc.firm_id=e.firm_id AND fc.company_id=e.company_id "
            "WHERE e.id=? AND e.firm_id=? AND fc.status='active'",
            (event_id, actor.firm_id),
        ).fetchone()
        if not event or not self._visible(conn, actor, event):
            raise AccountingError("业务事项不存在或无权访问", 404)
        tag_authorized_company(event["company_id"])
        return event

    def _visible(self, conn, actor, event):
        if event["status"] in {"submitted", "returned"} and self.submission.can_receive(
            conn, actor, event
        ):
            return True
        member = self._membership(conn, actor, event["company_id"])
        if member:
            if event["applicant_id"] == actor.user_id or member["role"] == "finance":
                return True
            if event["department_id"] in self._approver_departments(
                conn, actor, event["company_id"]
            ):
                return True
        if event["status"] == "handed_off":
            mapping = conn.execute(
                "SELECT book_id FROM business_mappings WHERE event_id=?", (event["id"],)
            ).fetchone()
            if mapping:
                try:
                    self.access.book(conn, actor, mapping["book_id"])
                    return True
                except AccountingError as error:
                    if error.status not in {403, 404}:
                        raise
        return False

    @staticmethod
    def _version(event, expected_version):
        if type(expected_version) is not int or expected_version < 1:
            raise AccountingError("期望版本必须是大于零的整数")
        if event["version"] != expected_version:
            raise AccountingError("事项版本已更新，请刷新后重试", 409)

    def _permissions(self, conn, actor, event):
        member = self._membership(conn, actor, event["company_id"])
        own = bool(member and event["applicant_id"] == actor.user_id)
        department = bool(
            member
            and not own
            and event["department_id"]
            in self._approver_departments(conn, actor, event["company_id"])
        )
        finance = bool(member and member["role"] == "finance")
        direct = event["submission_mode"] == "direct"
        receiver = direct and self.submission.can_receive(conn, actor, event)
        return {
            "edit": own and event["status"] in EDITABLE,
            "submit": own
            and event["status"] in EDITABLE
            and (
                member["role"] in {"owner", "admin", "finance"}
                if direct
                else bool(event["department_id"])
            ),
            "department_approve": not direct and department and event["status"] == "submitted",
            "finance_approve": not direct
            and finance
            and not own
            and event["status"] == "department_approved",
            "return": (receiver and event["status"] == "submitted")
            or (
                not direct
                and (
                    (department and event["status"] == "submitted")
                    or (finance and not own and event["status"] == "department_approved")
                )
            ),
            "handoff": (receiver and event["status"] == "submitted")
            or (not direct and finance and event["status"] == "finance_approved"),
        }

    def _attachments(self, conn, event_id):
        return [
            dict(row)
            for row in conn.execute(
                "SELECT id,filename,media_type,sha256,size_bytes,created_at,created_by FROM business_attachments WHERE event_id=? ORDER BY created_at,id",
                (event_id,),
            )
        ]

    def _event_json(self, conn, actor, event):
        result = dict(event)
        for private_key in ("create_key", "creation_fingerprint"):
            result.pop(private_key, None)
        labels = conn.execute(
            "SELECT c.name AS company_name,d.name AS department_name FROM companies c "
            "LEFT JOIN departments d ON d.id=? AND d.company_id=c.id WHERE c.id=?",
            (event["department_id"], event["company_id"]),
        ).fetchone()
        result["company_name"] = labels["company_name"] if labels else ""
        result["department_name"] = labels["department_name"] if labels else ""
        applicant = conn.execute(
            "SELECT accounting.business_applicant_name(?,?,?)"
            if self.db.is_postgres
            else "SELECT name FROM users WHERE id=?",
            (event["id"], actor.firm_id, event["company_id"])
            if self.db.is_postgres
            else (event["applicant_id"],),
        ).fetchone()
        result["applicant_name"] = applicant[0] if applicant else ""
        result["payload"] = json.loads(event["payload"])
        result["currency"] = "CNY"
        result["attachments"] = self._attachments(conn, event["id"])
        from ..intelligence.current_evidence import superseded_ids

        excluded = superseded_ids(self.db, conn, event)
        for attachment in result["attachments"]:
            attachment["superseded"] = attachment["id"] in excluded
        result["actions"] = [
            dict(row)
            for row in conn.execute(
                "SELECT id,submitted_version,stage,decision,reason,created_by,created_at FROM business_actions WHERE event_id=? ORDER BY created_at,id",
                (event["id"],),
            )
        ]
        result["versions"] = []
        for row in conn.execute(
            "SELECT event_version,payload,template_snapshot,attachment_snapshot,created_at,created_by FROM business_versions WHERE event_id=? ORDER BY event_version",
            (event["id"],),
        ):
            version = dict(row)
            for key in ("payload", "template_snapshot", "attachment_snapshot"):
                version[key] = json.loads(version[key])
            result["versions"].append(version)
        mapping = conn.execute(
            "SELECT book_id,source_id,draft_id,submitted_version,created_at FROM business_mappings WHERE event_id=?",
            (event["id"],),
        ).fetchone()
        result["mapping"] = None
        if mapping:
            member = self._membership(conn, actor, event["company_id"])
            ledger_access = bool(member and member["role"] == "finance")
            if not ledger_access:
                try:
                    self.access.book(conn, actor, mapping["book_id"])
                    ledger_access = True
                except AccountingError as error:
                    if error.status not in {403, 404}:
                        raise
            if ledger_access:
                result["mapping"] = dict(mapping)
        result["permissions"] = self._permissions(conn, actor, event)
        if getattr(self, "intelligence", None):
            result["permissions"]["process_intelligence"] = self.intelligence._permissions(
                conn, actor, event
            )["process"]
        return result

    def get_event(self, actor, event_id):
        with self.db.transaction(actor=actor) as conn:
            return self._event_json(conn, actor, self._event(conn, actor, event_id))

    def list_events(
        self, actor, scope="own", company_id=None, status=None, limit=50, offset=0, period=None
    ):
        if scope not in {"own", "department", "company", "accounting"}:
            raise AccountingError("业务查看范围不存在")
        if type(limit) is not int or not 1 <= limit <= 100 or type(offset) is not int or offset < 0:
            raise AccountingError("分页参数无效")
        if status is not None and status not in STATUSES:
            raise AccountingError("业务事项状态无效")
        if period is not None:
            import re

            if not isinstance(period, str) or not re.fullmatch(
                r"20[0-9]{2}-(?:0[1-9]|1[0-2])", period
            ):
                raise AccountingError("期间格式应为 YYYY-MM")
        with self.db.transaction(actor=actor) as conn:
            self.access.require_user(conn, actor.user_id)
            parts = ["e.firm_id=?", "fc.status='active'"]
            params = [actor.firm_id]
            if company_id:
                parts.append("e.company_id=?")
                params.append(company_id)
            if status:
                parts.append("e.status=?")
                params.append(status)
            if period:
                parts.append(self.submission.period_sql() + "=?")
                params.append(period)
            if scope == "own":
                parts.append(
                    "e.applicant_id=? AND EXISTS(SELECT 1 FROM company_memberships cm WHERE cm.company_id=e.company_id AND cm.user_id=?)"
                )
                params.extend([actor.user_id, actor.user_id])
            elif scope == "department":
                departments = self._approver_departments(conn, actor, company_id)
                if not departments:
                    raise AccountingError("没有已授权部门的查看权限", 403)
                parts.append("e.department_id IN (" + ",".join("?" for _ in departments) + ")")
                params.extend(departments)
            elif scope == "company":
                companies = [
                    row["company_id"]
                    for row in conn.execute(
                        "SELECT cm.company_id FROM company_memberships cm JOIN firm_clients fc ON fc.company_id=cm.company_id "
                        "WHERE cm.user_id=? AND cm.role='finance' AND fc.firm_id=? AND fc.status='active'",
                        (actor.user_id, actor.firm_id),
                    )
                ]
                if company_id:
                    companies = [item for item in companies if item == company_id]
                if not companies:
                    raise AccountingError("需要企业财务的企业范围查看权限", 403)
                parts.append("e.company_id IN (" + ",".join("?" for _ in companies) + ")")
                params.extend(companies)
            else:
                books = self._ledger_books(conn, actor)
                if not books:
                    raise AccountingError("没有授权账套的会计资料查看权限", 403)
                receive_books = [
                    item
                    for row in conn.execute(
                        "SELECT DISTINCT company_id FROM books WHERE firm_id=?", (actor.firm_id,)
                    )
                    for item in self.submission.direct_books(conn, actor, row["company_id"])
                ]
                mapped = (
                    "e.status='handed_off' AND EXISTS(SELECT 1 FROM business_mappings bm WHERE bm.event_id=e.id AND bm.book_id IN ("
                    + ",".join("?" for _ in books)
                    + "))"
                )
                direct = (
                    "e.submission_mode='direct' AND e.status IN ('submitted','returned') AND e.applicant_id<>? AND EXISTS(SELECT 1 FROM books rb WHERE rb.company_id=e.company_id AND rb.period="
                    + self.submission.period_sql()
                    + " AND rb.id IN ("
                    + ",".join("?" for _ in receive_books)
                    + "))"
                    if receive_books
                    else "0=1"
                )
                parts.append("((" + mapped + ") OR (" + direct + "))")
                params.extend(books)
                if receive_books:
                    params.extend([actor.user_id, *receive_books])
            base = (
                " FROM business_events e JOIN firm_clients fc ON fc.firm_id=e.firm_id AND fc.company_id=e.company_id WHERE "
                + " AND ".join(parts)
            )
            total = conn.execute("SELECT COUNT(*)" + base, params).fetchone()[0]
            events = conn.execute(
                "SELECT e.*" + base + " ORDER BY e.updated_at DESC,e.id LIMIT ? OFFSET ?",
                [*params, limit, offset],
            ).fetchall()
            return {
                "events": [self._event_json(conn, actor, row) for row in events],
                "total": total,
            }

    @staticmethod
    def _audit(conn, actor, action, object_id, details, book_id=None):
        conn.execute(
            "INSERT INTO audit_events(firm_id,book_id,user_id,action,object_id,details,created_at) VALUES(?,?,?,?,?,?,?)",
            (actor.firm_id, book_id, actor.user_id, action, object_id, encode(details), now()),
        )

    def _require_edit(self, conn, actor, event, expected_version):
        self._version(event, expected_version)
        if not self._permissions(conn, actor, event)["edit"]:
            raise AccountingError("仅申请人可修改草稿或退回事项", 403)

    def _bump(
        self,
        conn,
        actor,
        event,
        status=None,
        submitted_version=None,
        facts_changed=False,
        payload=None,
    ):
        version = event["version"] + 1
        conn.execute(
            "UPDATE business_events SET version=?,fact_version=fact_version+?,payload=COALESCE(?,payload),status=?,submitted_version=?,updated_at=? WHERE id=? AND firm_id=? AND version=?",
            (
                version,
                int(facts_changed),
                encode(payload) if payload is not None else None,
                status or event["status"],
                submitted_version if submitted_version is not None else event["submitted_version"],
                now(),
                event["id"],
                actor.firm_id,
                event["version"],
            ),
        )
        if getattr(self, "intelligence", None):
            self.intelligence.capture_snapshot(conn, actor, self._event(conn, actor, event["id"]))
        return version

    def create(self, actor, request: BusinessEventCreate, conn=None):
        payload = request.payload.model_dump()
        validate_payload(request.template_id, request.template_version, payload)
        digest = (
            fingerprint(request.model_dump(exclude={"idempotency_key"}))
            if request.idempotency_key
            else None
        )
        if conn is None:
            with self.db.transaction(write=True, actor=actor) as transaction:
                return self.create(actor, request, transaction)
        member = self._membership(conn, actor, request.company_id)
        if not member:
            raise AccountingError("需要当前企业的成员授权才能录入业务事项", 403)
        mode = self.submission.current(conn, actor.firm_id, request.company_id)["mode"]
        if mode == "direct" and member["role"] not in {"owner", "admin", "finance"}:
            raise AccountingError("直接提交通道仅供企业老板、管理员或出纳财务使用", 403)
        if request.idempotency_key:
            existing = conn.execute(
                "SELECT * FROM business_events WHERE firm_id=? AND applicant_id=? AND create_key=?",
                (actor.firm_id, actor.user_id, request.idempotency_key),
            ).fetchone()
            if existing:
                if existing["creation_fingerprint"] != digest:
                    raise AccountingError("创建幂等键已使用且原始事项内容不同", 409)
                return self._event_json(conn, actor, self._event(conn, actor, existing["id"]))
        department_id = request.department_id or member["department_id"]
        if member["role"] == "employee" and department_id != member["department_id"]:
            raise AccountingError("员工只能在本人所属部门录入事项", 403)
        if (
            department_id
            and not conn.execute(
                "SELECT 1 FROM departments WHERE id=? AND company_id=?",
                (department_id, request.company_id),
            ).fetchone()
        ):
            raise AccountingError("部门不属于当前企业")
        event_id, timestamp = uuid4().hex, now()
        conn.execute(
            "INSERT INTO business_events(id,firm_id,company_id,department_id,applicant_id,template_id,template_version,payload,status,version,created_at,updated_at,create_key,creation_fingerprint,submission_mode) VALUES(?,?,?,?,?,?,?,?,'draft',1,?,?,?,?,?)",
            (
                event_id,
                actor.firm_id,
                request.company_id,
                department_id,
                actor.user_id,
                request.template_id,
                request.template_version,
                encode(payload),
                timestamp,
                timestamp,
                request.idempotency_key,
                digest,
                mode,
            ),
        )
        self._audit(
            conn,
            actor,
            "business.create",
            event_id,
            {"template_id": request.template_id, "company_id": request.company_id},
        )
        if getattr(self, "intelligence", None):
            self.intelligence.capture_snapshot(conn, actor, self._event(conn, actor, event_id))
        return self._event_json(conn, actor, self._event(conn, actor, event_id))

    def update(self, actor, event_id, request: BusinessEventUpdate):
        with self.db.transaction(write=True, actor=actor) as conn:
            event = self._event(conn, actor, event_id)
            self._require_edit(conn, actor, event, request.expected_version)
            payload = request.payload.model_dump()
            validate_payload(event["template_id"], event["template_version"], payload)
            self._bump(
                conn,
                actor,
                event,
                facts_changed=json.loads(event["payload"]) != payload,
                payload=payload,
            )
            self._audit(conn, actor, "business.update", event_id, {"version": event["version"] + 1})
            return self._event_json(conn, actor, self._event(conn, actor, event_id))

    def require_attachment_upload(self, actor, event_id, expected_version):
        with self.db.transaction(actor=actor) as conn:
            self._require_edit(conn, actor, self._event(conn, actor, event_id), expected_version)

    def add_attachment(self, actor, event_id, expected_version, metadata, conn=None):
        if conn is None:
            with self.db.transaction(write=True, actor=actor) as transaction:
                return self.add_attachment(actor, event_id, expected_version, metadata, transaction)
        event = self._event(conn, actor, event_id)
        self._require_edit(conn, actor, event, expected_version)
        attachments = self._attachments(conn, event_id)
        if (
            len(attachments) >= 10
            or sum(row["size_bytes"] for row in attachments) + metadata["size_bytes"]
            > 50 * 1024 * 1024
        ):
            raise AccountingError("每个事项最多 10 个附件，合计不超过 50 MB")
        if any(row["sha256"] == metadata["sha256"] for row in attachments):
            raise AccountingError("该事项已有相同内容的附件", 409)
        conn.execute(
            "INSERT INTO business_attachments(id,event_id,firm_id,company_id,storage_key,filename,media_type,sha256,size_bytes,created_at,created_by) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (
                metadata["id"],
                event_id,
                actor.firm_id,
                event["company_id"],
                metadata["storage_key"],
                metadata["filename"],
                metadata["media_type"],
                metadata["sha256"],
                metadata["size_bytes"],
                now(),
                actor.user_id,
            ),
        )
        self._bump(conn, actor, event, facts_changed=True)
        self._audit(
            conn,
            actor,
            "business.attachment.add",
            event_id,
            {"attachment_id": metadata["id"], "sha256": metadata["sha256"]},
        )
        result = self._event_json(conn, actor, self._event(conn, actor, event_id))
        from .storage import retain_original

        retain_original(metadata)
        return result

    def download_metadata(self, actor, attachment_id):
        with self.db.transaction(actor=actor) as conn:
            attachment = conn.execute(
                "SELECT * FROM business_attachments WHERE id=? AND firm_id=?",
                (attachment_id, actor.firm_id),
            ).fetchone()
            if not attachment:
                raise AccountingError("附件不存在或无权访问", 404)
            self._event(conn, actor, attachment["event_id"])
            return dict(attachment)

    def submit(self, actor, event_id, expected_version, conn=None):
        if conn is None:
            with self.db.transaction(write=True, actor=actor) as transaction:
                return self.submit(actor, event_id, expected_version, transaction)
        event = self._event(conn, actor, event_id)
        self._require_edit(conn, actor, event, expected_version)
        direct = event["submission_mode"] == "direct"
        if not direct and not event["department_id"]:
            raise AccountingError("请先由管理员设置所属部门，再重新创建并提交事项")
        member = self._membership(conn, actor, event["company_id"])
        if direct and member["role"] not in {"owner", "admin", "finance"}:
            raise AccountingError("直接提交通道需要当前企业老板、管理员或出纳财务授权", 403)
        if member["role"] == "employee" and member["department_id"] != event["department_id"]:
            raise AccountingError("本人所属部门已变更，请按当前部门重新创建事项", 403)
        attachments = self._attachments(conn, event_id)
        if not attachments:
            raise AccountingError("提交前至少上传一个真实附件")
        if (
            not direct
            and self.db.is_postgres
            and not conn.execute(
                "SELECT accounting.business_submission_ready(?,?,?,?)",
                (actor.firm_id, event["company_id"], event["department_id"], actor.user_id),
            ).fetchone()[0]
        ):
            raise AccountingError("请先配置非申请人的有效部门审批人与企业财务审批人")
        for sql, text in (
            ()
            if self.db.is_postgres or direct
            else (
                (
                    "SELECT 1 FROM department_approvers da JOIN company_memberships cm ON cm.company_id=da.company_id AND cm.user_id=da.user_id JOIN users u ON u.id=da.user_id WHERE da.firm_id=? AND da.company_id=? AND da.department_id=? AND da.user_id<>? AND u.active=1",
                    "请先配置一位非申请人的有效部门审批人",
                ),
                (
                    "SELECT 1 FROM company_memberships cm JOIN users u ON u.id=cm.user_id WHERE cm.company_id=? AND cm.role='finance' AND cm.user_id<>? AND u.active=1",
                    "请先配置一位非申请人的有效企业财务审批人",
                ),
            )
        ):
            params = (
                (actor.firm_id, event["company_id"], event["department_id"], actor.user_id)
                if "department_approvers" in sql
                else (event["company_id"], actor.user_id)
            )
            if not conn.execute(sql, params).fetchone():
                raise AccountingError(text)
        payload = json.loads(event["payload"])
        template = validate_payload(event["template_id"], event["template_version"], payload)
        new_version = event["version"] + 1
        conn.execute(
            "INSERT INTO business_versions(event_id,firm_id,company_id,event_version,payload,template_snapshot,attachment_snapshot,created_at,created_by) VALUES(?,?,?,?,?,?,?,?,?)",
            (
                event_id,
                actor.firm_id,
                event["company_id"],
                new_version,
                event["payload"],
                encode(template),
                encode(attachments),
                now(),
                actor.user_id,
            ),
        )
        self._bump(conn, actor, event, "submitted", new_version)
        self._audit(
            conn,
            actor,
            "business.submit",
            event_id,
            {"submitted_version": new_version, "attachment_count": len(attachments)},
        )
        return self._event_json(conn, actor, self._event(conn, actor, event_id))

    def _action(self, conn, actor, event, stage, decision, reason=""):
        conn.execute(
            "INSERT INTO business_actions(id,event_id,firm_id,company_id,submitted_version,stage,decision,reason,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                uuid4().hex,
                event["id"],
                actor.firm_id,
                event["company_id"],
                event["submitted_version"],
                stage,
                decision,
                reason,
                actor.user_id,
                now(),
            ),
        )

    def _approve(self, actor, event_id, expected_version, stage):
        with self.db.transaction(write=True, actor=actor) as conn:
            event = self._event(conn, actor, event_id)
            self._version(event, expected_version)
            capability = "department_approve" if stage == "department" else "finance_approve"
            if not self._permissions(conn, actor, event)[capability]:
                raise AccountingError("没有当前审批阶段权限，申请人不可审批本人事项", 403)
            self._bump(
                conn,
                actor,
                event,
                "department_approved" if stage == "department" else "finance_approved",
            )
            self._action(conn, actor, event, stage, "approve")
            self._audit(
                conn,
                actor,
                "business." + stage + ".approve",
                event_id,
                {"submitted_version": event["submitted_version"]},
            )
            return self._event_json(conn, actor, self._event(conn, actor, event_id))

    def approve_department(self, actor, event_id, expected_version):
        return self._approve(actor, event_id, expected_version, "department")

    def approve_finance(self, actor, event_id, expected_version):
        return self._approve(actor, event_id, expected_version, "finance")

    def return_event(self, actor, event_id, request: ReturnRequest):
        with self.db.transaction(write=True, actor=actor) as conn:
            event = self._event(conn, actor, event_id)
            self._version(event, request.expected_version)
            if not self._permissions(conn, actor, event)["return"]:
                raise AccountingError("只有当前阶段审批人可退回，申请人不可退回本人事项", 403)
            self._bump(conn, actor, event, "returned")
            self._action(conn, actor, event, "return", "return", request.reason)
            self._audit(
                conn,
                actor,
                "business.return",
                event_id,
                {
                    "reason": request.reason,
                    "from_stage": event["status"],
                    "submitted_version": event["submitted_version"],
                },
            )
            return self._event_json(conn, actor, self._event(conn, actor, event_id))

    def _manage_department(self, conn, actor, company_id, department_id):
        self.access.company(conn, actor, company_id, manage=True)
        if not conn.execute(
            "SELECT 1 FROM departments WHERE id=? AND company_id=?", (department_id, company_id)
        ).fetchone():
            raise AccountingError("部门不存在或不属于当前企业", 404)

    def list_department_approvers(self, actor, company_id, department_id):
        with self.db.transaction(actor=actor) as conn:
            self._manage_department(conn, actor, company_id, department_id)
            rows = conn.execute(
                "SELECT da.user_id,u.name,u.email FROM department_approvers da JOIN users u ON u.id=da.user_id "
                "WHERE da.firm_id=? AND da.company_id=? AND da.department_id=? ORDER BY u.name,u.id",
                (actor.firm_id, company_id, department_id),
            ).fetchall()
            return {"approvers": [dict(row) for row in rows]}

    def grant_department_approver(self, actor, company_id, department_id, user_id):
        with self.db.transaction(write=True, actor=actor) as conn:
            self._manage_department(conn, actor, company_id, department_id)
            if not conn.execute(
                "SELECT 1 FROM company_memberships cm JOIN users u ON u.id=cm.user_id WHERE cm.company_id=? AND cm.user_id=? AND u.active=1",
                (company_id, user_id),
            ).fetchone():
                raise AccountingError("审批人必须是当前企业的有效成员")
            conn.execute(
                "INSERT INTO department_approvers(firm_id,company_id,department_id,user_id) VALUES(?,?,?,?) ON CONFLICT(firm_id,department_id,user_id) DO NOTHING",
                (actor.firm_id, company_id, department_id, user_id),
            )
            self._audit(
                conn,
                actor,
                "business.approver.grant",
                user_id,
                {"company_id": company_id, "department_id": department_id},
            )
            return {"user_id": user_id, "company_id": company_id, "department_id": department_id}

    def revoke_department_approver(self, actor, company_id, department_id, user_id):
        with self.db.transaction(write=True, actor=actor) as conn:
            self._manage_department(conn, actor, company_id, department_id)
            conn.execute(
                "DELETE FROM department_approvers WHERE firm_id=? AND company_id=? AND department_id=? AND user_id=?",
                (actor.firm_id, company_id, department_id, user_id),
            )
            self._audit(
                conn,
                actor,
                "business.approver.revoke",
                user_id,
                {"company_id": company_id, "department_id": department_id},
            )
            return {"revoked": True}

    @staticmethod
    def _proposal(template_id, payload, amount):
        if payload["direction"] == "income":
            return [("1002", amount, 0), ("6001", 0, amount)]
        clearing = (
            "2241"
            if template_id == "reimbursement"
            else "2202"
            if payload["payment_status"] == "unpaid"
            else "1002"
        )
        lines = [(clearing, 0, amount)]
        if payload["category"] in EXPENSE_ACCOUNTS:
            lines.insert(0, (EXPENSE_ACCOUNTS[payload["category"]], amount, 0))
        return lines

    def handoff(self, actor, event_id, request: HandoffRequest):
        with self.db.transaction(write=True, actor=actor) as conn:
            receipt = self._handoff_in_transaction(conn, actor, event_id, request)
        # History, attachments and presentation permissions need no write lock.
        # Read under a new live authorization check after committing the atomic
        # source/draft/job receipt; a revoked reader must not receive old data.
        with self.db.transaction(actor=actor) as conn:
            self.access.book(conn, actor, receipt["book_id"], "edit")
            event = self._event(conn, actor, event_id)
            return {"event": self._event_json(conn, actor, event), **receipt}

    def _handoff_in_transaction(self, conn, actor, event_id, request):
        event = self._event(conn, actor, event_id)
        member = self._membership(conn, actor, event["company_id"])
        direct = event["submission_mode"] == "direct"
        if direct and not self.submission.can_receive(conn, actor, event):
            raise AccountingError("需要独立的授权会计接收直接提交资料", 403)
        if not direct and (not member or member["role"] != "finance"):
            raise AccountingError("需要企业财务权限才能移交会计", 403)
        book = self.access.book(conn, actor, request.book_id, "edit")
        if book["company_id"] != event["company_id"]:
            raise AccountingError("目标账套不属于事项所在企业", 403)
        digest = fingerprint(
            {
                "event_id": event_id,
                "book_id": request.book_id,
                "submitted_version": event["submitted_version"],
            }
        )
        existing_key = conn.execute(
            "SELECT * FROM business_mappings WHERE firm_id=? AND idempotency_key=?",
            (actor.firm_id, request.idempotency_key),
        ).fetchone()
        existing_event = conn.execute(
            "SELECT * FROM business_mappings WHERE event_id=?", (event_id,)
        ).fetchone()
        if existing_key or existing_event:
            mapping = existing_key or existing_event
            if (
                mapping["event_id"] != event_id
                or mapping["request_fingerprint"] != digest
                or mapping["idempotency_key"] != request.idempotency_key
            ):
                raise AccountingError("移交幂等键或已移交的目标内容不一致", 409)
            job = conn.execute(
                "SELECT id FROM proposal_jobs WHERE book_id=? AND draft_id=? ORDER BY created_at,id LIMIT 1",
                (mapping["book_id"], mapping["draft_id"]),
            ).fetchone()
            return {
                "book_id": mapping["book_id"],
                "source_id": mapping["source_id"],
                "draft_id": mapping["draft_id"],
                "automatic_job": {"id": job["id"]} if job else None,
            }
        self._version(event, request.expected_version)
        if event["status"] != ("submitted" if direct else "finance_approved"):
            raise AccountingError("事项必须完成部门与财务审批后才能移交", 409)
        if book["period_status"] != "open":
            raise AccountingError("目标会计期间已关闭，不可移交", 403)
        snapshot = conn.execute(
            "SELECT * FROM business_versions WHERE event_id=? AND event_version=?",
            (event_id, event["submitted_version"]),
        ).fetchone()
        if not snapshot:
            raise AccountingError("提交快照不存在，不能移交", 409)
        payload = json.loads(snapshot["payload"])
        if payload["occurred_on"][:7] != book["period"]:
            raise AccountingError("业务发生日期不属于目标会计期间")
        amount = money_to_cents(payload["amount"])
        source_id, draft_id = uuid4().hex, uuid4().hex
        evidence = f"business:{event_id}:{event['submitted_version']}"
        conn.execute(
            "INSERT INTO sources(id,book_id,external_id,source_name,date,description,counterparty,amount_cents,direction,evidence_ref,fingerprint,source_kind) VALUES(?,?,?,?,?,?,?,?,?,?,?,'business')",
            (
                source_id,
                request.book_id,
                evidence,
                "企业业务事项",
                payload["occurred_on"],
                payload["description"],
                payload["counterparty"],
                amount,
                payload["direction"],
                evidence,
                fingerprint({"payload": payload, "evidence_ref": evidence}),
            ),
        )
        from ..accounting.catalog import CatalogService

        catalog = CatalogService(self.db)
        configured = None
        template_pending = ""
        try:
            configured = catalog.generate_in_transaction(
                conn,
                actor,
                request.book_id,
                event["template_id"],
                payload,
                amount,
                {
                    "department_id": event["department_id"],
                    "applicant_id": event["applicant_id"],
                },
            )
        except AccountingError as error:
            # Tax formulas consume verified invoice amounts after OCR;
            # handoff still preserves the approved business facts now.
            if "拆分税额模板要求" not in str(error):
                raise
            template_pending = (
                "企业拆分税额模板等待票据识别和金额核对；当前保留空草稿，须处理完成后复核。"
            )
        lines = (
            configured["lines"]
            if configured is not None
            else []
            if template_pending
            else self._proposal(event["template_id"], payload, amount)
        )
        available = {
            row["code"]
            for row in conn.execute(
                "SELECT code FROM accounts WHERE book_id=? AND active=1", (request.book_id,)
            )
        }
        # 账套自定义科目缺失时保留可用一侧，让会计补充，不默默建立科目。
        lines = [line for line in lines if line[0] in available]
        explanation = (
            f"来自业务事项 {event_id} 的提交版本 {event['submitted_version']}，"
            + ("经企业授权直接提交，由独立会计接收；" if direct else "已完成部门和企业财务审批；")
            + "按固定模板及费用类别生成候选分录。附件 SHA-256 仅用于追溯提交资料，未验证单据真实性。"
            "未判定税额、收入确认或业务实质，须由会计独立复核。"
        )
        if configured is not None:
            explanation = configured["explanation"] + "；" + "；".join(configured["issues"])
        elif template_pending:
            explanation = template_pending
        conn.execute(
            "INSERT INTO drafts(id,book_id,source_id,description,status,version,explanation) VALUES(?,?,?,?,'draft',1,?)",
            (draft_id, request.book_id, source_id, payload["description"], explanation),
        )
        conn.executemany(
            "INSERT INTO draft_lines(draft_id,book_id,ordinal,account_code,debit_cents,credit_cents) VALUES(?,?,?,?,?,?)",
            [(draft_id, request.book_id, ordinal, *line) for ordinal, line in enumerate(lines, 1)],
        )
        conn.execute(
            "INSERT INTO business_mappings(event_id,firm_id,company_id,book_id,source_id,draft_id,submitted_version,idempotency_key,request_fingerprint,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (
                event_id,
                actor.firm_id,
                event["company_id"],
                request.book_id,
                source_id,
                draft_id,
                event["submitted_version"],
                request.idempotency_key,
                digest,
                actor.user_id,
                now(),
            ),
        )
        if configured is not None:
            catalog.save_auxiliaries_in_transaction(
                conn, actor, request.book_id, draft_id, configured["auxiliaries"]
            )
        self._bump(conn, actor, event, "handed_off")
        self._action(conn, actor, event, "handoff", "handoff")
        self._audit(
            conn,
            actor,
            "business.handoff",
            event_id,
            {
                "book_id": request.book_id,
                "source_id": source_id,
                "draft_id": draft_id,
                "submitted_version": event["submitted_version"],
            },
            request.book_id,
        )
        current_event = self._event(conn, actor, event_id)
        automatic = None
        if getattr(self, "proposal_queue", None) is not None:
            current_draft = conn.execute(
                "SELECT * FROM drafts WHERE id=? AND book_id=?", (draft_id, request.book_id)
            ).fetchone()
            automatic = self.proposal_queue.enqueue_in_transaction(
                conn, actor, current_event, book, current_draft
            )
        # Handoff returns a stable task identity. Live progress belongs to
        # the task endpoint, so a retry returns the same command receipt.
        return {
            "book_id": request.book_id,
            "source_id": source_id,
            "draft_id": draft_id,
            "automatic_job": {"id": automatic["id"]} if automatic else None,
        }
