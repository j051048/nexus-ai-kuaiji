"""Private, immutable scope conversations; deterministic evidence snapshots."""

import json
from uuid import uuid4

from app.accounting.domain import AccountingError
from app.accounting.models import cents_to_money, money_to_cents
from app.accounting.queries import page_bounds
from app.accounting.service import fingerprint, now
from app.jobs.visibility import event_scope
from .models import ConversationCreate, TurnCreate
from .scope import book_read_scope, conversation_scope


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


TURN_PUBLIC = "id,conversation_id,firm_id,company_id,created_by,question,status,provider,model,expected_scope_version,input_fingerprint,model_fingerprint,sent_at,error_code,error_detail,reply_json,created_at,updated_at,finished_at"


class AssistantService:
    def __init__(
        self, db, accounting, business, agent_workspace=None, provider=None, intelligence=None
    ):
        self.db, self.accounting, self.business = db, accounting, business
        self.access, self.agent_workspace, self.provider = (
            accounting.access,
            agent_workspace,
            provider,
        )
        self.intelligence = intelligence or getattr(accounting, "intelligence", None)

    def _timestamp(self, conn):
        # Dispatch and leases use PostgreSQL's clock, not a possibly different
        # Windows API host clock (important with hosted databases and workers).
        return (
            conn.execute(
                "SELECT to_char(clock_timestamp() AT TIME ZONE 'UTC','YYYY-MM-DD\"T\"HH24:MI:SS.US\"+00:00\"')"
            ).fetchone()[0]
            if self.db.is_postgres
            else now()
        )

    def _model_info(self):
        settings = getattr(self.provider, "settings", None)
        available = bool(getattr(self.provider, "available", False))
        provider = "openai_compatible"
        model = getattr(settings, "chat_model", "") if settings else ""
        method = getattr(self.provider, "configuration_fingerprint", None)
        digest = (
            method()
            if method
            else fingerprint({"provider": provider, "model": model, "available": available})
        )
        return {
            "model_available": available,
            "provider": provider,
            "model": model or "unconfigured",
            "model_fingerprint": digest,
            "notice": "仅发送明确范围的已保存摘要；任务执行需要单独预览确认，审核与导出仍由会计完成。"
            if available
            else "尚未配置会计对话模型；保存的资料与原流程仍可使用。",
        }

    def _availability(self, conn, actor, company_id):
        info = self._model_info()
        row = conn.execute(
            "SELECT enabled FROM ai_company_settings WHERE firm_id=? AND company_id=?",
            (actor.firm_id, company_id),
        ).fetchone()
        info["supplier_configured"] = info["model_available"]
        info["company_enabled"] = bool(row and row["enabled"])
        info["model_available"] = info["supplier_configured"] and info["company_enabled"]
        if info["supplier_configured"] and not info["company_enabled"]:
            info["notice"] = (
                "管理员已暂停该企业AI处理；不会向模型发送资料。请联系企业或事务所管理员核对用量后恢复。"
            )
        return info

    @staticmethod
    def _conversation_json(row):
        return {
            key: row[key]
            for key in (
                "id",
                "firm_id",
                "company_id",
                "created_by",
                "title",
                "scope_version",
                "draft_id",
                "created_at",
                "updated_at",
            )
        } | {
            "book_ids": json.loads(row["book_ids_json"]),
            "event_ids": json.loads(row["event_ids_json"]),
        }

    def _conversation(self, conn, actor, conversation_id):
        self.access.require_user(conn, actor.user_id)
        scope, args = conversation_scope(self.db, actor)
        row = conn.execute(
            "SELECT c.* FROM assistant_conversations c WHERE c.id=? AND " + scope,
            [conversation_id, *args],
        ).fetchone()
        if not row:
            raise AccountingError("对话不存在或当前范围已失去授权", 404)
        # The predicate checks the owner and *every* current scope reference,
        # including revoked memberships. Do not repeat the same per-reference
        # authorization queries just to read immutable history or a plan list.
        # Snapshot reads independently load and authorize the current records.
        return self._conversation_json(row)

    def _check_refs(self, conn, actor, conversation):
        company = self.access.company(conn, actor, conversation["company_id"])
        books = {}
        if conversation["book_ids"]:
            scope, params = book_read_scope(actor)
            rows = conn.execute(
                "SELECT b.*,p.status AS period_status FROM books b "
                "JOIN periods p ON p.book_id=b.id AND p.period=b.period "
                "WHERE b.company_id=? AND b.id IN ("
                + ",".join("?" for _ in conversation["book_ids"])
                + ") AND "
                + scope,
                [conversation["company_id"], *conversation["book_ids"], *params],
            ).fetchall()
            books = {row["id"]: row for row in rows}
            if set(books) != set(conversation["book_ids"]):
                raise AccountingError("对话账套不存在或无权访问", 404)
        events = {}
        for event_id in conversation["event_ids"]:
            event = self.business._event(conn, actor, event_id)
            if event["company_id"] != conversation["company_id"]:
                raise AccountingError("对话事项不属于当前企业", 404)
            events[event_id] = event
        draft = None
        if conversation.get("draft_id"):
            draft = conn.execute(
                "SELECT * FROM drafts WHERE id=? AND book_id=?",
                (conversation["draft_id"], conversation["book_ids"][0]),
            ).fetchone()
            if not draft:
                raise AccountingError("对话凭证不存在或无权访问", 404)
        # These rows are reused only by this immediate snapshot construction,
        # never stored on the service/actor or reused after a write or request.
        return company, books, events, draft

    def choices(self, actor, company_id, event_q="", limit=20, offset=0):
        page_bounds(limit, offset)
        if len(event_q) > 200:
            raise AccountingError("事项搜索文字过长")
        with self.db.transaction(actor=actor) as conn:
            company = self.access.company(conn, actor, company_id)
            books, params = book_read_scope(actor)
            rows = conn.execute(
                "SELECT b.id,b.name,b.period,p.status AS period_status FROM books b JOIN periods p ON p.book_id=b.id AND p.period=b.period WHERE b.company_id=? AND "
                + books
                + " ORDER BY b.period DESC,b.id LIMIT 100",
                [company_id, *params],
            ).fetchall()
            events, params = event_scope(actor)
            predicate = " FROM business_events e WHERE e.company_id=? AND " + events
            args = [company_id, *params]
            if event_q.strip():
                text = event_q.strip().replace("!", "!!").replace("%", "!%").replace("_", "!_")
                predicate += " AND e.payload LIKE ? ESCAPE '!'"
                args.append("%" + text + "%")
            total = conn.execute("SELECT COUNT(*)" + predicate, args).fetchone()[0]
            items = conn.execute(
                "SELECT e.id,e.payload,e.status"
                + predicate
                + " ORDER BY e.updated_at DESC,e.id LIMIT ? OFFSET ?",
                [*args, limit, offset],
            ).fetchall()
            return {
                "company_id": company_id,
                "company_name": company["name"],
                "books": [dict(r) for r in rows],
                "events": [
                    {
                        "id": r["id"],
                        "title": json.loads(r["payload"]).get("description", "业务事项"),
                        "amount": cents_to_money(
                            money_to_cents(json.loads(r["payload"])["amount"])
                        ),
                        "status": r["status"],
                    }
                    for r in items
                ],
                "pagination": self._pagination(total, limit, offset, len(items)),
                **self._availability(conn, actor, company_id),
            }

    @staticmethod
    def _pagination(total, limit, offset, size):
        return {"total": total, "limit": limit, "offset": offset, "has_more": offset + size < total}

    def create(self, actor, body: ConversationCreate):
        command = body.model_dump()
        digest = fingerprint(command)
        with self.db.transaction(write=True, actor=actor) as conn:
            scope = {
                "company_id": body.company_id,
                "book_ids": body.book_ids,
                "event_ids": body.event_ids,
                "draft_id": body.draft_id,
            }
            self._check_refs(conn, actor, scope)
            old = conn.execute(
                "SELECT id,create_fingerprint FROM assistant_conversations WHERE firm_id=? AND created_by=? AND create_key=?",
                (actor.firm_id, actor.user_id, body.idempotency_key),
            ).fetchone()
            if old:
                if old["create_fingerprint"] != digest:
                    raise AccountingError("同一创建请求对应不同范围", 409)
                return {
                    "conversation": self._conversation(conn, actor, old["id"]),
                    **self._availability(conn, actor, body.company_id),
                }
            identifier, stamp = uuid4().hex, self._timestamp(conn)
            conn.execute(
                "INSERT INTO assistant_conversations(id,firm_id,company_id,created_by,scope_version,book_ids_json,event_ids_json,draft_id,title,create_key,create_fingerprint,created_at,updated_at) VALUES(?,?,?,?,1,?,?,?,?,?,?,?,?)",
                (
                    identifier,
                    actor.firm_id,
                    body.company_id,
                    actor.user_id,
                    encode(body.book_ids),
                    encode(body.event_ids),
                    body.draft_id,
                    body.title,
                    body.idempotency_key,
                    digest,
                    stamp,
                    stamp,
                ),
            )
            return {
                "conversation": self._conversation(conn, actor, identifier),
                **self._availability(conn, actor, body.company_id),
            }

    def list(self, actor, company_id, limit=20, offset=0):
        page_bounds(limit, offset)
        with self.db.transaction(actor=actor) as conn:
            self.access.company(conn, actor, company_id)
            scope, params = conversation_scope(self.db, actor)
            where = " FROM assistant_conversations c WHERE c.company_id=? AND " + scope
            args = [company_id, *params]
            total = conn.execute("SELECT COUNT(*)" + where, args).fetchone()[0]
            rows = conn.execute(
                "SELECT c.*" + where + " ORDER BY c.created_at DESC,c.id LIMIT ? OFFSET ?",
                [*args, limit, offset],
            ).fetchall()
            return {
                "conversations": [self._conversation_json(r) for r in rows],
                "pagination": self._pagination(total, limit, offset, len(rows)),
                **self._availability(conn, actor, company_id),
            }

    def snapshot_in_transaction(self, conn, actor, conversation):
        company, books, events, draft_row = self._check_refs(conn, actor, conversation)
        context = {
            "company_id": company["id"],
            "company_name": company["name"],
            "scope_version": 1,
            "books": [],
            "events": [],
            "draft": None,
            "evidence": [],
            "notice": "资料是当前已保存事实的摘要，附带状态和版本。未提供的数据不得推断，金额计算由后台确定性程序完成。",
        }
        basis = {"books": [], "events": [], "draft": None}
        draft_counts, source_totals = {}, {}
        if books:
            placeholders = ",".join("?" for _ in books)
            for row in conn.execute(
                "SELECT book_id,status,COUNT(*) AS total FROM drafts WHERE book_id IN ("
                + placeholders
                + ") GROUP BY book_id,status ORDER BY book_id,status",
                list(books),
            ):
                draft_counts.setdefault(row["book_id"], {})[row["status"]] = row["total"]
            source_totals = {
                row["book_id"]: row
                for row in conn.execute(
                    "SELECT book_id,COUNT(*) AS total,COALESCE(SUM(amount_cents),0) AS amount "
                    "FROM sources WHERE book_id IN (" + placeholders + ") GROUP BY book_id",
                    list(books),
                )
            }
        for book_id in conversation["book_ids"]:
            book = books[book_id]
            sources = source_totals.get(book_id, {"total": 0, "amount": 0})
            item = {
                "id": book_id,
                "name": book["name"],
                "period": book["period"],
                "period_status": book["period_status"],
                "draft_counts": draft_counts.get(book_id, {}),
                "source_count": sources["total"],
                "source_gross_total": cents_to_money(sources["amount"]),
            }
            context["books"].append(item)
            basis["books"].append(item)
            context["evidence"].append(
                {
                    "id": "book:" + book_id,
                    "kind": "book",
                    "label": book["name"] + " · " + book["period"],
                    "detail": "当前账套保留的原始来源数量与绝对金额摘要（含隔离及被替代原件）；仅表示资料量，不代表有效交易额、收入或利润。",
                    "book_id": book_id,
                }
            )
        for event_id in conversation["event_ids"]:
            event = events[event_id]
            payload = json.loads(event["payload"])
            item = {
                "id": event_id,
                "status": event["status"],
                "version": event["version"],
                "fact_version": event["fact_version"],
                "amount": cents_to_money(money_to_cents(payload["amount"])),
                "description": payload.get("description", ""),
                "occurred_on": payload.get("occurred_on", payload.get("date")),
                "counterparty": payload.get("counterparty", ""),
                "template_code": event["template_id"],
            }
            context["events"].append(item)
            evidence_id = f"event:{event_id}:v{event['fact_version']}"
            context["evidence"].append(
                {
                    "id": evidence_id,
                    "kind": "event",
                    "label": item["description"] or "业务事项",
                    "detail": f"当前保存金额 {item['amount']} 元；流程状态 {event['status']}",
                    "event_id": event_id,
                }
            )
            from app.intelligence.current_evidence import superseded_ids

            superseded = superseded_ids(self.db, conn, event)
            attachments = [
                r for r in self.business._attachments(conn, event_id) if r["id"] not in superseded
            ]
            # Only identifiers and hashes of current files, never full raw text,
            # paths, bytes, employee bank details or unrelated event histories.
            for attachment in attachments[:20]:
                context["evidence"].append(
                    {
                        "id": "attachment:" + attachment["id"],
                        "kind": "attachment",
                        "label": attachment["filename"],
                        "detail": "当前上传原件，模型没有收到文件内容；须经授权识别后核验。",
                        "event_id": event_id,
                        "attachment_id": attachment["id"],
                    }
                )
            assessment_fingerprint = None
            if self.intelligence:
                latest = self.intelligence._latest(conn, event, excluded=superseded)
                documents = []
                for attachment in attachments[:20]:
                    run = latest.get(attachment["id"])
                    if not run or run["status"] != "succeeded" or not run["result_json"]:
                        continue
                    for index, document in enumerate(json.loads(run["result_json"])["documents"]):
                        # Extracted labels and exact money strings are evidence,
                        # not verified tax eligibility or approved accounting.
                        documents.append(
                            {
                                "attachment_id": attachment["id"],
                                "extraction_id": run["id"],
                                "document_index": index,
                                **{
                                    key: document.get(key)
                                    for key in (
                                        "document_type",
                                        "invoice_number",
                                        "issued_on",
                                        "seller_name",
                                        "buyer_name",
                                        "gross_amount",
                                        "net_amount",
                                        "tax_amount",
                                        "currency",
                                    )
                                },
                            }
                        )
                item["documents"] = documents[:20]
                item["documents_truncated"] = len(documents) > 20 or len(attachments) > 20
                assessment = self.intelligence._assessment(
                    conn, actor, event, persist=False, latest=latest, attachments=attachments
                )
                assessment_fingerprint = assessment["fingerprint"]
                item["checks"] = [
                    {
                        "code": finding["code"],
                        "severity": finding["severity"],
                        "message": finding["payload"].get("message", "当前资料仍需人工核对"),
                        "resolved": bool(finding.get("resolved", False)),
                    }
                    for finding in assessment["findings"]
                    if finding["severity"] == "blocking"
                ][:30]
                by_attachment = {row["attachment_id"]: row for row in documents}
                for evidence in context["evidence"]:
                    if (
                        evidence.get("event_id") == event_id
                        and evidence.get("attachment_id") in by_attachment
                    ):
                        row = by_attachment[evidence["attachment_id"]]
                        evidence["detail"] = (
                            "已保存的 OCR 结构摘要（需人工核验）：价税合计 "
                            + str(row["gross_amount"])
                            + " 元，税额 "
                            + str(row["tax_amount"])
                            + " 元；未发送原件或识别全文。"
                        )
            basis["events"].append(
                {
                    "id": event_id,
                    "payload": payload,
                    "status": event["status"],
                    "version": event["version"],
                    "fact_version": event["fact_version"],
                    "attachments": [{"id": a["id"], "sha256": a["sha256"]} for a in attachments],
                    "assessment": assessment_fingerprint,
                }
            )
        if conversation["draft_id"]:
            book_id, draft_id = conversation["book_ids"][0], conversation["draft_id"]
            draft = self.accounting._draft_json(conn, draft_row, actor)
            context["draft"] = {
                key: draft[key]
                for key in (
                    "id",
                    "description",
                    "status",
                    "version",
                    "explanation",
                    "issues",
                    "lines",
                    "source_amount",
                )
            }
            context["evidence"].append(
                {
                    "id": f"draft:{draft_id}:v{draft['version']}",
                    "kind": "draft",
                    "label": "当前凭证分录",
                    "detail": draft["explanation"] or "未保存生成依据",
                    "book_id": book_id,
                    "draft_id": draft_id,
                }
            )
            basis["draft"] = context["draft"]
        context["input_fingerprint"] = (
            conn.execute(
                "SELECT accounting.assistant_input_basis(?)", (conversation["id"],)
            ).fetchone()[0]
            if self.db.is_postgres
            else fingerprint(basis)
        )
        if len(encode(context).encode()) > 65536:
            raise AccountingError("资料摘要超出单次对话上限，请减少事项范围", 422)
        return context

    def detail(self, actor, conversation_id):
        with self.db.transaction(actor=actor) as conn:
            conversation = self._conversation(conn, actor, conversation_id)
            return self._detail_in_transaction(conn, actor, conversation)

    def _detail_in_transaction(self, conn, actor, conversation):
        conversation_id = conversation["id"]
        context = self.snapshot_in_transaction(conn, actor, conversation)
        turns = conn.execute(
            "SELECT "
            + TURN_PUBLIC
            + " FROM assistant_turns WHERE conversation_id=? ORDER BY created_at DESC,id LIMIT 20",
            (conversation_id,),
        ).fetchall()
        return {
            "conversation": conversation,
            "context": context,
            "turns": [self._turn_json(r) for r in turns],
            **self._availability(conn, actor, conversation["company_id"]),
        }

    @staticmethod
    def _turn_json(row):
        data = {
            key: row[key]
            for key in (
                "id",
                "conversation_id",
                "question",
                "status",
                "provider",
                "model",
                "expected_scope_version",
                "input_fingerprint",
                "sent_at",
                "error_code",
                "error_detail",
                "created_at",
                "updated_at",
                "finished_at",
            )
        }
        data["reply"] = json.loads(row["reply_json"]) if row["reply_json"] else None
        data["mode"] = "model" if data["reply"] else None
        return data

    def _history(self, conn, conversation_id, exclude_turn_id=None):
        rows = conn.execute(
            "SELECT role,content_json FROM assistant_messages WHERE conversation_id=? AND (CAST(? AS TEXT) IS NULL OR turn_id<>?) ORDER BY created_at DESC,id DESC LIMIT 12",
            (conversation_id, exclude_turn_id, exclude_turn_id),
        ).fetchall()
        history = [
            {"role": r["role"], "content": json.loads(r["content_json"])} for r in reversed(rows)
        ]
        # PostgreSQL's JSONB text includes separator spaces. Use the same
        # conservative size when trimming, so native history proof agrees.
        while (
            history
            and len(json.dumps(history, ensure_ascii=False, sort_keys=True).encode()) > 49152
        ):
            history.pop(0)
        return history

    def messages(self, actor, conversation_id, limit=50, offset=0):
        page_bounds(limit, offset)
        with self.db.transaction(actor=actor) as conn:
            self._conversation(conn, actor, conversation_id)
            return self._messages_in_transaction(conn, conversation_id, limit, offset)

    def _messages_in_transaction(self, conn, conversation_id, limit, offset):
        total = conn.execute(
            "SELECT COUNT(*) FROM assistant_messages WHERE conversation_id=?",
            (conversation_id,),
        ).fetchone()[0]
        rows = conn.execute(
            "SELECT m.id,m.turn_id,m.role,m.content_json,m.created_at,t.input_fingerprint,t.snapshot_json FROM assistant_messages m JOIN assistant_turns t ON t.id=m.turn_id AND t.conversation_id=m.conversation_id WHERE m.conversation_id=? ORDER BY m.created_at DESC,m.id DESC LIMIT ? OFFSET ?",
            (conversation_id, limit, offset),
        ).fetchall()
        messages = []
        for r in reversed(rows):
            content = json.loads(r["content_json"])
            references = set(content.get("citation_ids", []))
            citations = [
                item
                for item in json.loads(r["snapshot_json"])["evidence"]
                if item["id"] in references
            ]
            messages.append(
                {
                    key: r[key]
                    for key in ("id", "turn_id", "role", "created_at", "input_fingerprint")
                }
                | {"content": content, "citations": citations}
            )
        return {
            "messages": messages,
            "pagination": self._pagination(total, limit, offset, len(rows)),
        }

    def workspace(
        self,
        actor,
        conversation_id,
        actions,
        *,
        message_limit=100,
        message_offset=0,
        action_limit=20,
        action_offset=0,
    ):
        """One live authorization and read snapshot for the visible conversation.

        The authorized conversation is request-local. It is never retained on
        the service or reused after this transaction, a write, or a scope change.
        Existing individual endpoints continue to authorize independently.
        """
        page_bounds(message_limit, message_offset)
        actions.validate_page(action_limit, action_offset)
        with self.db.transaction(actor=actor) as conn:
            conversation = self._conversation(conn, actor, conversation_id)
            return {
                "detail": self._detail_in_transaction(conn, actor, conversation),
                "messages": self._messages_in_transaction(
                    conn, conversation_id, message_limit, message_offset
                ),
                "actions": actions.list_in_transaction(
                    conn, actor, conversation, action_limit, action_offset
                ),
            }

    def enqueue(self, actor, conversation_id, body: TurnCreate):
        request_fp = fingerprint(body.model_dump())
        with self.db.transaction(write=True, actor=actor) as conn:
            conversation = self._conversation(conn, actor, conversation_id)
            prior = conn.execute(
                "SELECT "
                + TURN_PUBLIC
                + ",request_fingerprint FROM assistant_turns WHERE conversation_id=? AND request_key=?",
                (conversation_id, body.idempotency_key),
            ).fetchone()
            if prior:
                if prior["request_fingerprint"] != request_fp:
                    raise AccountingError("同一消息请求对应不同内容", 409)
                return {"turn": self._turn_json(prior)}
            if conn.execute(
                "SELECT 1 FROM assistant_turns WHERE conversation_id=? AND status IN ('queued','running')",
                (conversation_id,),
            ).fetchone():
                raise AccountingError("当前对话仍在处理上一条消息", 409)
            context = self.snapshot_in_transaction(conn, actor, conversation)
            history = self._history(conn, conversation_id)
            info = self._availability(conn, actor, conversation["company_id"])
            if not info["model_available"]:
                raise AccountingError(info["notice"], 409)
            identifier, stamp = uuid4().hex, self._timestamp(conn)
            conn.execute(
                "INSERT INTO assistant_turns(id,conversation_id,firm_id,company_id,created_by,provider,model,expected_scope_version,question,input_fingerprint,model_fingerprint,snapshot_json,history_json,status,available_at,request_key,request_fingerprint,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,'queued',?,?,?,?,?)",
                (
                    identifier,
                    conversation_id,
                    actor.firm_id,
                    conversation["company_id"],
                    actor.user_id,
                    info["provider"],
                    info["model"],
                    body.expected_scope_version,
                    body.question,
                    context["input_fingerprint"],
                    info["model_fingerprint"],
                    encode(context),
                    encode(history),
                    stamp,
                    body.idempotency_key,
                    request_fp,
                    stamp,
                    stamp,
                ),
            )
            conn.execute(
                "INSERT INTO assistant_messages(id,conversation_id,turn_id,role,content_json,created_at) VALUES(?,?,?,'user',?,?)",
                (
                    uuid4().hex,
                    conversation_id,
                    identifier,
                    encode({"question": body.question}),
                    stamp,
                ),
            )
            return {
                "turn": self._turn_json(
                    conn.execute(
                        "SELECT " + TURN_PUBLIC + " FROM assistant_turns WHERE id=?", (identifier,)
                    ).fetchone()
                )
            }

    def turn(self, actor, conversation_id, turn_id):
        with self.db.transaction(actor=actor) as conn:
            self._conversation(conn, actor, conversation_id)
            row = conn.execute(
                "SELECT " + TURN_PUBLIC + " FROM assistant_turns WHERE id=? AND conversation_id=?",
                (turn_id, conversation_id),
            ).fetchone()
            if not row:
                raise AccountingError("对话任务不存在", 404)
            return {"turn": self._turn_json(row)}

    def cancel(self, actor, conversation_id, turn_id, body):
        with self.db.transaction(write=True, actor=actor) as conn:
            self._conversation(conn, actor, conversation_id)
            row = conn.execute(
                "SELECT " + TURN_PUBLIC + " FROM assistant_turns WHERE id=? AND conversation_id=?",
                (turn_id, conversation_id),
            ).fetchone()
            if not row:
                raise AccountingError("对话任务不存在", 404)
            if row["status"] in {"queued", "running"}:
                if self.db.is_postgres:
                    conn.execute("SELECT accounting.assistant_cancel(?,?)", (turn_id, body.reason))
                else:
                    stamp = now()
                    conn.execute(
                        "UPDATE assistant_turns SET status='cancelled',lease_token=NULL,lease_until=NULL,worker_id=NULL,error_code='user_cancelled',error_detail=?,updated_at=?,finished_at=? WHERE id=?",
                        (body.reason, stamp, stamp, turn_id),
                    )
            return {
                "turn": self._turn_json(
                    conn.execute(
                        "SELECT " + TURN_PUBLIC + " FROM assistant_turns WHERE id=?", (turn_id,)
                    ).fetchone()
                )
            }
