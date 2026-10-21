"""Explicit human request -> existing case workflow, with a current-source fence."""
from __future__ import annotations

import hashlib

from pydantic import Field, StrictStr, field_validator

from app.accounting.domain import AccountingError
from app.accounting.models import InputModel
from app.accounting.service import fingerprint
from app.cases.models import CaseCreate


class HandoffRequest(InputModel):
    expected_fingerprint: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    idempotency_key: StrictStr = Field(min_length=8, max_length=120)
    reason: StrictStr = Field(min_length=1, max_length=1000)

    @field_validator("reason", "idempotency_key")
    @classmethod
    def text(cls, value):
        if not value.strip() or "\x00" in value:
            raise ValueError("请填写有效的人工处理依据和操作键")
        return value.strip()


class HandoffService:
    def __init__(self, cases, *, assistant=None, intelligence=None, intake=None):
        self.cases, self.db = cases, cases.db
        self.assistant, self.intelligence, self.intake = assistant, intelligence, intake

    def _event_basis(self, conn, actor, event_id):
        business = self.intelligence.business if self.intelligence else self.cases.business
        event = business._event(conn, actor, event_id)
        mapping = conn.execute("SELECT book_id,draft_id FROM business_mappings WHERE event_id=?", (event_id,)).fetchone()
        return {"id": event_id, "version": event["version"], "fact_version": event["fact_version"],
                "attachments": [dict(a) for a in conn.execute("SELECT id,sha256 FROM business_attachments WHERE event_id=? ORDER BY id", (event_id,))],
                "draft": self._draft_basis(conn, mapping["book_id"], mapping["draft_id"]) if mapping else None}

    @staticmethod
    def _draft_basis(conn, book_id, draft_id):
        row = conn.execute(
            "SELECT d.id,d.version,d.status,d.source_id,s.fingerprint,b.period,p.status AS period_status "
            "FROM drafts d JOIN sources s ON s.id=d.source_id AND s.book_id=d.book_id "
            "JOIN books b ON b.id=d.book_id JOIN periods p ON p.book_id=b.id AND p.period=b.period WHERE d.id=? AND d.book_id=?",
            (draft_id, book_id),
        ).fetchone()
        return dict(row) if row else None

    def _source(self, conn, actor, kind, run_id):
        if kind not in {"chat", "ocr", "journal", "intake"}:
            raise AccountingError("执行记录不存在或无权访问", 404)
        target = {}
        current = {}
        if kind == "chat" and self.assistant:
            from app.assistant.service import TURN_PUBLIC

            run = conn.execute("SELECT " + TURN_PUBLIC + " FROM assistant_turns WHERE id=? AND firm_id=?", (run_id, actor.firm_id)).fetchone()
            if not run:
                raise AccountingError("执行记录不存在或无权访问", 404)
            conversation = self.assistant._conversation(conn, actor, run["conversation_id"])
            # A new handoff case changes assessment aggregates. Bind underlying
            # facts, periods and drafts, excluding the case we are about to make.
            current = {"scope_version": conversation["scope_version"],
                       "events": [self._event_basis(conn, actor, event_id) for event_id in conversation["event_ids"]],
                       "books": [{key: book[key] for key in ("id", "period", "period_status")} for book in
                                 (self.assistant.access.book(conn, actor, book_id) for book_id in conversation["book_ids"])],
                       "draft": self._draft_basis(conn, conversation["book_ids"][0], conversation["draft_id"])
                       if len(conversation["book_ids"]) == 1 and conversation["draft_id"] else None}
            if len(conversation["event_ids"]) == 1:
                target = {"event_id": conversation["event_ids"][0]}
            elif not conversation["event_ids"] and len(conversation["book_ids"]) == 1 and conversation["draft_id"]:
                target = {"book_id": conversation["book_ids"][0], "draft_id": conversation["draft_id"]}
        elif kind in {"ocr", "journal"} and self.intelligence:
            run = conn.execute("SELECT * FROM ai_runs WHERE id=? AND firm_id=? AND kind=?", (run_id, actor.firm_id, "proposal" if kind == "journal" else "ocr")).fetchone()
            if not run:
                raise AccountingError("执行记录不存在或无权访问", 404)
            event = self.intelligence.business._event(conn, actor, run["event_id"])
            if kind == "journal":
                self.intelligence.access.book(conn, actor, run["book_id"])
            current = self._event_basis(conn, actor, event["id"])
            target = {"event_id": event["id"]}
        elif kind == "intake" and self.intake:
            run = conn.execute("SELECT * FROM intake_runs WHERE id=? AND firm_id=? AND created_by=?", (run_id, actor.firm_id, actor.user_id)).fetchone()
            if not run:
                raise AccountingError("执行记录不存在或无权访问", 404)
            draft = self.intake._draft(conn, actor, run["intake_id"])
            current = {"intake_version": draft["version"], "intake_status": draft["status"]}
            # Private preparation is not a published accounting source. Do not
            # widen visibility or invent an event to create a case.
        else:
            raise AccountingError("执行记录不存在或无权访问", 404)
        run = dict(run)
        basis = fingerprint({"kind": kind, "run_id": run_id, "firm_id": actor.firm_id,
                             "company_id": run["company_id"], "status": run["status"],
                             "input_fingerprint": run["input_fingerprint"], "error_code": run["error_code"],
                             "current": current, "target": target})
        return run, target, basis

    def preview(self, actor, kind, run_id):
        with self.db.transaction(actor=actor) as conn:
            run, target, basis = self._source(conn, actor, kind, run_id)
            eligible = False
            if target and run["status"] not in {"queued", "running", "retry_wait"}:
                case_scope = {"id": "", "company_id": run["company_id"], "event_id": target.get("event_id"),
                              "book_id": target.get("book_id"), "draft_id": target.get("draft_id"), "source_id": None}
                try:
                    eligible = self.cases._manage(conn, actor, case_scope)
                    if eligible:
                        self.cases._editable(conn, actor, case_scope)
                except AccountingError as error:
                    if error.status not in {403, 404, 409}:
                        raise
                    eligible = False
        if kind == "chat":
            record = self.assistant.execution(actor, run["conversation_id"], run_id)["execution"]
        elif kind == "intake":
            record = self.intake.run_execution(actor, run["intake_id"], run_id)
        else:
            record = self.intelligence.run_execution(actor, run["event_id"], run_id)
        # The APIs return a record, not a mutable task or a replay permission.
        record = dict(record)
        record["handoff"] = {**record["handoff"], "fingerprint": basis,
                             "supported": bool(target), "can_create": eligible,
                             "message": "可由有当前来源复核权限的会计创建内部异常任务。" if target else "请先在业务事项中确认资料并联系会计；此范围没有可发布的单一来源。"}
        return record

    def create(self, actor, kind, run_id, payload):
        if not isinstance(payload, HandoffRequest):
            raise AccountingError("人工接手请求无效", 422)
        with self.db.transaction(actor=actor) as conn:
            run, target, basis = self._source(conn, actor, kind, run_id)
        if not target:
            raise AccountingError("当前执行没有可接手的单一已发布来源，请联系会计", 409)
        if run["status"] in {"queued", "running", "retry_wait"}:
            raise AccountingError("任务仍在处理，请先使用原任务的取消操作并核对状态", 409)
        if basis != payload.expected_fingerprint:
            raise AccountingError("执行或资料已变化，请重新查看后确认接手", 409)

        def guard(conn):
            current_run, current_target, current_basis = self._source(conn, actor, kind, run_id)
            if current_basis != payload.expected_fingerprint or current_target != target or current_run["company_id"] != run["company_id"]:
                raise AccountingError("执行或资料已变化，人工任务未创建", 409)

        # Same user key reused for a different run/reason remains a conflict in
        # case_commands. Do not incorporate run_id into this key.
        key = "ak04:" + hashlib.sha256(payload.idempotency_key.encode()).hexdigest()
        request = CaseCreate(**target, kind="exception", title="AI 执行人工核对",
                             reason=f"执行引用 {kind}:{run_id}；资料指纹 {basis}。人工依据：{payload.reason}",
                             idempotency_key=key)
        case = self.cases.create(actor, request, source_guard=guard)
        return {"execution_id": f"{kind}:{run_id}", "case": case, "automatic_retry": False,
                "message": "已创建人工核对任务；凭证仍须会计独立复核后导出。"}
