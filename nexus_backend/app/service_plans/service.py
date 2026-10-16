"""Explicit monthly work plans; material acceptance never creates accounting facts."""

import json
import re
from uuid import uuid4

from ..accounting.domain import AccountingError, Actor
from ..accounting.queries import page_bounds
from ..accounting.service import now


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class ServicePlansService:
    def __init__(self, db, accounting, business):
        self.db, self.accounting, self.business = db, accounting, business
        self.access = accounting.access
        self.attachments = None

    def _book(self, conn, actor, book_id, write=False, review=False):
        book = self.access.book(conn, actor, book_id, "review" if review else "edit")
        if write:
            self.accounting._open(book)
        ledger = conn.execute(
            "SELECT ledger_id FROM ledger_periods WHERE book_id=?", (book_id,)
        ).fetchone()
        if not ledger:
            raise AccountingError("账套尚未关联长期账套", 409)
        return {**book, "ledger_id": ledger["ledger_id"]}

    def _company_of_book(self, actor, book_id):
        """Resolve the book's company up front for a company-scoped write lock."""
        with self.db.transaction(actor=actor) as conn:
            return self._book(conn, actor, book_id)["company_id"]

    def _company_of_request(self, actor, request_id):
        with self.db.transaction(actor=actor) as conn:
            return self._request(conn, actor, request_id)["company_id"]

    def _audit(self, conn, actor, book, action, object_id, details):
        conn.execute(
            "INSERT INTO audit_events(firm_id,book_id,company_id,user_id,action,object_id,details,created_at) VALUES(?,?,?,?,?,?,?,?)",
            (
                actor.firm_id,
                book["id"],
                book["company_id"],
                actor.user_id,
                action,
                object_id,
                encode(details),
                now(),
            ),
        )

    def _candidates(self, conn, actor, book):
        if self.db.is_postgres:
            return [
                dict(row)
                for row in conn.execute(
                    "SELECT * FROM accounting.service_plan_candidates(?)", (book["id"],)
                )
            ]
        rows = conn.execute(
            "SELECT DISTINCT u.id,u.name FROM users u LEFT JOIN memberships m ON m.user_id=u.id AND m.firm_id=? "
            "LEFT JOIN company_memberships cm ON cm.user_id=u.id AND cm.company_id=? "
            "LEFT JOIN book_access ba ON ba.user_id=u.id AND ba.book_id=? "
            "WHERE u.active=1 AND (m.user_id IS NOT NULL OR cm.user_id IS NOT NULL OR ba.user_id IS NOT NULL) ORDER BY u.name,u.id",
            (actor.firm_id, book["company_id"], book["id"]),
        ).fetchall()
        result = []
        for row in rows:
            target = Actor(row["id"], actor.firm_id)
            fm = self.access.firm_role(conn, target)
            cm = self.access.company_role(conn, target, book["company_id"])
            ba = conn.execute(
                "SELECT role FROM book_access WHERE book_id=? AND user_id=?",
                (book["id"], row["id"]),
            ).fetchone()
            p = self.access.permissions(fm, ba["role"] if ba else None, cm)
            if p["edit"] or cm:
                result.append(
                    {
                        "user_id": row["id"],
                        "name": row["name"],
                        "can_lead": p["edit"],
                        "can_review": p["review"],
                        "can_contact": bool(cm),
                    }
                )
        return result

    def _assign(self, conn, actor, book, user_id, capability, candidates=None):
        if user_id is None:
            return
        found = next(
            (
                u
                for u in (
                    candidates if candidates is not None else self._candidates(conn, actor, book)
                )
                if u["user_id"] == user_id
            ),
            None,
        )
        if not found or not found[capability]:
            raise AccountingError("被指派人已失权或不具备当前企业／账套所需权限", 409)

    @staticmethod
    def _plan(conn, book_id):
        row = conn.execute(
            "SELECT * FROM service_plan_versions WHERE book_id=? ORDER BY version DESC LIMIT 1",
            (book_id,),
        ).fetchone()
        if not row:
            return None
        return {
            key: row[key]
            for key in (
                "book_id",
                "version",
                "lead_user_id",
                "reviewer_user_id",
                "backup_user_id",
                "contact_user_id",
                "due_on",
                "notes",
                "updated_at",
                "updated_by",
            )
        }

    @staticmethod
    def _template(row):
        return {
            "id": row["id"],
            "name": row["name"],
            "version": row["version"],
            "ledger_id": row["ledger_id"],
            "items": json.loads(row["items_json"]),
            "created_at": row["created_at"],
        }

    def overview(self, actor, book_id):
        with self.db.transaction(actor=actor) as conn:
            book = self._book(conn, actor, book_id)
            page = self._requests(conn, actor, "r.book_id=?", [book_id], 20, 0)
            return {
                "book": {
                    key: book[key]
                    for key in (
                        "id",
                        "company_id",
                        "company_name",
                        "name",
                        "period",
                        "period_status",
                        "ledger_id",
                    )
                },
                "permissions": {
                    "manage": book["permissions"]["edit"] and book["period_status"] == "open",
                    "review": book["permissions"]["review"] and book["period_status"] == "open",
                },
                "plan": self._plan(conn, book_id),
                "candidates": self._candidates(conn, actor, book),
                "requirements": page["rows"],
                "requirements_pagination": page["pagination"],
                "templates": [
                    self._template(row)
                    for row in conn.execute(
                        "SELECT * FROM material_templates WHERE ledger_id=? ORDER BY created_at,id",
                        (book["ledger_id"],),
                    )
                ],
            }

    def save_plan(self, actor, book_id, request):
        company_id = self._company_of_book(actor, book_id)
        with self.db.transaction(
            write=True, actor=actor, company_id=company_id, write_scope="company"
        ) as conn:
            book = self._book(conn, actor, book_id, write=True)
            prior = self._plan(conn, book_id)
            if (prior["version"] if prior else 0) != request.expected_version:
                raise AccountingError("月度服务单已更新，请刷新后重试", 409)
            candidates = self._candidates(conn, actor, book)
            for field, capability in (
                ("lead_user_id", "can_lead"),
                ("reviewer_user_id", "can_review"),
                ("backup_user_id", "can_lead"),
                ("contact_user_id", "can_contact"),
            ):
                self._assign(conn, actor, book, getattr(request, field), capability, candidates)
            value = request.model_dump(mode="json", exclude={"expected_version", "reason"})
            conn.execute(
                "INSERT INTO service_plan_versions(book_id,firm_id,company_id,version,lead_user_id,reviewer_user_id,backup_user_id,contact_user_id,due_on,notes,reason,updated_by,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    book_id,
                    actor.firm_id,
                    book["company_id"],
                    request.expected_version + 1,
                    request.lead_user_id,
                    request.reviewer_user_id,
                    request.backup_user_id,
                    request.contact_user_id,
                    value["due_on"],
                    request.notes,
                    request.reason,
                    actor.user_id,
                    now(),
                ),
            )
            self._audit(
                conn,
                actor,
                book,
                "service_plan.save",
                book_id,
                {**value, "version": request.expected_version + 1, "reason": request.reason},
            )
        return self.overview(actor, book_id)

    def inherit_plan(self, actor, source_book_id, target_book_id, reason):
        """Copy the latest source plan onto an opened target period.

        The copy re-evaluates current candidates and never grants access:
        fields whose assignee lost the required capability are dropped and
        reported. Idempotent when the target already has a plan version.
        """
        if source_book_id == target_book_id:
            raise AccountingError("继承来源与目标期间必须不同", 409)
        company_id = self._company_of_book(actor, target_book_id)
        with self.db.transaction(
            write=True, actor=actor, company_id=company_id, write_scope="company"
        ) as conn:
            source = self._book(conn, actor, source_book_id)
            target = self._book(conn, actor, target_book_id, write=True)
            if source["company_id"] != target["company_id"]:
                raise AccountingError("继承来源必须与目标期间属于同一企业", 409)
            if source["ledger_id"] != target["ledger_id"]:
                raise AccountingError("继承来源与目标期间必须属于同一长期账套", 409)
            existing = self._plan(conn, target_book_id)
            if existing:
                return {"copied": False, "dropped": [], "reason": "already_planned", "version": existing["version"]}
            prior = self._plan(conn, source_book_id)
            if not prior:
                return {"copied": False, "dropped": [], "reason": "source_without_plan"}
            candidates = {row["user_id"]: row for row in self._candidates(conn, actor, target)}
            dropped, fields = [], {}
            for field, capability in (
                ("lead_user_id", "can_lead"),
                ("reviewer_user_id", "can_review"),
                ("backup_user_id", "can_lead"),
                ("contact_user_id", "can_contact"),
            ):
                value = prior[field]
                if value and (value not in candidates or not candidates[value][capability]):
                    dropped.append(field)
                    value = None
                fields[field] = value
            if fields["lead_user_id"] and fields["lead_user_id"] == fields["reviewer_user_id"]:
                dropped.append("reviewer_user_id")
                fields["reviewer_user_id"] = None
            due_on = self._shifted_due(prior["due_on"], target["period"])
            version = 1
            conn.execute(
                "INSERT INTO service_plan_versions(book_id,firm_id,company_id,version,lead_user_id,reviewer_user_id,backup_user_id,contact_user_id,due_on,notes,reason,updated_by,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    target_book_id,
                    actor.firm_id,
                    target["company_id"],
                    version,
                    fields["lead_user_id"],
                    fields["reviewer_user_id"],
                    fields["backup_user_id"],
                    fields["contact_user_id"],
                    due_on,
                    prior["notes"] or "",
                    reason,
                    actor.user_id,
                    now(),
                ),
            )
            self._audit(
                conn,
                actor,
                target,
                "service_plan.inherit",
                target_book_id,
                {
                    "source_book_id": source_book_id,
                    "version": version,
                    "dropped": dropped,
                    "due_on": due_on,
                    "reason": reason,
                },
            )
            return {"copied": True, "dropped": dropped, "version": version}

    @staticmethod
    def _shifted_due(due_on, period):
        """Carry the source cutoff day into the month after the target period."""
        year, month = map(int, period.split("-"))
        try:
            day = min(int(str(due_on)[8:10]), 28)
        except (TypeError, ValueError):
            day = 10
        month += 1
        return f"{year + (month - 1) // 12:04d}-{(month - 1) % 12 + 1:02d}-{day:02d}"

    def create_template(self, actor, book_id, request):
        company_id = self._company_of_book(actor, book_id)
        with self.db.transaction(
            write=True, actor=actor, company_id=company_id, write_scope="company"
        ) as conn:
            book = self._book(conn, actor, book_id, write=True)
            items = [item.model_dump() for item in request.items]
            candidates = self._candidates(conn, actor, book)
            for item in items:
                self._assign(conn, actor, book, item["assignee_user_id"], "can_contact", candidates)
            template_id = uuid4().hex
            conn.execute(
                "INSERT INTO material_templates(id,book_id,ledger_id,firm_id,company_id,version,name,items_json,reason,created_by,created_at) VALUES(?,?,?,?,?,1,?,?,?,?,?)",
                (
                    template_id,
                    book_id,
                    book["ledger_id"],
                    actor.firm_id,
                    book["company_id"],
                    request.name,
                    encode(items),
                    request.reason,
                    actor.user_id,
                    now(),
                ),
            )
            self._audit(
                conn,
                actor,
                book,
                "material.template_create",
                template_id,
                {"reason": request.reason, "items": len(items)},
            )
            row = conn.execute(
                "SELECT * FROM material_templates WHERE id=?", (template_id,)
            ).fetchone()
            return self._template(row)

    def generate(self, actor, book_id, template_id, request):
        company_id = self._company_of_book(actor, book_id)
        with self.db.transaction(
            write=True, actor=actor, company_id=company_id, write_scope="company"
        ) as conn:
            book = self._book(conn, actor, book_id, write=True)
            template = conn.execute(
                "SELECT * FROM material_templates WHERE id=? AND ledger_id=?",
                (template_id, book["ledger_id"]),
            ).fetchone()
            if not template:
                raise AccountingError("资料模板不存在或不属于当前长期账套", 404)
            if template["version"] != request.expected_version:
                raise AccountingError("资料模板版本已更新", 409)
            created = 0
            candidates = self._candidates(conn, actor, book)
            plan = self._plan(conn, book_id)
            for item in json.loads(template["items_json"]):
                if conn.execute(
                    "SELECT 1 FROM material_requests WHERE book_id=? AND template_id=? AND item_key=?",
                    (book_id, template_id, item["key"]),
                ).fetchone():
                    continue
                # Copying to a new month re-evaluates current membership; never grants access.
                assignee = item["assignee_user_id"] or (plan["contact_user_id"] if plan else None)
                if not assignee:
                    raise AccountingError(
                        "请为资料项目指派企业成员，或先设置本月服务单资料联系人", 409
                    )
                self._assign(conn, actor, book, assignee, "can_contact", candidates)
                year, month = map(int, book["period"].split("-"))
                month += item["due_month_offset"]
                due_on = f"{year + (month - 1) // 12:04d}-{(month - 1) % 12 + 1:02d}-{item['due_day']:02d}"
                conn.execute(
                    "INSERT INTO material_requests(id,book_id,firm_id,company_id,period,template_id,item_key,title,description,required,due_on,assignee_user_id,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        uuid4().hex,
                        book_id,
                        actor.firm_id,
                        book["company_id"],
                        book["period"],
                        template_id,
                        item["key"],
                        item["title"],
                        item["description"],
                        int(item["required"]),
                        due_on,
                        assignee,
                        actor.user_id,
                        now(),
                    ),
                )
                created += 1
            if created:
                self._audit(
                    conn,
                    actor,
                    book,
                    "material.generate",
                    template_id,
                    {"created": created, "reason": request.reason, "period": book["period"]},
                )
            return {"created": created, "reused": len(json.loads(template["items_json"])) - created}

    def _request(self, conn, actor, request_id, *, submit=False, review=False):
        self.access.require_user(conn, actor.user_id)
        row = conn.execute(
            "SELECT r.*,c.name AS company_name FROM material_requests r "
            "JOIN companies c ON c.id=r.company_id AND c.status='active' JOIN firm_clients fc ON fc.firm_id=r.firm_id AND fc.company_id=r.company_id AND fc.status='active' WHERE r.id=? AND r.firm_id=?",
            (request_id, actor.firm_id),
        ).fetchone()
        if not row:
            raise AccountingError("资料请求不存在或无权访问", 404)
        own = row["assignee_user_id"] == actor.user_id and bool(
            self.access.company_role(conn, actor, row["company_id"])
        )
        if submit and not own:
            raise AccountingError("资料请求不存在或无权访问", 404)
        if review or not own:
            self._book(conn, actor, row["book_id"], write=submit or review, review=review)
        elif submit:
            if not self._open_request(conn, row):
                raise AccountingError("该会计期间已关闭，不可提交资料", 403)
        return dict(row)

    def _open_request(self, conn, row):
        if self.db.is_postgres:
            return conn.execute(
                "SELECT accounting.service_material_open(?)", (row["id"],)
            ).fetchone()[0]
        period = conn.execute(
            "SELECT status FROM periods WHERE book_id=? AND period=?",
            (row["book_id"], row["period"]),
        ).fetchone()
        return bool(period and period["status"] == "open")

    @staticmethod
    def _actions(conn, request_id):
        return [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM material_request_actions WHERE request_id=? ORDER BY version",
                (request_id,),
            )
        ]

    def _evidence(self, conn, actor, row, event_ids, *, own=False):
        snapshots = []
        for event_id in event_ids:
            event = self.business._event(conn, actor, event_id)
            if event["company_id"] != row["company_id"] or event["firm_id"] != row["firm_id"]:
                raise AccountingError("资料必须属于当前企业", 404)
            payload = json.loads(event["payload"])
            if payload.get("occurred_on", "")[:7] != row["period"]:
                raise AccountingError("业务事项不属于当前资料月份", 409)
            if event["applicant_id"] != row["assignee_user_id"] or (
                own and event["applicant_id"] != actor.user_id
            ):
                raise AccountingError("只能提交被指派人本人的业务事项", 403)
            if event["status"] == "draft":
                raise AccountingError("请先提交业务事项，再关联资料清单", 409)
            attachments = self.business._attachments(conn, event_id)
            from ..intelligence.current_evidence import current_attachments

            attachments = current_attachments(self.db, conn, event, attachments)
            if not attachments:
                raise AccountingError("关联业务事项尚无原始附件", 409)
            snapshots.append(
                {
                    "event_id": event_id,
                    "event_version": event["submitted_version"] or event["version"],
                    "fact_version": event["fact_version"],
                    "title": payload.get("description", "业务资料"),
                    "attachments": [
                        {key: a[key] for key in ("id", "filename", "sha256")} for a in attachments
                    ],
                }
            )
        return snapshots

    @staticmethod
    def _stamp(evidence):
        # Persist references, versions and content hashes only. Names and business
        # values are always resolved through the existing current event ACL.
        return [
            {
                "event_id": e["event_id"],
                "event_version": e["event_version"],
                "fact_version": e["fact_version"],
                "attachments": [{"id": a["id"], "sha256": a["sha256"]} for a in e["attachments"]],
            }
            for e in evidence
        ]

    def _public(self, conn, actor, row):
        actions = self._actions(conn, row["id"])
        latest = actions[-1] if actions else None
        submission = next((x for x in reversed(actions) if x["action"] == "submit"), None)
        review = next((x for x in reversed(actions) if x["action"] in {"accept", "return"}), None)
        own = row["assignee_user_id"] == actor.user_id and bool(
            self.access.company_role(conn, actor, row["company_id"])
        )
        can_review = False
        try:
            book = self.access.book(conn, actor, row["book_id"], "review")
            can_review = book["period_status"] == "open"
        except AccountingError as error:
            if error.status not in {403, 404}:
                raise
        current = None
        evidence = []
        files = []
        if submission:
            saved = json.loads(submission["evidence_json"])
            try:
                evidence = self._evidence(
                    conn, actor, row, json.loads(submission["event_ids_json"])
                )
                current = self._stamp(evidence) == saved
            except AccountingError:
                current = False
            # Snapshot payloads and filenames follow current business visibility too.
            if current is False:
                evidence = []
            file_ids = json.loads(submission["attachment_ids_json"])
            file_stamps = json.loads(submission["attachment_evidence_json"])
            try:
                visible_files = (
                    self.attachments.list(conn, actor, row, file_ids)
                    if file_ids and self.attachments
                    else []
                )
                by_id = {item["id"]: item for item in visible_files}
                files = [by_id[file_id] for file_id in file_ids if file_id in by_id]
                file_current = [
                    {"id": a["id"], "sha256": a["sha256"]} for a in files
                ] == file_stamps
                current = bool(current and file_current)
                if not file_current:
                    files = []
            except AccountingError:
                current = False
        period_open = self._open_request(conn, row)
        state = latest["state"] if latest else "pending"
        result = {
            key: row[key]
            for key in (
                "id",
                "book_id",
                "company_id",
                "company_name",
                "period",
                "title",
                "description",
                "due_on",
                "assignee_user_id",
            )
        }
        result.update(
            {
                "required": bool(row["required"]),
                "assignee_name": None,
                "state": state,
                "version": latest["version"] if latest else 1,
                "evidence_current": current,
                "latest_submission": {
                    "id": submission["id"],
                    "mode": submission["mode"],
                    "note": submission["reason"],
                    "event_ids": json.loads(submission["event_ids_json"]),
                    "attachment_ids": json.loads(submission["attachment_ids_json"]),
                    "attachments": files,
                    "evidence": evidence,
                    "submitted_by": submission["created_by"],
                    "submitted_at": submission["created_at"],
                }
                if submission
                else None,
                "last_review": {
                    "action": review["action"],
                    "reason": review["reason"],
                    "reviewed_by": review["created_by"],
                    "reviewed_at": review["created_at"],
                }
                if review
                else None,
                "permissions": {
                    "submit": own and period_open and state in {"pending", "returned"},
                    "review": can_review
                    and state in {"submitted", "accepted"}
                    and bool(submission and submission["created_by"] != actor.user_id),
                },
            }
        )
        result["attachments"] = (
            self.attachments.list(
                conn,
                actor,
                row,
                None
                if own
                else json.loads(submission["attachment_ids_json"])
                if submission
                else [],
            )
            if self.attachments
            else []
        )
        return result

    def _requests(self, conn, actor, where, params, limit, offset):
        page_bounds(limit, offset)
        base = (
            " FROM material_requests r JOIN companies c ON c.id=r.company_id AND c.status='active' JOIN firm_clients fc ON fc.firm_id=r.firm_id AND fc.company_id=r.company_id AND fc.status='active' WHERE r.firm_id=? AND "
            + where
        )
        total = conn.execute("SELECT COUNT(*)" + base, [actor.firm_id, *params]).fetchone()[0]
        rows = conn.execute(
            "SELECT r.*,c.name AS company_name" + base + " ORDER BY r.due_on,r.id LIMIT ? OFFSET ?",
            [actor.firm_id, *params, limit, offset],
        ).fetchall()
        return {
            "rows": [self._public(conn, actor, dict(row)) for row in rows],
            "pagination": {
                "limit": limit,
                "offset": offset,
                "total": total,
                "has_more": offset + len(rows) < total,
            },
        }

    def book_requests(self, actor, book_id, limit=20, offset=0, request_id=None):
        with self.db.transaction(actor=actor) as conn:
            self._book(conn, actor, book_id)
            return self._requests(
                conn,
                actor,
                "r.book_id=?" + (" AND r.id=?" if request_id else ""),
                [book_id, *([request_id] if request_id else [])],
                limit,
                offset,
            )

    def my_requests(self, actor, company_id=None, period=None, limit=20, offset=0, request_id=None):
        if period and not re.fullmatch(r"20[0-9]{2}-(0[1-9]|1[0-2])", period):
            raise AccountingError("请选择有效月份")
        with self.db.transaction(actor=actor) as conn:
            self.access.require_user(conn, actor.user_id)
            if company_id:
                self.access.company(conn, actor, company_id)
                if not self.access.company_role(conn, actor, company_id):
                    raise AccountingError("当前企业没有本人资料请求", 404)
            where = "r.assignee_user_id=? AND EXISTS(SELECT 1 FROM company_memberships cm WHERE cm.company_id=r.company_id AND cm.user_id=?)"
            params = [actor.user_id, actor.user_id]
            if company_id:
                where += " AND r.company_id=?"
                params.append(company_id)
            if period:
                where += " AND r.period=?"
                params.append(period)
            if request_id:
                where += " AND r.id=?"
                params.append(request_id)
            return self._requests(conn, actor, where, params, limit, offset)

    def _action(
        self,
        conn,
        actor,
        row,
        version,
        action,
        reason,
        *,
        mode=None,
        event_ids=None,
        evidence=None,
        attachment_ids=None,
        attachment_evidence=None,
    ):
        conn.execute(
            "INSERT INTO material_request_actions(id,request_id,book_id,firm_id,company_id,version,action,state,mode,reason,event_ids_json,evidence_json,attachment_ids_json,attachment_evidence_json,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                uuid4().hex,
                row["id"],
                row["book_id"],
                actor.firm_id,
                row["company_id"],
                version,
                action,
                {"submit": "submitted", "accept": "accepted", "return": "returned"}[action],
                mode,
                reason,
                encode(event_ids or []),
                encode(evidence or []),
                encode(attachment_ids or []),
                encode(attachment_evidence or []),
                actor.user_id,
                now(),
            ),
        )
        # The database appends the canonical scoped audit in the same transaction.
        # Employees can submit assigned materials without receiving book access.

    def submit(self, actor, request_id, request):
        if request.attachment_ids and not self.attachments:
            raise AccountingError("独立资料原件存储尚未配置", 409)
        verified_files = (
            self.attachments.verify(actor, request_id, request.attachment_ids)
            if request.attachment_ids
            else []
        )
        company_id = self._company_of_request(actor, request_id)
        with self.db.transaction(
            write=True, actor=actor, company_id=company_id, write_scope="company"
        ) as conn:
            row = self._request(conn, actor, request_id, submit=True)
            prior = self._public(conn, actor, row)
            if prior["version"] != request.expected_version:
                raise AccountingError("资料请求已更新，请刷新后重试", 409)
            if not prior["permissions"]["submit"]:
                raise AccountingError("当前状态不可重复提交，请等待验收或退回", 409)
            evidence = self._evidence(conn, actor, row, request.event_ids, own=True)
            if request.attachment_ids and not self.attachments:
                raise AccountingError("独立资料原件存储尚未配置", 409)
            files = (
                self.attachments.evidence(conn, actor, row, request.attachment_ids)
                if request.attachment_ids
                else []
            )
            if [(a["id"], a["sha256"]) for a in files] != [
                (a["id"], a["sha256"]) for a in verified_files
            ]:
                raise AccountingError("原件依据在核对后发生变化，请重新提交", 409)
            self._action(
                conn,
                actor,
                row,
                request.expected_version + 1,
                "submit",
                request.note,
                mode=request.mode,
                event_ids=request.event_ids,
                evidence=self._stamp(evidence),
                attachment_ids=request.attachment_ids,
                attachment_evidence=[{"id": a["id"], "sha256": a["sha256"]} for a in files],
            )
            return self._public(conn, actor, row)

    def review(self, actor, request_id, request):
        verified_files = []
        if request.action == "accept":
            with self.db.transaction(actor=actor) as conn:
                row = self._request(conn, actor, request_id, review=True)
                actions = self._actions(conn, request_id)
                latest = actions[-1] if actions else None
                if not latest or latest["version"] != request.expected_version:
                    raise AccountingError("资料请求已更新，请刷新后重试", 409)
                submission = next((a for a in reversed(actions) if a["action"] == "submit"), None)
                file_ids = json.loads(submission["attachment_ids_json"]) if submission else []
            if file_ids:
                if not self.attachments:
                    raise AccountingError("独立资料原件存储尚未配置", 409)
                verified_files = self.attachments.verify(actor, request_id, file_ids, review=True)
        company_id = self._company_of_request(actor, request_id)
        with self.db.transaction(
            write=True, actor=actor, company_id=company_id, write_scope="company"
        ) as conn:
            row = self._request(conn, actor, request_id, review=True)
            prior = self._public(conn, actor, row)
            if prior["version"] != request.expected_version:
                raise AccountingError("资料请求已更新，请刷新后重试", 409)
            if prior["state"] not in {"submitted", "accepted"} or (
                prior["state"] == "accepted" and request.action != "return"
            ):
                raise AccountingError("待验收资料可验收或退回；已验收资料可退回补正", 409)
            if prior["latest_submission"]["submitted_by"] == actor.user_id:
                raise AccountingError("资料提交与验收必须由不同人员完成", 403)
            if request.action == "accept" and not prior["evidence_current"]:
                raise AccountingError("资料证据已变化、失权或尚未移交，请核查后退回重提", 409)
            if request.action == "accept" and prior["latest_submission"]["attachment_ids"]:
                if not self.attachments:
                    raise AccountingError("独立资料原件存储尚未配置", 409)
                files = self.attachments.evidence(
                    conn, actor, row, prior["latest_submission"]["attachment_ids"]
                )
                expected = [
                    {"id": a["id"], "sha256": a["sha256"]}
                    for a in prior["latest_submission"]["attachments"]
                ]
                if [{"id": a["id"], "sha256": a["sha256"]} for a in files] != expected:
                    raise AccountingError("独立原件内容已变化，请核查并退回补正", 409)
                if [(a["id"], a["sha256"]) for a in files] != [
                    (a["id"], a["sha256"]) for a in verified_files
                ]:
                    raise AccountingError("原件依据在核对后发生变化，请重新验收", 409)
            self._action(
                conn, actor, row, request.expected_version + 1, request.action, request.reason
            )
            return self._public(conn, actor, row)
