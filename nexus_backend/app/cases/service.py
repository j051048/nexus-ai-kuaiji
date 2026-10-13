"""补件独立于已批准业务事实；完成待办不能代替财务异常核验。"""
import json
from datetime import datetime
from uuid import uuid4
from zoneinfo import ZoneInfo
from app.accounting.access import AccessControl
from app.accounting.domain import Actor, AccountingError
from app.accounting.service import fingerprint, now
from app.business.service import encode

ACTIVE = {"open", "awaiting_info", "response_submitted", "in_review"}


class CaseService:
    def __init__(self, db, business):
        self.db, self.business, self.access = db, business, AccessControl()

    def _task_table(self):
        return "(SELECT * FROM case_tasks UNION ALL SELECT * FROM accounting.case_collector_tasks())" if self.db.is_postgres else "case_tasks"

    def _current_target(self, conn, row):
        if row["event_id"]:
            mapping = conn.execute("SELECT book_id,source_id,draft_id FROM business_mappings WHERE event_id=?", (row["event_id"],)).fetchone()
            if mapping:
                return dict(mapping)
        return {"book_id": row["book_id"], "source_id": row["source_id"], "draft_id": row["draft_id"]}

    def _manage(self, conn, actor, row):
        if row["event_id"]:
            event = self.business._event(conn, actor, row["event_id"])
            if self.access.company_role(conn, actor, row["company_id"]) == "finance" and event["applicant_id"] != actor.user_id:
                return True
        target = self._current_target(conn, row)
        if target["book_id"]:
            try:
                self.access.book(conn, actor, target["book_id"], "review")
                return True
            except AccountingError as error:
                if error.status not in {403, 404}:
                    raise
        return False

    def _recipient(self, conn, actor, row):
        return bool(row["kind"] == "supplement" and row["recipient_id"] == actor.user_id and
                    self.business._membership(conn, actor, row["company_id"]))

    def _collector(self, conn, actor, row, candidate=False):
        if row["event_id"] or row["kind"] != "exception" or not row["book_id"] or not candidate and row["assignee_id"] != actor.user_id:
            return False
        if self.access.company_role(conn, actor, row["company_id"]) != "finance":
            return False
        try:
            self.access.book(conn, actor, row["book_id"], "edit")
            return True
        except AccountingError as error:
            if error.status in {403, 404}:
                return False
            raise

    def _responder(self, conn, actor, row):
        return self._recipient(conn, actor, row) or self._collector(conn, actor, row) or bool(row["kind"] == "exception" and not row["event_id"] and self._manage(conn, actor, row))

    def _visible(self, conn, actor, row):
        try:
            return self._recipient(conn, actor, row) or self._collector(conn, actor, row) or self._manage(conn, actor, row)
        except AccountingError as error:
            if error.status in {403, 404}:
                return False
            raise

    def _case(self, conn, actor, case_id, manage=False):
        self.access.require_user(conn, actor.user_id)
        row = conn.execute("SELECT t.* FROM " + self._task_table() + " t JOIN firm_clients f ON f.firm_id=t.firm_id AND f.company_id=t.company_id AND f.status='active' WHERE t.id=? AND t.firm_id=?", (case_id, actor.firm_id)).fetchone()
        if not row or not self._visible(conn, actor, row):
            raise AccountingError("异常任务不存在或无权访问", 404)
        if manage and not self._manage(conn, actor, row):
            raise AccountingError("需要当前企业财务或账套复核权限", 403)
        return row

    def _editable(self, conn, actor, row):
        target = self._current_target(conn, row)
        if target["book_id"]:
            book = self.access.book(conn, actor, target["book_id"])
            if book["period_status"] != "open":
                raise AccountingError("会计期间已关闭，不能修改补件或异常任务", 409)
            draft = conn.execute("SELECT status FROM drafts WHERE id=? AND book_id=?", (target["draft_id"], target["book_id"])).fetchone()
            if not draft or draft["status"] != "draft":
                raise AccountingError("该凭证已审核或导出，不能再修改补件或异常任务", 409)

    def _response_open(self, conn, actor, row):
        if self.db.is_postgres:
            if not conn.execute("SELECT accounting.case_source_open(?)", (row["id"],)).fetchone()[0]:
                raise AccountingError("会计期间已关闭或凭证已审核，不能继续补件", 409)
            return
        target = self._current_target(conn, row)
        if not target["book_id"]:
            return
        allowed = bool(conn.execute("SELECT 1 FROM drafts d JOIN books b ON b.id=d.book_id JOIN periods p ON p.book_id=b.id AND p.period=b.period WHERE d.id=? AND d.book_id=? AND d.status='draft' AND p.status='open'", (target["draft_id"], target["book_id"])).fetchone())
        if not allowed:
            raise AccountingError("会计期间已关闭或凭证已审核，不能继续补件", 409)

    def _receipt(self, conn, actor, case_id, operation, payload):
        digest = fingerprint({"case_id": case_id, "operation": operation, "payload": payload.model_dump()})
        prior = conn.execute("SELECT * FROM case_commands WHERE firm_id=? AND user_id=? AND idempotency_key=?", (actor.firm_id, actor.user_id, payload.idempotency_key)).fetchone()
        if prior and (prior["request_fingerprint"] != digest or prior["case_id"] != case_id):
            raise AccountingError("幂等键已经用于不同的异常处理操作", 409)
        return digest, prior

    def _version(self, row, expected):
        if row["version"] != expected:
            raise AccountingError("异常任务已更新，请刷新后重试", 409)

    def _history(self, conn, actor, row, action, reason, audience="internal", version=None):
        conn.execute("INSERT INTO case_history(id,case_id,firm_id,company_id,case_version,action,audience,reason,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)", (uuid4().hex, row["id"], actor.firm_id, row["company_id"], version or row["version"], action, audience, reason, actor.user_id, now()))

    def _finish(self, conn, actor, row, payload, digest, operation, audience="internal", **changes):
        values = {**changes, "version": row["version"] + 1, "updated_at": now()}
        if self.db.is_postgres and self._collector(conn, actor, row) and not self._manage(conn, actor, row):
            conn.execute("SELECT accounting.case_collection_update(?,?,?,?)", (row["id"], row["version"], changes.get("state", row["state"]), values["updated_at"]))
        else:
            conn.execute("UPDATE case_tasks SET " + ",".join(f"{key}=?" for key in values) + " WHERE id=? AND firm_id=? AND version=?", [*values.values(), row["id"], actor.firm_id, row["version"]])
        self._history(conn, actor, row, operation, payload.reason, audience, row["version"] + 1)
        conn.execute("INSERT INTO case_commands(firm_id,user_id,idempotency_key,case_id,request_fingerprint,result_version,created_at) VALUES(?,?,?,?,?,?,?)", (actor.firm_id, actor.user_id, payload.idempotency_key, row["id"], digest, row["version"] + 1, now()))
        # A requester/collector can act on published case material without
        # gaining ledger audit permissions. The case ID carries its scope.
        self.business._audit(conn, actor, "case." + operation, row["id"], {"case_version": row["version"] + 1, "operation": operation})
        return self._json(conn, actor, self._case(conn, actor, row["id"]))

    def _json(self, conn, actor, row, detail=True):
        manage, recipient = self._manage(conn, actor, row), self._recipient(conn, actor, row)
        result = dict(row)
        if manage:
            result.update(self._current_target(conn, row))
        result.pop("accepted_fact_revision", None)
        result["required_fields"] = json.loads(row["required_fields"])
        company = conn.execute("SELECT name FROM companies WHERE id=?", (row["company_id"],)).fetchone()
        result["company_name"] = company["name"] if company else ""
        result["overdue"] = bool(row["state"] != "closed" and row["due_on"] and row["due_on"] < datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat())
        can_respond = self._responder(conn, actor, row) and row["state"] in ({"awaiting_info"} if row["event_id"] else {"open"})
        result["permissions"] = {"manage": manage, "respond": can_respond, "upload": can_respond}
        if not manage and self._collector(conn, actor, row):
            result["collection_request"] = True
            publication = conn.execute("SELECT reason FROM case_history WHERE case_id=? AND audience='requester' ORDER BY created_at DESC,id DESC LIMIT 1", (row["id"],)).fetchone()
            result["title"] = "银行流水资料补充"
            result["request_reason"] = publication["reason"] if publication else "请补充已指派的业务资料"
            source = conn.execute("SELECT date,description,counterparty,amount_cents,direction FROM sources WHERE id=? AND book_id=?", (row["source_id"], row["book_id"])).fetchone()
            if source:
                from app.accounting.models import cents_to_money
                result["source_summary"] = {"date": source["date"], "description": source["description"], "counterparty": source["counterparty"], "amount": cents_to_money(source["amount_cents"]), "direction": source["direction"]}
        if not manage:
            for key in ("book_id", "draft_id", "source_id", "assignee_id", "created_by"):
                result.pop(key, None)
        if not detail:
            return result
        history = conn.execute("SELECT action,audience,reason,case_version,created_at,created_by FROM case_history WHERE case_id=?" + ("" if manage else " AND audience='requester'") + " ORDER BY created_at,id", (row["id"],))
        result["history"] = [dict(item) for item in history]
        result["responses"] = []
        for response in conn.execute("SELECT * FROM case_responses WHERE case_id=? ORDER BY created_at,id", (row["id"],)):
            if not manage and response["created_by"] != actor.user_id:
                continue
            item = dict(response)
            item["fields"] = json.loads(item["fields"])
            item["supersedes_attachment_ids"] = json.loads(item["supersedes_attachment_ids"])
            if not manage:
                item.pop("supersedes_attachment_ids", None)
            item["attachments"] = [dict(a) for a in conn.execute("SELECT id,filename,media_type,size_bytes,sha256,created_at FROM case_attachments WHERE response_id=? ORDER BY created_at,id", (item["id"],))]
            result["responses"].append(item)
        result["replacement_choices"] = []
        if manage and row["event_id"]:
            superseded = self.superseded_attachment_ids_in_transaction(conn, actor, row["event_id"])
            result["replacement_choices"] = [dict(item) for item in conn.execute("SELECT id,filename FROM business_attachments WHERE event_id=? ORDER BY created_at,id", (row["event_id"],)) if item["id"] not in superseded]
        return result

    def _scope_sql(self, actor):
        # RLS and SQLite use the same current grants. Assignment never appears
        # here: a task must not create permission to its underlying evidence.
        sql = """((t.kind='supplement' AND t.recipient_id=? AND EXISTS(SELECT 1 FROM company_memberships cm WHERE cm.company_id=t.company_id AND cm.user_id=?))
          OR (t.event_id IS NOT NULL AND EXISTS(SELECT 1 FROM company_memberships cm JOIN business_events e ON e.id=t.event_id WHERE cm.company_id=t.company_id AND cm.user_id=? AND cm.role='finance' AND e.applicant_id<>?))
          OR (t.book_id IS NOT NULL AND (EXISTS(SELECT 1 FROM memberships m WHERE m.firm_id=t.firm_id AND m.user_id=? AND m.role IN ('owner','admin'))
           OR EXISTS(SELECT 1 FROM book_access ba WHERE ba.book_id=t.book_id AND ba.firm_id=t.firm_id AND ba.user_id=? AND ba.role='reviewer' AND
            (EXISTS(SELECT 1 FROM memberships m WHERE m.firm_id=t.firm_id AND m.user_id=? AND m.role='reviewer') OR
             (NOT EXISTS(SELECT 1 FROM memberships m WHERE m.firm_id=t.firm_id AND m.user_id=?) AND EXISTS(SELECT 1 FROM company_memberships cm WHERE cm.company_id=t.company_id AND cm.user_id=? AND cm.role='finance')))))))"""
        sql = sql.replace("t.book_id", "COALESCE(t.book_id,(SELECT bm.book_id FROM business_mappings bm WHERE bm.event_id=t.event_id))")
        collector = """(t.kind='exception' AND t.event_id IS NULL AND t.assignee_id=? AND EXISTS(SELECT 1 FROM company_memberships cm WHERE cm.company_id=t.company_id AND cm.user_id=? AND cm.role='finance') AND
         EXISTS(SELECT 1 FROM book_access ba WHERE ba.book_id=t.book_id AND ba.firm_id=t.firm_id AND ba.user_id=? AND ba.role IN ('editor','reviewer') AND
          (NOT EXISTS(SELECT 1 FROM memberships m WHERE m.firm_id=t.firm_id AND m.user_id=?) OR EXISTS(SELECT 1 FROM memberships m WHERE m.firm_id=t.firm_id AND m.user_id=? AND m.role IN ('owner','admin','operator','reviewer')))))"""
        return "(" + sql + " OR " + collector + ")", [actor.user_id] * 14

    def list(self, actor, company_id=None, book_id=None, event_id=None, state=None, overdue=False, mine=False, limit=20, offset=0):
        if type(limit) is not int or not 1 <= limit <= 100 or type(offset) is not int or offset < 0:
            raise AccountingError("分页参数无效")
        if state and state not in ACTIVE | {"closed"}:
            raise AccountingError("任务状态无效")
        with self.db.transaction(actor=actor) as conn:
            self.access.require_user(conn, actor.user_id)
            scope, params = self._scope_sql(actor)
            parts, values = ["t.firm_id=?", "f.status='active'", scope], [actor.firm_id, *params]
            for key, value in (("company_id", company_id), ("book_id", book_id), ("event_id", event_id), ("state", state)):
                if value:
                    parts.append("COALESCE(t.book_id,(SELECT bm.book_id FROM business_mappings bm WHERE bm.event_id=t.event_id))=?" if key == "book_id" else "t." + key + "=?")
                    values.append(value)
            if overdue:
                parts.append("t.state<>'closed' AND t.due_on<?")
                values.append(datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat())
            if mine:
                parts.append("(t.assignee_id=? OR (t.kind='supplement' AND t.recipient_id=?))")
                values.extend([actor.user_id, actor.user_id])
            base = " FROM " + self._task_table() + " t JOIN firm_clients f ON f.firm_id=t.firm_id AND f.company_id=t.company_id WHERE " + " AND ".join(parts)
            total = conn.execute("SELECT COUNT(*)" + base, values).fetchone()[0]
            rows = conn.execute("SELECT t.*" + base + " ORDER BY CASE t.priority WHEN 'urgent' THEN 0 WHEN 'high' THEN 1 ELSE 2 END,COALESCE(t.due_on,'9999-12-31'),t.updated_at DESC,t.id LIMIT ? OFFSET ?", [*values, limit, offset])
            return {"cases": [self._json(conn, actor, row, False) for row in rows], "total": total, "limit": limit, "offset": offset}

    def get(self, actor, case_id):
        with self.db.transaction(actor=actor) as conn:
            return self._json(conn, actor, self._case(conn, actor, case_id))

    def create(self, actor, payload):
        with self.db.transaction(write=True, actor=actor) as conn:
            self.access.require_user(conn, actor.user_id)
            event = self.business._event(conn, actor, payload.event_id) if payload.event_id else None
            mapping = conn.execute("SELECT * FROM business_mappings WHERE event_id=?", (event["id"],)).fetchone() if event else None
            book_id, draft_id = payload.book_id, payload.draft_id
            if mapping:
                if book_id and book_id != mapping["book_id"] or draft_id and draft_id != mapping["draft_id"]:
                    raise AccountingError("任务账套或凭证与业务来源不一致", 422)
                book_id, draft_id = mapping["book_id"], mapping["draft_id"]
            if bool(book_id) != bool(draft_id) or not event and not book_id:
                raise AccountingError("必须指定业务事项或同一账套的来源凭证", 422)
            book = self.access.book(conn, actor, book_id) if book_id else None
            draft = conn.execute("SELECT * FROM drafts WHERE id=? AND book_id=?", (draft_id, book_id)).fetchone() if book else None
            if book and (not draft or event and book["company_id"] != event["company_id"]):
                raise AccountingError("任务来源与企业不一致", 404)
            if payload.kind == "supplement" and not event:
                raise AccountingError("员工补件须绑定原业务事项；流水异常请创建会计内部任务", 422)
            case_id = uuid4().hex
            row = {"id": case_id, "firm_id": actor.firm_id, "company_id": event["company_id"] if event else book["company_id"], "event_id": event["id"] if event else None,
                   "book_id": book_id, "draft_id": draft_id, "source_id": draft["source_id"] if draft else None, "kind": payload.kind,
                   "title": payload.title, "request_reason": payload.reason, "required_fields": encode(payload.required_fields), "attachment_required": int(payload.attachment_required),
                   "recipient_id": event["applicant_id"] if event and payload.kind == "supplement" else None,
                   "assignee_id": None, "priority": payload.priority, "due_on": payload.due_on, "state": "awaiting_info" if payload.kind == "supplement" else "open", "version": 1,
                   "evidence_revision": 0, "accepted_fact_revision": 0, "created_by": actor.user_id, "created_at": now(), "updated_at": now()}
            if not self._manage(conn, actor, row):
                raise AccountingError("需要当前企业财务或账套复核权限", 403)
            self._editable(conn, actor, row)
            digest = fingerprint({"operation": "create", "payload": payload.model_dump()})
            prior = conn.execute("SELECT * FROM case_commands WHERE firm_id=? AND user_id=? AND idempotency_key=?", (actor.firm_id, actor.user_id, payload.idempotency_key)).fetchone()
            if prior:
                if prior["request_fingerprint"] != digest:
                    raise AccountingError("幂等键已经用于不同的任务", 409)
                return self._json(conn, actor, self._case(conn, actor, prior["case_id"]))
            conn.execute("INSERT INTO case_tasks(" + ",".join(row) + ") VALUES(" + ",".join("?" for _ in row) + ")", list(row.values()))
            self._history(conn, actor, row, "create", payload.reason, "requester" if payload.kind == "supplement" else "internal")
            conn.execute("INSERT INTO case_commands(firm_id,user_id,idempotency_key,case_id,request_fingerprint,result_version,created_at) VALUES(?,?,?,?,?,1,?)", (actor.firm_id, actor.user_id, payload.idempotency_key, case_id, digest, now()))
            self.business._audit(conn, actor, "case.create", case_id, {"kind": payload.kind, "case_version": 1})
            return self._json(conn, actor, self._case(conn, actor, case_id))

    def command(self, actor, case_id, operation, payload):
        with self.db.transaction(write=True, actor=actor) as conn:
            row = self._case(conn, actor, case_id, manage=operation != "respond")
            digest, prior = self._receipt(conn, actor, case_id, operation, payload)
            if prior:
                return self._json(conn, actor, row)
            self._version(row, payload.expected_version)
            if operation != "respond":
                self._editable(conn, actor, row)
            if operation == "assign":
                if row["state"] == "closed":
                    raise AccountingError("已关闭任务不能指派", 409)
                target = Actor(payload.assignee_id, actor.firm_id)
                allowed = conn.execute("SELECT accounting.case_assignee_allowed(?,?)", (case_id, payload.assignee_id)).fetchone()[0] if self.db.is_postgres else self._manage(conn, target, row) or self._collector(conn, target, row, candidate=True)
                if not allowed:
                    raise AccountingError("被指派人必须已有当前企业财务或账套复核权限，指派不会授予权限", 403)
                return self._finish(conn, actor, row, payload, digest, operation, "requester" if not row["event_id"] else "internal", assignee_id=payload.assignee_id, due_on=payload.due_on, priority=payload.priority)
            if operation == "comment":
                if payload.audience == "requester" and row["kind"] != "supplement" and row["event_id"]:
                    raise AccountingError("内部异常任务不能公开给员工", 422)
                return self._finish(conn, actor, row, payload, digest, operation, payload.audience)
            if operation == "respond":
                if not self._responder(conn, actor, row) or row["state"] not in ({"awaiting_info"} if row["event_id"] else {"open"}):
                    raise AccountingError("仅当前申请人可响应待补件请求", 403)
                self._response_open(conn, actor, row)
                self._respond(conn, actor, row, payload)
                return self._finish(conn, actor, row, payload, digest, operation, "requester" if row["kind"] == "supplement" or self._collector(conn, actor, row) else "internal", state="response_submitted")
            if operation == "review-response":
                response = conn.execute("SELECT * FROM case_responses WHERE case_id=? AND state='submitted'", (case_id,)).fetchone()
                if row["state"] != "response_submitted" or not response:
                    raise AccountingError("没有待核验的补件响应", 409)
                if response["created_by"] == actor.user_id:
                    raise AccountingError("不能核验本人提交的补件", 403)
                if payload.supersedes_attachment_ids:
                    if payload.decision != "accept" or not row["event_id"]:
                        raise AccountingError("仅接受同一业务事项的新原件时可指定替代关系", 422)
                    if not conn.execute("SELECT 1 FROM case_attachments WHERE response_id=?", (response["id"],)).fetchone():
                        raise AccountingError("替代原件至少需要一份新上传的补件附件", 422)
                    for attachment_id in payload.supersedes_attachment_ids:
                        if not conn.execute("SELECT 1 FROM business_attachments WHERE id=? AND event_id=? AND firm_id=? AND company_id=?", (attachment_id, row["event_id"], actor.firm_id, row["company_id"])).fetchone() or conn.execute("SELECT 1 FROM case_supersessions WHERE old_attachment_id=?", (attachment_id,)).fetchone():
                            raise AccountingError("被替代原件不存在、不属于当前事项或已经被替代", 404)
                conn.execute("UPDATE case_responses SET state=?,reviewed_by=?,reviewed_at=?,review_reason=?,supersedes_attachment_ids=? WHERE id=? AND state='submitted'", ("accepted" if payload.decision == "accept" else "returned", actor.user_id, now(), payload.reason, encode(payload.supersedes_attachment_ids), response["id"]))
                changes = {"state": "in_review" if payload.decision == "accept" else "awaiting_info" if row["event_id"] else "open"}
                if payload.decision == "accept":
                    changes["evidence_revision"] = row["evidence_revision"] + 1
                result = self._finish(conn, actor, row, payload, digest, operation, "requester" if row["kind"] == "supplement" or row["assignee_id"] and not row["event_id"] else "internal", **changes)
                if payload.decision == "accept":
                    response = conn.execute("SELECT * FROM case_responses WHERE id=?", (response["id"],)).fetchone()
                    self._accept_evidence(conn, actor, self._case(conn, actor, case_id), response)
                    result = self._json(conn, actor, self._case(conn, actor, case_id))
                return result
            if operation == "close":
                if row["state"] in {"awaiting_info", "response_submitted"}:
                    raise AccountingError("补件尚未提交或核验，不能关闭任务", 409)
                if row["state"] == "closed":
                    raise AccountingError("任务已经关闭", 409)
                return self._finish(conn, actor, row, payload, digest, operation, "requester" if row["kind"] == "supplement" else "internal", state="closed")
            raise AccountingError("异常处理操作不存在", 404)

    def _response_draft(self, conn, actor, row):
        response = conn.execute("SELECT * FROM case_responses WHERE case_id=? AND state='draft'", (row["id"],)).fetchone()
        if response:
            if response["created_by"] != actor.user_id:
                raise AccountingError("补件草稿所属账号已变化", 409)
            return response
        response_id = uuid4().hex
        conn.execute("INSERT INTO case_responses(id,case_id,firm_id,company_id,state,created_by,created_at) VALUES(?,?,?,?,'draft',?,?)", (response_id, row["id"], actor.firm_id, row["company_id"], actor.user_id, now()))
        return conn.execute("SELECT * FROM case_responses WHERE id=?", (response_id,)).fetchone()

    def _respond(self, conn, actor, row, payload):
        missing = [field for field in json.loads(row["required_fields"]) if not payload.fields.get(field, "").strip()]
        if missing:
            raise AccountingError("请补齐要求的字段：" + "、".join(missing), 422)
        response = self._response_draft(conn, actor, row)
        if row["attachment_required"] and not conn.execute("SELECT 1 FROM case_attachments WHERE response_id=?", (response["id"],)).fetchone():
            raise AccountingError("请先上传要求的补件原件", 422)
        conn.execute("UPDATE case_responses SET message=?,fields=?,state='submitted',submitted_at=? WHERE id=? AND state='draft'", (payload.reason, encode(payload.fields), now(), response["id"]))

    def require_upload(self, actor, case_id, expected_version, idempotency_key=None):
        with self.db.transaction(actor=actor) as conn:
            row = self._case(conn, actor, case_id)
            if idempotency_key and conn.execute("SELECT 1 FROM case_commands WHERE firm_id=? AND user_id=? AND idempotency_key=? AND case_id=?", (actor.firm_id, actor.user_id, idempotency_key, case_id)).fetchone():
                return
            self._version(row, expected_version)
            if not self._responder(conn, actor, row) or row["state"] not in ({"awaiting_info"} if row["event_id"] else {"open"}):
                raise AccountingError("仅申请人可上传待补件原件", 403)
            self._response_open(conn, actor, row)

    def add_attachment(self, actor, case_id, expected_version, idempotency_key, stored):
        from .models import CaseCommand
        payload = CaseCommand(expected_version=expected_version, idempotency_key=idempotency_key, reason="上传补件原件：" + stored["filename"])
        with self.db.transaction(write=True, actor=actor) as conn:
            row = self._case(conn, actor, case_id)
            digest = fingerprint({"operation": "upload", "case_id": case_id, "expected_version": expected_version, "sha256": stored["sha256"], "filename": stored["filename"]})
            prior = conn.execute("SELECT * FROM case_commands WHERE firm_id=? AND user_id=? AND idempotency_key=?", (actor.firm_id, actor.user_id, idempotency_key)).fetchone()
            if prior:
                if prior["request_fingerprint"] != digest or prior["case_id"] != case_id:
                    raise AccountingError("幂等键已用于不同附件", 409)
                return {"case": self._json(conn, actor, row), "duplicate": True}
            self._version(row, expected_version)
            if not self._responder(conn, actor, row) or row["state"] not in ({"awaiting_info"} if row["event_id"] else {"open"}):
                raise AccountingError("仅申请人可上传待补件原件", 403)
            self._response_open(conn, actor, row)
            response = self._response_draft(conn, actor, row)
            summary = conn.execute("SELECT COUNT(*),COALESCE(SUM(size_bytes),0) FROM case_attachments WHERE response_id=?", (response["id"],)).fetchone()
            total = conn.execute("SELECT COALESCE(SUM(size_bytes),0) FROM case_attachments WHERE case_id=?", (case_id,)).fetchone()[0]
            if summary[0] >= 10 or summary[1] + stored["size_bytes"] > 50 * 1024 * 1024 or total + stored["size_bytes"] > 100 * 1024 * 1024:
                raise AccountingError("每次补件最多 10 个附件、50 MB，任务累计最多 100 MB", 413)
            conn.execute("INSERT INTO case_attachments(id,response_id,case_id,firm_id,company_id,storage_key,filename,media_type,sha256,size_bytes,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (stored["id"], response["id"], case_id, actor.firm_id, row["company_id"], stored["storage_key"], stored["filename"], stored["media_type"], stored["sha256"], stored["size_bytes"], actor.user_id, now()))
            return {"case": self._finish(conn, actor, row, payload, digest, "upload", "requester" if row["kind"] == "supplement" or self._collector(conn, actor, row) else "internal"), "duplicate": False}

    def download_metadata(self, actor, attachment_id):
        with self.db.transaction(actor=actor) as conn:
            item = conn.execute("SELECT * FROM case_attachments WHERE id=? AND firm_id=?", (attachment_id, actor.firm_id)).fetchone()
            if not item:
                raise AccountingError("补件原件不存在或无权访问", 404)
            task = self._case(conn, actor, item["case_id"])
            if not self._manage(conn, actor, task) and item["created_by"] != actor.user_id:
                raise AccountingError("补件原件不存在或无权访问", 404)
            return dict(item)

    def _accept_evidence(self, conn, actor, row, response):
        if self.db.is_postgres:
            conn.execute("SELECT accounting.case_accept_evidence(?,?)", (row["id"], row["version"]))
            return
        if row["event_id"]:
            event = self.business._event(conn, actor, row["event_id"])
            if not self.business._membership(conn, Actor(response["created_by"], actor.firm_id), row["company_id"]):
                raise AccountingError("补件申请人已被撤销企业权限，请重新核实", 409)
            conn.execute("INSERT INTO business_attachments(id,event_id,firm_id,company_id,storage_key,filename,media_type,sha256,size_bytes,created_by,created_at) SELECT a.id,?,a.firm_id,a.company_id,a.storage_key,a.filename,a.media_type,a.sha256,a.size_bytes,a.created_by,a.created_at FROM case_attachments a WHERE a.response_id=?", (row["event_id"], response["id"]))
            for old_id in json.loads(response["supersedes_attachment_ids"]):
                conn.execute("INSERT INTO case_supersessions(old_attachment_id,event_id,case_id,response_id,firm_id,company_id,accepted_by,accepted_at) VALUES(?,?,?,?,?,?,?,?)", (old_id, row["event_id"], row["id"], response["id"], actor.firm_id, row["company_id"], actor.user_id, now()))
            conn.execute("UPDATE business_events SET version=version+1,fact_version=fact_version+1,updated_at=? WHERE id=? AND firm_id=?", (now(), event["id"], actor.firm_id))
        elif row["source_id"]:
            evidence_ref = "已核验补件 case:" + row["id"] + "；" + response["message"]
            conn.execute("INSERT INTO source_evidence(source_id,book_id,evidence_ref,created_at,created_by) VALUES(?,?,?,?,?)", (row["source_id"], row["book_id"], evidence_ref, now(), actor.user_id))
            conn.execute("UPDATE drafts SET version=version+1 WHERE id=? AND book_id=? AND status='draft'", (row["draft_id"], row["book_id"]))
        conn.execute("UPDATE case_tasks SET accepted_fact_revision=evidence_revision WHERE id=?", (row["id"],))

    def blocking_issues_in_transaction(self, conn, actor, source, draft=None):
        source_id = source["id"]
        book_id = source["book_id"]
        if self.db.is_postgres:
            blocked = conn.execute("SELECT accounting.case_source_blocked(?,?)", (book_id, source_id)).fetchone()[0]
            return ["异常待办未关闭，请由授权人员核对"] if blocked else []
        mapping = conn.execute("SELECT event_id FROM business_mappings WHERE source_id=? AND book_id=?", (source_id, book_id)).fetchone()
        firm_id = actor.firm_id if actor else conn.execute("SELECT firm_id FROM books WHERE id=?", (book_id,)).fetchone()[0]
        rows = conn.execute("SELECT id,title,state FROM case_tasks WHERE firm_id=? AND state<>'closed' AND (source_id=? OR (? IS NOT NULL AND event_id=?)) ORDER BY created_at,id", (firm_id, source_id, mapping["event_id"] if mapping else None, mapping["event_id"] if mapping else None)).fetchall()
        return ["异常待办未关闭，请由授权人员核对"] if rows else []

    def evidence_revision_in_transaction(self, conn, actor, source=None, event_id=None):
        if source is not None:
            mapping = conn.execute("SELECT event_id FROM business_mappings WHERE source_id=? AND book_id=?", (source["id"], source["book_id"])).fetchone()
            event_id = mapping["event_id"] if mapping else None
        clauses, values = [], [actor.firm_id]
        if source is not None:
            clauses.append("source_id=?")
            values.append(source["id"])
        if event_id:
            clauses.append("event_id=?")
            values.append(event_id)
        predicate = " OR ".join(clauses) if clauses else "1=0"
        rows = [dict(row) for row in conn.execute("SELECT id,version,state,evidence_revision FROM case_tasks WHERE firm_id=? AND (" + predicate + ") ORDER BY id", values)]
        return fingerprint(rows)

    def accepted_context_in_transaction(self, conn, actor, event_id):
        self.business._event(conn, actor, event_id)
        return [{"response_id": row["id"], "message": row["message"], "fields": json.loads(row["fields"])} for row in conn.execute("SELECT r.id,r.message,r.fields FROM case_responses r JOIN case_tasks t ON t.id=r.case_id WHERE t.event_id=? AND t.firm_id=? AND r.state='accepted' ORDER BY r.reviewed_at,r.id", (event_id, actor.firm_id))]

    def superseded_attachment_ids_in_transaction(self, conn, actor, event_id):
        self.business._event(conn, actor, event_id)
        return {row["old_attachment_id"] for row in conn.execute("SELECT old_attachment_id FROM case_supersessions WHERE event_id=? AND firm_id=?", (event_id, actor.firm_id))}

    def assignees(self, actor, case_id):
        with self.db.transaction(actor=actor) as conn:
            row = self._case(conn, actor, case_id, manage=True)
            if self.db.is_postgres:
                return {"assignees": [dict(item) for item in conn.execute("SELECT * FROM accounting.case_assignees(?)", (case_id,))]}
            result = []
            for user in conn.execute("SELECT id AS user_id,name FROM users WHERE active=1 ORDER BY name,id"):
                try:
                    target = Actor(user["user_id"], actor.firm_id)
                    if self._manage(conn, target, row) or self._collector(conn, target, row, candidate=True):
                        result.append(dict(user))
                except AccountingError as error:
                    if error.status not in {403, 404}:
                        raise
            return {"assignees": result}
