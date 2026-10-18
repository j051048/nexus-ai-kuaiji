"""Bounded assistant tools: immutable previews, live authorization and atomic receipts.

The model may suggest a tool name and explanation. It never selects identifiers,
approves accounting work, edits business facts or invokes arbitrary endpoints.
"""

from datetime import datetime, timedelta, timezone
import json
from typing import Literal
from uuid import uuid4

from pydantic import Field, StrictStr, field_validator

from ..accounting.domain import AccountingError
from ..accounting.models import InputModel
from ..accounting.queries import inactive_source_sql
from ..accounting.service import fingerprint
from ..intelligence.current_evidence import current_attachments
from ..jobs.requests import BatchItem, BatchRequest
from ..jobs.visibility import event_scope


ActionKind = Literal["ocr_batch", "proposal_batch", "bank_drafts"]
LABELS = {
    "ocr_batch": "识别当前范围的新票据",
    "proposal_batch": "生成业务事项分录建议",
    "bank_drafts": "生成流水凭证草稿",
}
MAX_ITEMS = 100


class ActionPrepare(InputModel):
    kind: ActionKind
    reason: StrictStr = Field(min_length=3, max_length=1000)
    idempotency_key: StrictStr = Field(min_length=8, max_length=120)

    @field_validator("reason")
    @classmethod
    def meaningful(cls, value):
        if len(value.strip()) < 3:
            raise ValueError("操作依据至少三个字符")
        return value.strip()

    @field_validator("idempotency_key")
    @classmethod
    def key(cls, value):
        if len(value.strip()) < 8:
            raise ValueError("操作请求键至少八个字符")
        return value.strip()


class ActionConfirm(InputModel):
    fingerprint: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    confirmed: bool = Field(strict=True)
    idempotency_key: StrictStr = Field(min_length=8, max_length=120)

    @field_validator("idempotency_key")
    @classmethod
    def meaningful(cls, value):
        if len(value.strip()) < 8:
            raise ValueError("操作请求键至少八个字符")
        return value.strip()

    @field_validator("confirmed")
    @classmethod
    def explicit_confirmation(cls, value):
        if value is not True:
            raise ValueError("操作必须由用户明确确认")
        return value


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class AssistantActionService:
    def __init__(self, db, conversations, queue, ledger):
        self.db, self.conversations, self.queue, self.ledger = db, conversations, queue, ledger
        self.access, self.business, self.intelligence = (
            ledger.access,
            queue.business,
            queue.intelligence,
        )

    def _conversation(self, conn, actor, conversation_id):
        return self.conversations._conversation(conn, actor, conversation_id)

    def _stamp(self, conn):
        return self.queue.database_timestamp(conn)

    @staticmethod
    def _scope(conversation):
        return {
            key: conversation.get(key)
            for key in ("company_id", "book_ids", "event_ids", "draft_id")
        }

    @staticmethod
    def _blocked(code, message):
        return {"code": code, "message": message}

    def _events(self, conn, actor, conversation):
        scope, params = event_scope(actor)
        scope += " AND e.company_id=?"
        params.append(conversation["company_id"])
        events, books = conversation.get("event_ids", []), conversation.get("book_ids", [])
        if events:
            scope += " AND e.id IN (" + ",".join("?" for _ in events) + ")"
            params.extend(events)
        if books:
            scope += (
                " AND EXISTS(SELECT 1 FROM business_mappings bm WHERE bm.event_id=e.id AND bm.book_id IN ("
                + ",".join("?" for _ in books)
                + "))"
            )
            params.extend(books)
        if conversation.get("draft_id"):
            scope += " AND EXISTS(SELECT 1 FROM business_mappings bm WHERE bm.event_id=e.id AND bm.draft_id=?)"
            params.append(conversation["draft_id"])
        # Bounded server selection, ordered deterministically before snapshot.
        return conn.execute(
            "SELECT e.* FROM business_events e WHERE "
            + scope
            + " ORDER BY e.updated_at,e.id LIMIT 201",
            params,
        ).fetchall()

    def _book_basis(self, conn, actor, book_id, kind):
        book = self.access.book(conn, actor, book_id, "read" if kind == "ocr_batch" else "edit")
        self.ledger._open(book)
        return {
            "id": book_id,
            "period": book["period"],
            "period_status": book["period_status"],
            "accounts": [
                dict(row)
                for row in conn.execute(
                    "SELECT code,name,active,version,auxiliary_requirements FROM accounts WHERE book_id=? ORDER BY code",
                    (book_id,),
                )
            ],
            "rules": [
                dict(row)
                for row in conn.execute(
                    "SELECT r.id,r.status,r.state_version,r.current_version,v.definition_fingerprint FROM learning_rules r JOIN learning_rule_versions v ON v.rule_id=r.id AND v.version=r.current_version WHERE r.book_id=? ORDER BY r.id",
                    (book_id,),
                )
            ],
            "templates": [
                dict(row)
                for row in conn.execute(
                    "SELECT t.id,t.active,t.current_version,v.definition_fingerprint FROM journal_templates t JOIN journal_template_versions v ON v.template_id=t.id AND v.version=t.current_version WHERE t.book_id=? ORDER BY t.id",
                    (book_id,),
                )
            ],
            "migration_rules": [
                dict(row)
                for row in conn.execute(
                    "SELECT * FROM accounting.migration_learning_public_rules(?) ORDER BY id",
                    (book_id,),
                )
            ]
            if self.db.is_postgres
            else [
                dict(row)
                for row in conn.execute(
                    "SELECT r.id,r.version,r.definition_json FROM migration_learning_rules r JOIN migration_projects p ON p.id=r.project_id WHERE r.book_id=? AND p.status='active' AND NOT EXISTS(SELECT 1 FROM migration_learning_retirements x WHERE x.rule_id=r.id) ORDER BY r.id",
                    (book_id,),
                )
            ],
        }

    def _snapshot(self, conn, actor, conversation, kind):
        blocked, items, books_basis = [], [], []
        company_id = conversation.get("company_id")
        if not company_id:
            return {
                "kind": kind,
                "scope": self._scope(conversation),
                "scope_version": conversation["scope_version"],
                "items": [],
                "books": [],
                "blocked": [
                    self._blocked("company_required", "请先明确选择一家客户企业，再准备后台任务")
                ],
            }
        self.access.company(conn, actor, company_id)
        selected_books = conversation.get("book_ids", [])
        if not selected_books and not conversation.get("event_ids"):
            blocked.append(
                self._blocked(
                    "scope_required",
                    "请明确选择账套或业务事项；公司概览对话不会自动扩大到整家企业的资料",
                )
            )
        if kind in {"proposal_batch", "bank_drafts"} and not selected_books:
            blocked.append(
                self._blocked("book_required", "请在对话范围中明确选择账套；助手不会自行切换账套")
            )
        if kind == "bank_drafts" and (
            conversation.get("event_ids") or conversation.get("draft_id")
        ):
            blocked.append(
                self._blocked(
                    "bank_scope_required",
                    "当前范围限定了事项或凭证，请选择账套范围后生成银行流水草稿",
                )
            )
        if blocked:
            return {
                "kind": kind,
                "scope": self._scope(conversation),
                "scope_version": conversation["scope_version"],
                "items": [],
                "books": [],
                "blocked": blocked,
            }
        for book_id in selected_books:
            try:
                books_basis.append(self._book_basis(conn, actor, book_id, kind))
            except AccountingError as error:
                if error.status not in {403, 409}:
                    raise
                blocked.append(self._blocked("book_unavailable", str(error)))
        allowed_books = {book["id"] for book in books_basis}
        if kind == "bank_drafts":
            for book_id in sorted(allowed_books):
                remaining = MAX_ITEMS + 1 - len(items)
                if remaining <= 0:
                    break
                rows = conn.execute(
                    "SELECT s.* FROM sources s LEFT JOIN drafts d ON d.source_id=s.id WHERE s.book_id=? AND s.source_kind='bank' AND d.id IS NULL AND NOT "
                    + inactive_source_sql(self.db.dialect)
                    + " ORDER BY s.date,s.id LIMIT ?",
                    (book_id, remaining),
                ).fetchall()
                for source in rows:
                    if self.ledger._source_state_issues(conn, source):
                        blocked.append(
                            self._blocked(
                                "source_attention", "部分流水存在事实修订或重复问题，请先处理异常"
                            )
                        )
                        continue
                    if conn.execute(
                        "SELECT 1 FROM ai_bank_links WHERE bank_source_id=?", (source["id"],)
                    ).fetchone():
                        blocked.append(
                            self._blocked(
                                "linked_bank", "部分流水已关联业务事项，须在关联事项中处理"
                            )
                        )
                        continue
                    items.append(
                        {
                            "title": source["description"],
                            "book_id": book_id,
                            "source_id": source["id"],
                            "source": dict(source),
                            "evidence": [
                                dict(row)
                                for row in conn.execute(
                                    "SELECT id,evidence_ref,created_at,created_by FROM source_evidence WHERE source_id=? ORDER BY id",
                                    (source["id"],),
                                )
                            ],
                        }
                    )
        else:
            if not self.intelligence.provider.settings.extraction_available:
                blocked.append(
                    self._blocked("ocr_unavailable", "尚未配置票据识别服务，请先配置模型供应商")
                )
            else:
                events = self._events(conn, actor, conversation)
                if len(events) > 200:
                    blocked.append(
                        self._blocked(
                            "scope_limit",
                            "当前范围超过 200 个事项，请缩小范围；未选中的事项不会执行",
                        )
                    )
                for raw in events[:200]:
                    event = self.business._event(conn, actor, raw["id"])
                    try:
                        self.intelligence._require(
                            conn,
                            actor,
                            event,
                            "process" if kind == "ocr_batch" else "generate_proposal",
                            enabled=True,
                        )
                    except AccountingError as error:
                        if error.status not in {403, 409}:
                            raise
                        blocked.append(
                            self._blocked(
                                "event_unavailable",
                                "部分事项无处理权限、已冻结或企业未启用模型处理",
                            )
                        )
                        continue
                    mapping = self.intelligence._mapping(conn, event)
                    if selected_books and (not mapping or mapping["book_id"] not in allowed_books):
                        continue
                    if kind == "ocr_batch":
                        latest = self.intelligence._latest(conn, event)
                        attachments = current_attachments(
                            self.db,
                            conn,
                            event,
                            conn.execute(
                                "SELECT * FROM business_attachments WHERE event_id=? ORDER BY id",
                                (event["id"],),
                            ).fetchall(),
                        )
                        for attachment in attachments:
                            try:
                                ocr_key = self.intelligence._ocr_cache_key(
                                    conn, actor, event, attachment["media_type"]
                                )
                            except AccountingError as error:
                                if error.status not in {403, 409}:
                                    raise
                                blocked.append(
                                    self._blocked(
                                        "policy_required",
                                        "企业 AI 外发政策或可信端点不可用，请管理员核验",
                                    )
                                )
                                continue
                            digest = fingerprint(
                                {
                                    "attachment_id": attachment["id"],
                                    "sha256": attachment["sha256"],
                                    "config": ocr_key,
                                    "fact_version": event["fact_version"],
                                    "force_refresh": False,
                                }
                            )
                            current = latest.get(attachment["id"])
                            if (
                                current
                                and current["status"] == "succeeded"
                                and current["input_fingerprint"] == digest
                            ):
                                continue
                            if conn.execute(
                                "SELECT 1 FROM ocr_jobs WHERE event_id=? AND attachment_id=? AND status IN ('queued','running','retry_wait')",
                                (event["id"], attachment["id"]),
                            ).fetchone():
                                blocked.append(
                                    self._blocked(
                                        "already_running",
                                        "部分票据已有后台任务，可在任务进度中继续查看",
                                    )
                                )
                                continue
                            items.append(
                                {
                                    "title": attachment["filename"],
                                    "event_id": event["id"],
                                    "attachment_id": attachment["id"],
                                    "book_id": mapping["book_id"] if mapping else None,
                                    "expected_version": event["version"],
                                    "expected_fact_version": event["fact_version"],
                                    "attachment_sha256": attachment["sha256"],
                                    "event_payload": event["payload"],
                                    "event_status": event["status"],
                                }
                            )
                            if len(items) > MAX_ITEMS:
                                break
                    elif mapping:
                        event, book, draft = self.queue.proposals._input(
                            conn, actor, event["id"], mapping["book_id"], mapping["draft_id"]
                        )
                        attention = self.queue.proposals._attention(conn, actor, event, draft)
                        if attention:
                            blocked.append(self._blocked(attention[0], attention[1]))
                            continue
                        if conn.execute(
                            "SELECT 1 FROM proposal_jobs WHERE draft_id=?", (draft["id"],)
                        ).fetchone():
                            blocked.append(
                                self._blocked(
                                    "proposal_exists",
                                    "部分草稿已有分录任务；失败或需处理的任务请在后台运行页核验并重试",
                                )
                            )
                            continue
                        items.append(
                            {
                                "title": draft["description"],
                                "event_id": event["id"],
                                "book_id": book["id"],
                                "draft_id": draft["id"],
                                "expected_version": event["version"],
                                "expected_fact_version": event["fact_version"],
                                "expected_draft_version": draft["version"],
                                "event_payload": event["payload"],
                                "event_status": event["status"],
                                "draft": dict(draft),
                                "policy_fingerprint": self.queue.proposals.policy_fingerprint(
                                    conn, event, book["id"]
                                ),
                                "attachments": [
                                    dict(row)
                                    for row in current_attachments(
                                        self.db,
                                        conn,
                                        event,
                                        conn.execute(
                                            "SELECT * FROM business_attachments WHERE event_id=? ORDER BY id",
                                            (event["id"],),
                                        ).fetchall(),
                                    )
                                ],
                            }
                        )
                    if len(items) > MAX_ITEMS:
                        break
        if len(items) > MAX_ITEMS:
            items = items[:MAX_ITEMS]
            blocked.append(
                self._blocked("item_limit", "本次只处理按日期排序的前 100 项，剩余资料请另建预览")
            )
        if not items:
            blocked.append(
                self._blocked(
                    "no_eligible_items",
                    "当前明确范围没有可新建的任务；已完成、已在运行或需人工处理的资料不会重复提交",
                )
            )
        # Keep a bounded, de-duplicated explanation rather than leaking hidden totals.
        blocked = list({(row["code"], row["message"]): row for row in blocked}.values())
        context = self.conversations.snapshot_in_transaction(conn, actor, conversation)
        basis = (
            conn.execute(
                "SELECT accounting.assistant_input_basis(?)", (conversation["id"],)
            ).fetchone()[0]
            if self.db.is_postgres
            else context["input_fingerprint"]
        )
        return {
            "kind": kind,
            "scope": self._scope(conversation),
            "scope_version": conversation["scope_version"],
            "items": items,
            "books": books_basis,
            "blocked": blocked,
            "input_basis": basis,
            "configuration_fingerprint": self.queue.configuration_fingerprint()
            if kind == "ocr_batch"
            else self.queue.proposals.configuration_fingerprint()
            if kind == "proposal_batch"
            else "",
        }

    def _plan(self, conn, actor, conversation, plan_id):
        row = conn.execute(
            "SELECT * FROM assistant_action_plans WHERE id=? AND conversation_id=? AND firm_id=? AND created_by=?",
            (plan_id, conversation["id"], actor.firm_id, actor.user_id),
        ).fetchone()
        if not row:
            raise AccountingError("操作预览不存在或无权访问", 404)
        return row

    def _receipt_row(self, conn, plan):
        return conn.execute(
            "SELECT * FROM assistant_action_receipts WHERE plan_id=?", (plan["id"],)
        ).fetchone()

    def _receipt_json(self, conn, actor, row):
        result = json.loads(row["result_json"])
        # Progress is live. The immutable receipt records submission, not success.
        if result.get("batch_id"):
            batch = self.queue._batch_json(
                conn,
                actor,
                conn.execute(
                    "SELECT * FROM ocr_batches WHERE id=?", (result["batch_id"],)
                ).fetchone(),
            )
            result["jobs"] = [
                {
                    key: job[key]
                    for key in ("id", "event_id", "status", "error_code", "error_detail")
                }
                for job in batch["jobs"]
            ]
            result["progress"] = batch["progress"]
            result["status"] = batch["status"]
        elif result.get("jobs"):
            for job in result["jobs"]:
                try:
                    current = self.queue.proposals._job(conn, actor, job["id"])
                    job.update(
                        {key: current[key] for key in ("status", "error_code", "error_detail")}
                    )
                except AccountingError as error:
                    if error.status not in {403, 404}:
                        raise
                    job.update(
                        status="scope_changed",
                        error_code="scope_changed",
                        error_detail="当前权限不足以读取任务进度，历史回执仅记录当时提交",
                    )
        # Result links come from the proved receipt's actual drafts, never from
        # the page's previously selected book or model-supplied identifiers.
        result["draft_refs"] = []
        if result.get("draft_ids"):
            ids = result["draft_ids"]
            refs = {
                item["id"]: dict(item)
                for item in conn.execute(
                    "SELECT id,book_id FROM drafts WHERE id IN ("
                    + ",".join("?" for _ in ids)
                    + ")",
                    ids,
                )
            }
            result["draft_refs"] = [refs[identifier] for identifier in ids if identifier in refs]
        return {
            **result,
            "id": row["id"],
            "plan_id": row["plan_id"],
            "created_at": row["created_at"],
        }

    def _json(self, conn, actor, conversation, row, *, receipts=None, stamp=None):
        snapshot = json.loads(row["snapshot_json"])
        receipt = receipts.get(row["id"]) if receipts is not None else self._receipt_row(conn, row)
        state = (
            "executed"
            if receipt
            else "expired"
            if datetime.fromisoformat(row["expires_at"])
            <= datetime.fromisoformat(stamp or self._stamp(conn))
            else "ready"
            if row["item_count"]
            else "empty"
        )
        result = {
            "id": row["id"],
            "kind": row["kind"],
            "label": LABELS[row["kind"]],
            "state": state,
            "scope": self._scope(conversation),
            "fingerprint": row["fingerprint"],
            "count": row["item_count"],
            "reason": row["reason"],
            "created_at": row["created_at"],
            "expires_at": row["expires_at"],
            "requires_confirmation": True,
            "blocked": snapshot["blocked"],
            "items": [
                {
                    key: item[key]
                    for key in (
                        "title",
                        "book_id",
                        "event_id",
                        "attachment_id",
                        "source_id",
                        "draft_id",
                    )
                    if key in item
                }
                for item in snapshot["items"]
            ],
        }
        if receipt:
            result["receipt"] = self._receipt_json(conn, actor, receipt)
        return result

    def prepare(self, actor, conversation_id, request: ActionPrepare):
        with self.db.transaction(write=True, actor=actor) as conn:
            conversation = self._conversation(conn, actor, conversation_id)
            digest = fingerprint(
                {"conversation_id": conversation_id, "kind": request.kind, "reason": request.reason}
            )
            existing = conn.execute(
                "SELECT * FROM assistant_action_plans WHERE conversation_id=? AND created_by=? AND request_key=?",
                (conversation_id, actor.user_id, request.idempotency_key),
            ).fetchone()
            if existing:
                if existing["request_fingerprint"] != digest:
                    raise AccountingError("此预览请求键已用于不同操作", 409)
                return self._json(conn, actor, conversation, existing)
            snapshot = self._snapshot(conn, actor, conversation, request.kind)
            plan_id, stamp = uuid4().hex, self._stamp(conn)
            expiry = (datetime.fromisoformat(stamp) + timedelta(minutes=30)).isoformat()
            conn.execute(
                "INSERT INTO assistant_action_plans(id,conversation_id,firm_id,company_id,created_by,kind,snapshot_json,fingerprint,item_count,reason,request_key,request_fingerprint,created_at,expires_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    plan_id,
                    conversation_id,
                    actor.firm_id,
                    conversation["company_id"],
                    actor.user_id,
                    request.kind,
                    encode(snapshot),
                    fingerprint(snapshot),
                    len(snapshot["items"]),
                    request.reason,
                    request.idempotency_key,
                    digest,
                    stamp,
                    expiry,
                ),
            )
            self.business._audit(
                conn,
                actor,
                "assistant.action.preview",
                plan_id,
                {
                    "conversation_id": conversation_id,
                    "kind": request.kind,
                    "count": len(snapshot["items"]),
                },
            )
            return self._json(
                conn, actor, conversation, self._plan(conn, actor, conversation, plan_id)
            )

    def get(self, actor, conversation_id, plan_id):
        with self.db.transaction(actor=actor) as conn:
            conversation = self._conversation(conn, actor, conversation_id)
            return self._json(
                conn, actor, conversation, self._plan(conn, actor, conversation, plan_id)
            )

    @staticmethod
    def validate_page(limit, offset):
        if (
            type(limit) is not int
            or not 1 <= limit <= 20
            or type(offset) is not int
            or not 0 <= offset <= 100000
        ):
            raise AccountingError("操作历史每页 1–20 项，分页范围无效", 422)

    def list(self, actor, conversation_id, limit=20, offset=0):
        self.validate_page(limit, offset)
        with self.db.transaction(actor=actor) as conn:
            conversation = self._conversation(conn, actor, conversation_id)
            return self.list_in_transaction(conn, actor, conversation, limit, offset)

    def list_in_transaction(self, conn, actor, conversation, limit=20, offset=0):
        # Only reuse a conversation already authorized in this same transaction.
        # Receipt progress is still read live under that transaction's RLS scope.
        self.validate_page(limit, offset)
        conversation_id = conversation["id"]
        where = (
            " FROM assistant_action_plans WHERE conversation_id=? AND firm_id=? AND created_by=?"
        )
        args = (conversation_id, actor.firm_id, actor.user_id)
        total = conn.execute("SELECT COUNT(*)" + where, args).fetchone()[0]
        rows = conn.execute(
            "SELECT *" + where + " ORDER BY created_at DESC,id DESC LIMIT ? OFFSET ?",
            (*args, limit, offset),
        ).fetchall()
        # A page uses one database timestamp and one receipt read, rather
        # than two network round trips per unexecuted plan. Progress in an
        # executed receipt is still loaded live under current permissions.
        receipts, stamp = {}, self._stamp(conn) if rows else None
        if rows:
            receipts = {
                row["plan_id"]: row
                for row in conn.execute(
                    "SELECT * FROM assistant_action_receipts WHERE conversation_id=? "
                    "AND firm_id=? AND created_by=? AND plan_id IN ("
                    + ",".join("?" for _ in rows)
                    + ")",
                    (*args, *(row["id"] for row in rows)),
                )
            }
        return {
            "plans": [
                self._json(conn, actor, conversation, row, receipts=receipts, stamp=stamp)
                for row in rows
            ],
            "pagination": {
                "total": total,
                "limit": limit,
                "offset": offset,
                "has_more": offset + len(rows) < total,
                "next_offset": offset + len(rows) if offset + len(rows) < total else None,
            },
        }

    def confirm(self, actor, conversation_id, plan_id, request: ActionConfirm):
        with self.db.transaction(write=True, actor=actor) as conn:
            conversation = self._conversation(conn, actor, conversation_id)
            plan = self._plan(conn, actor, conversation, plan_id)
            if plan["fingerprint"] != request.fingerprint:
                raise AccountingError("确认指纹与该预览不一致，请重新核对操作范围", 409)
            prior = self._receipt_row(conn, plan)
            digest = fingerprint(
                {"plan_id": plan_id, "fingerprint": request.fingerprint, "confirmed": True}
            )
            if prior:
                if (
                    prior["request_key"] != request.idempotency_key
                    or prior["request_fingerprint"] != digest
                ):
                    raise AccountingError("该预览已经执行，请读取原回执；不能再次执行", 409)
                return self._receipt_json(conn, actor, prior)
            if datetime.fromisoformat(plan["expires_at"]) <= datetime.fromisoformat(
                self._stamp(conn)
            ):
                raise AccountingError("操作预览已超过 30 分钟，请重新生成并核对", 409)
            if not plan["item_count"]:
                raise AccountingError("当前范围没有可执行的候选，请查看预览中的处理原因", 409)
            current = self._snapshot(conn, actor, conversation, plan["kind"])
            if fingerprint(current) != plan["fingerprint"]:
                raise AccountingError("权限、资料、草稿、期间或模型配置已改变，请重新生成预览", 409)
            if self.db.is_postgres:
                conn.execute(
                    "SELECT accounting.assistant_begin_action(?,?,?)",
                    (plan_id, request.fingerprint, request.idempotency_key),
                )
            items, result = (
                current["items"],
                {
                    "kind": plan["kind"],
                    "count": plan["item_count"],
                    "jobs": [],
                    "draft_ids": [],
                    "created": 0,
                    "exceptions": 0,
                },
            )
            if plan["kind"] == "ocr_batch":
                batch = self.queue.enqueue_in_transaction(
                    conn,
                    actor,
                    BatchRequest(
                        company_id=conversation["company_id"],
                        items=[
                            BatchItem(
                                event_id=item["event_id"],
                                attachment_id=item["attachment_id"],
                                expected_version=item["expected_version"],
                            )
                            for item in items
                        ],
                        idempotency_key="assistant:" + plan_id,
                    ),
                )
                result.update(
                    batch_id=batch["id"],
                    jobs=[
                        {key: job[key] for key in ("id", "event_id", "status")}
                        for job in batch["jobs"]
                    ],
                )
            elif plan["kind"] == "proposal_batch":
                for item in items:
                    event, book, draft = self.queue.proposals._input(
                        conn, actor, item["event_id"], item["book_id"], item["draft_id"]
                    )
                    job = self.queue.proposals.enqueue_in_transaction(
                        conn, actor, event, book, draft
                    )
                    result["jobs"].append(
                        {key: job[key] for key in ("id", "event_id", "draft_id", "status")}
                    )
            else:
                for book_id in sorted({item["book_id"] for item in items}):
                    created = self.ledger.suggestions_in_transaction(
                        conn,
                        actor,
                        book_id,
                        source_ids=[
                            item["source_id"] for item in items if item["book_id"] == book_id
                        ],
                    )
                    result["created"] += created["created"]
                    result["exceptions"] += created["exceptions"]
                    result["draft_ids"].extend(created["draft_ids"])
            receipt_id, stamp = uuid4().hex, self._stamp(conn)
            conn.execute(
                "INSERT INTO assistant_action_receipts(id,plan_id,conversation_id,firm_id,company_id,created_by,request_key,request_fingerprint,result_json,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    receipt_id,
                    plan_id,
                    conversation_id,
                    actor.firm_id,
                    conversation["company_id"],
                    actor.user_id,
                    request.idempotency_key,
                    digest,
                    encode(result),
                    stamp,
                ),
            )
            self.business._audit(
                conn,
                actor,
                "assistant.action.confirm",
                plan_id,
                {
                    "conversation_id": conversation_id,
                    "kind": plan["kind"],
                    "count": plan["item_count"],
                    "receipt_id": receipt_id,
                },
            )
            return self._receipt_json(conn, actor, self._receipt_row(conn, plan))

    def receipt(self, actor, conversation_id, plan_id):
        with self.db.transaction(actor=actor) as conn:
            conversation = self._conversation(conn, actor, conversation_id)
            plan = self._plan(conn, actor, conversation, plan_id)
            row = self._receipt_row(conn, plan)
            if not row:
                raise AccountingError("该预览尚未执行，没有任务回执", 404)
            return self._receipt_json(conn, actor, row)
