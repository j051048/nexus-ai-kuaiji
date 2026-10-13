import json
from uuid import uuid4
from app.accounting.access import AccessControl, FIRM_MANAGERS
from app.accounting.domain import AccountingError
from app.accounting.models import money_to_cents
from app.accounting.service import fingerprint, now
from app.business.service import BusinessService, encode
from app.business.templates import validate_payload


class FactRevisionService:
    def __init__(self, db, business=None, ledger=None):
        self.db = db
        self.access = AccessControl()
        self.business = business or BusinessService(db)
        self.ledger = ledger
        self.after_replace = None

    def _mapping(self, conn, actor, event_id, capability="read"):
        event = self.business._event(conn, actor, event_id)
        mapping = conn.execute(
            "SELECT * FROM business_mappings WHERE event_id=?", (event_id,)
        ).fetchone()
        if not mapping or event["status"] != "handed_off":
            raise AccountingError("只可修订已移交会计的业务事项", 409)
        book = self.access.book(conn, actor, mapping["book_id"], capability)
        draft = conn.execute(
            "SELECT * FROM drafts WHERE id=? AND book_id=?",
            (mapping["draft_id"], mapping["book_id"]),
        ).fetchone()
        return event, mapping, book, draft

    def _authorized_row(self, conn, actor, revision_id, capability="read"):
        row = conn.execute(
            "SELECT * FROM fact_revisions WHERE id=? AND firm_id=?", (revision_id, actor.firm_id)
        ).fetchone()
        if not row:
            raise AccountingError("事实修订不存在或无权访问", 404)
        self.access.book(conn, actor, row["old_book_id"], capability)
        self.access.book(conn, actor, row["target_book_id"], capability)
        return row

    def _json(self, conn, actor, row):
        result = dict(row)
        for key in ("old_snapshot", "proposed_payload"):
            result[key] = json.loads(result[key])
        event = conn.execute(
            "SELECT applicant_id FROM business_events WHERE id=?", (row["event_id"],)
        ).fetchone()
        permission = False
        try:
            self.access.book(conn, actor, row["old_book_id"], "review")
            self.access.book(conn, actor, row["target_book_id"], "review")
            permission = bool(
                event
                and row["proposed_by"] != actor.user_id
                and event["applicant_id"] != actor.user_id
            )
        except AccountingError:
            pass
        result["permissions"] = {"review": permission and row["state"] == "pending"}
        result["decisions"] = [
            dict(item)
            for item in conn.execute(
                "SELECT * FROM fact_revision_decisions WHERE revision_id=? ORDER BY created_at,id",
                (row["id"],),
            )
        ]
        lineage = conn.execute(
            "SELECT * FROM fact_revision_lineage WHERE revision_id=?", (row["id"],)
        ).fetchone()
        result["lineage"] = dict(lineage) if lineage else None
        return result

    def context(self, actor, event_id):
        with self.db.transaction(actor=actor) as conn:
            event, mapping, book, draft = self._mapping(conn, actor, event_id)
            can_submit = book["permissions"]["edit"] and book["period_status"] == "open"
            books = []
            # Current grants are applied in SQL, before returning candidate books.
            role = self.access.firm_role(conn, actor)
            sql = """SELECT b.id,b.name,b.period FROM books b JOIN periods p ON p.book_id=b.id AND p.period=b.period
                JOIN firm_clients fc ON fc.firm_id=b.firm_id AND fc.company_id=b.company_id AND fc.status='active'
                LEFT JOIN book_access a ON a.book_id=b.id AND a.user_id=? AND a.firm_id=b.firm_id
                LEFT JOIN company_memberships cm ON cm.company_id=b.company_id AND cm.user_id=?
                WHERE b.firm_id=? AND b.company_id=? AND p.status='open'"""
            params = [actor.user_id, actor.user_id, actor.firm_id, event["company_id"]]
            if role not in FIRM_MANAGERS:
                sql += " AND a.role IN ('editor','reviewer') AND (?=1 OR cm.role IN ('owner','admin','finance'))"
                params.append(1 if role else 0)
            for item in conn.execute(sql + " ORDER BY b.period,b.name,b.id", params):
                try:
                    self.access.book(conn, actor, item["id"], "edit")
                    books.append(dict(item))
                except AccountingError:
                    pass
            return {
                "event_id": event_id,
                "event_version": event["version"],
                "fact_version": event["fact_version"],
                "payload": json.loads(event["payload"]),
                "template_id": event["template_id"],
                "mapping": dict(mapping),
                "draft_version": draft["version"],
                "draft_status": draft["status"],
                "books": books,
                "permissions": {"submit": bool(can_submit)},
                "pending": conn.execute(
                    "SELECT id FROM fact_revisions WHERE event_id=? AND state='pending'",
                    (event_id,),
                ).fetchone()
                is not None,
            }

    def list(self, actor, event_id=None, limit=30, offset=0):
        if type(limit) is not int or not 1 <= limit <= 100 or type(offset) is not int or offset < 0:
            raise AccountingError("分页参数无效")
        with self.db.transaction(actor=actor) as conn:
            self.access.require_user(conn, actor.user_id)
            role = self.access.firm_role(conn, actor)
            where = "r.firm_id=? AND fo.status='active' AND ft.status='active'"
            params = [actor.firm_id]
            if role not in FIRM_MANAGERS:
                where += " AND ao.role IS NOT NULL AND atg.role IS NOT NULL AND (?=1 OR cm.role IN ('owner','admin','finance'))"
                params.append(1 if role else 0)
            if event_id:
                where += " AND r.event_id=?"
                params.append(event_id)
            joins = (
                """ FROM fact_revisions r JOIN books bo ON bo.id=r.old_book_id JOIN books bt ON bt.id=r.target_book_id
              JOIN firm_clients fo ON fo.firm_id=bo.firm_id AND fo.company_id=bo.company_id
              JOIN firm_clients ft ON ft.firm_id=bt.firm_id AND ft.company_id=bt.company_id
              LEFT JOIN book_access ao ON ao.book_id=bo.id AND ao.user_id=? AND ao.firm_id=r.firm_id
              LEFT JOIN book_access atg ON atg.book_id=bt.id AND atg.user_id=? AND atg.firm_id=r.firm_id
              LEFT JOIN company_memberships cm ON cm.company_id=r.company_id AND cm.user_id=? WHERE """
                + where
            )
            args = [actor.user_id, actor.user_id, actor.user_id, *params]
            total = conn.execute("SELECT COUNT(*)" + joins, args).fetchone()[0]
            rows = conn.execute(
                "SELECT r.*" + joins + " ORDER BY r.created_at DESC,r.id LIMIT ? OFFSET ?",
                [*args, limit, offset],
            ).fetchall()
            return {
                "items": [self._json(conn, actor, row) for row in rows],
                "total": total,
                "limit": limit,
                "offset": offset,
                "has_more": offset + len(rows) < total,
            }

    def get(self, actor, revision_id):
        with self.db.transaction(actor=actor) as conn:
            return self._json(conn, actor, self._authorized_row(conn, actor, revision_id))

    def submit(self, actor, event_id, request):
        payload = request.payload.model_dump()
        command = {"event_id": event_id, **request.model_dump(exclude={"idempotency_key"})}
        digest = fingerprint(command)
        with self.db.transaction(write=True, actor=actor) as conn:
            event, mapping, book, draft = self._mapping(conn, actor, event_id, "edit")
            target = self.access.book(conn, actor, request.target_book_id, "edit")
            if target["company_id"] != event["company_id"]:
                raise AccountingError("修订目标账套必须属于同一企业", 403)
            previous = conn.execute(
                "SELECT * FROM fact_revisions WHERE firm_id=? AND proposed_by=? AND idempotency_key=?",
                (actor.firm_id, actor.user_id, request.idempotency_key),
            ).fetchone()
            if previous:
                if previous["request_fingerprint"] != digest:
                    raise AccountingError("操作编号已绑定另一项事实修订", 409)
                return self._json(conn, actor, previous)
            if book["period_status"] != "open" or target["period_status"] != "open":
                raise AccountingError("原账套和目标账套会计期间必须开放", 403)
            if (event["version"], event["fact_version"], draft["version"]) != (
                request.expected_event_version,
                request.expected_fact_version,
                request.expected_draft_version,
            ):
                raise AccountingError("事实或草稿版本已变化，请刷新后重新提议", 409)
            validate_payload(event["template_id"], event["template_version"], payload)
            if payload["occurred_on"][:7] != target["period"]:
                raise AccountingError("修订发生日期不属于目标会计期间")
            if payload == json.loads(event["payload"]):
                raise AccountingError("新事实与当前事实相同，无需修订", 409)
            if conn.execute(
                "SELECT 1 FROM fact_revisions WHERE event_id=? AND state='pending'", (event_id,)
            ).fetchone():
                raise AccountingError("该事项已有待复核的事实修订", 409)
            old = {
                "payload": json.loads(event["payload"]),
                "event_version": event["version"],
                "fact_version": event["fact_version"],
                "draft_version": draft["version"],
                "draft_status": draft["status"],
                "mapping": dict(mapping),
                "attachments": self.business._attachments(conn, event_id),
            }
            rid = uuid4().hex
            if self.db.is_postgres:
                rid = conn.execute(
                    "SELECT accounting.submit_fact_revision(?,?,?,?,?,?,?,?,?,?)",
                    (
                        event_id,
                        request.target_book_id,
                        request.expected_event_version,
                        request.expected_fact_version,
                        request.expected_draft_version,
                        encode(payload),
                        request.reason,
                        request.idempotency_key,
                        digest,
                        rid,
                    ),
                ).fetchone()[0]
            else:
                conn.execute(
                    "INSERT INTO fact_revisions(id,firm_id,company_id,event_id,old_book_id,old_source_id,old_draft_id,target_book_id,expected_event_version,expected_fact_version,expected_draft_version,old_snapshot,proposed_payload,reason,proposed_by,idempotency_key,request_fingerprint,state,version,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'pending',1,?)",
                    (
                        rid,
                        actor.firm_id,
                        event["company_id"],
                        event_id,
                        book["id"],
                        mapping["source_id"],
                        draft["id"],
                        target["id"],
                        event["version"],
                        event["fact_version"],
                        draft["version"],
                        encode(old),
                        encode(payload),
                        request.reason,
                        actor.user_id,
                        request.idempotency_key,
                        digest,
                        now(),
                    ),
                )
                conn.execute(
                    "INSERT INTO audit_events(firm_id,book_id,user_id,action,object_id,details,created_at) VALUES(?,?,?,?,?,?,?)",
                    (
                        actor.firm_id,
                        book["id"],
                        actor.user_id,
                        "business.fact_revision.submit",
                        rid,
                        encode(
                            {
                                "event_id": event_id,
                                "target_book_id": target["id"],
                                "reason": request.reason,
                            }
                        ),
                        now(),
                    ),
                )
            return self._json(
                conn,
                actor,
                conn.execute("SELECT * FROM fact_revisions WHERE id=?", (rid,)).fetchone(),
            )

    def review(self, actor, revision_id, request):
        digest = fingerprint(
            {"revision_id": revision_id, **request.model_dump(exclude={"idempotency_key"})}
        )
        with self.db.transaction(write=True, actor=actor) as conn:
            revision = self._authorized_row(conn, actor, revision_id, "review")
            event, mapping, book, draft = self._mapping(conn, actor, revision["event_id"], "review")
            target = self.access.book(conn, actor, revision["target_book_id"], "review")
            if actor.user_id in {revision["proposed_by"], event["applicant_id"]}:
                raise AccountingError("修订须由业务申请人和提议人以外的会计独立复核", 403)
            previous = conn.execute(
                "SELECT * FROM fact_revision_decisions WHERE firm_id=? AND reviewed_by=? AND idempotency_key=?",
                (actor.firm_id, actor.user_id, request.idempotency_key),
            ).fetchone()
            if previous:
                if previous["request_fingerprint"] != digest:
                    raise AccountingError("操作编号已绑定另一项修订复核", 409)
                return self._json(conn, actor, revision)
            if revision["state"] != "pending" or revision["version"] != request.expected_version:
                raise AccountingError("修订已复核或版本发生变化", 409)
            if request.decision == "accept" and (
                book["period_status"] != "open" or target["period_status"] != "open"
            ):
                raise AccountingError("原账套和目标账套会计期间必须开放", 403)
            if request.decision == "accept" and (
                mapping["book_id"],
                mapping["source_id"],
                mapping["draft_id"],
                event["version"],
                event["fact_version"],
                draft["version"],
            ) != (
                revision["old_book_id"],
                revision["old_source_id"],
                revision["old_draft_id"],
                revision["expected_event_version"],
                revision["expected_fact_version"],
                revision["expected_draft_version"],
            ):
                raise AccountingError("事实、映射或草稿已变化，请拒绝并重新提议", 409)
            if (
                request.decision == "accept"
                and draft["status"] == "draft"
                and conn.execute(
                    "SELECT 1 FROM ai_bank_links WHERE event_id=?", (event["id"],)
                ).fetchone()
            ):
                raise AccountingError(
                    "事项已有银行流水关联，须先人工解除并重新核对，不能保留旧匹配替代事实", 409
                )
            if request.decision == "accept" and draft["status"] == "draft":
                from ..cases.service import CaseService

                source = conn.execute(
                    "SELECT * FROM sources WHERE id=? AND book_id=?",
                    (mapping["source_id"], mapping["book_id"]),
                ).fetchone()
                if CaseService(self.db, self.business).blocking_issues_in_transaction(
                    conn, actor, source
                ):
                    raise AccountingError(
                        "原来源还有未关闭的异常或补件，请先完成原任务，再接受事实修订；任务不会自动迁移到新期间",
                        409,
                    )
                from ..settlements.service import SettlementService

                if SettlementService(self.db).source_allocated(conn, actor, source):
                    raise AccountingError(
                        "原来源已有有效核销分配，请先按核销流程处理，不能替代已分配事实", 409
                    )
            before = revision["state"]
            if self.db.is_postgres:
                conn.execute(
                    "SELECT accounting.review_fact_revision(?,?,?,?,?,?)",
                    (
                        revision_id,
                        request.expected_version,
                        request.decision,
                        request.reason,
                        request.idempotency_key,
                        digest,
                    ),
                )
            else:
                self._sqlite_review(conn, actor, revision, event, mapping, draft, request, digest)
            current = conn.execute(
                "SELECT * FROM fact_revisions WHERE id=?", (revision_id,)
            ).fetchone()
            if before == "pending" and current["state"] == "replaced" and self.after_replace:
                new_event, new_mapping, new_book, new_draft = self._mapping(
                    conn, actor, revision["event_id"]
                )
                self.after_replace(conn, actor, new_event, new_book, new_draft)
            return self._json(conn, actor, current)

    def _sqlite_review(self, conn, actor, revision, event, mapping, draft, request, digest):
        state = (
            "rejected"
            if request.decision == "reject"
            else "replaced"
            if draft["status"] == "draft"
            else "adjustment_proposed"
        )
        stamp = now()
        conn.execute(
            "INSERT INTO fact_revision_decisions(id,revision_id,firm_id,decision,result_state,reason,reviewed_by,idempotency_key,request_fingerprint,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                uuid4().hex,
                revision["id"],
                actor.firm_id,
                request.decision,
                state,
                request.reason,
                actor.user_id,
                request.idempotency_key,
                digest,
                stamp,
            ),
        )
        if state == "replaced":
            payload = json.loads(revision["proposed_payload"])
            sid, did = uuid4().hex, uuid4().hex
            evidence = "revision:" + revision["id"]
            conn.execute(
                "INSERT INTO sources(id,book_id,external_id,source_name,date,description,counterparty,amount_cents,direction,evidence_ref,fingerprint,source_kind) VALUES(?,?,?,?,?,?,?,?,?,?,?,'business')",
                (
                    sid,
                    revision["target_book_id"],
                    evidence,
                    "经独立审批的业务事实修订",
                    payload["occurred_on"],
                    payload["description"],
                    payload["counterparty"],
                    money_to_cents(payload["amount"]),
                    payload["direction"],
                    evidence,
                    fingerprint({"payload": payload, "evidence_ref": evidence}),
                ),
            )
            conn.execute(
                "INSERT INTO drafts(id,book_id,source_id,description,status,version,explanation) VALUES(?,?,?,?,'draft',1,?)",
                (
                    did,
                    revision["target_book_id"],
                    sid,
                    payload["description"],
                    "业务事实已独立审批替代；空草稿等待后台自动生成及会计复核。",
                ),
            )
            conn.execute(
                "INSERT INTO business_mapping_versions(event_id,firm_id,company_id,book_id,source_id,draft_id,submitted_version,idempotency_key,request_fingerprint,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    event["id"],
                    actor.firm_id,
                    event["company_id"],
                    revision["target_book_id"],
                    sid,
                    did,
                    event["submitted_version"],
                    evidence,
                    revision["request_fingerprint"],
                    actor.user_id,
                    stamp,
                ),
            )
            conn.execute(
                "INSERT INTO fact_revision_lineage(revision_id,firm_id,company_id,event_id,old_book_id,old_source_id,old_draft_id,new_book_id,new_source_id,new_draft_id,old_fact_version,new_fact_version,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    revision["id"],
                    actor.firm_id,
                    event["company_id"],
                    event["id"],
                    mapping["book_id"],
                    mapping["source_id"],
                    mapping["draft_id"],
                    revision["target_book_id"],
                    sid,
                    did,
                    event["fact_version"],
                    event["fact_version"] + 1,
                    stamp,
                ),
            )
            conn.execute(
                "UPDATE business_mappings SET book_id=?,source_id=?,draft_id=?,idempotency_key=?,request_fingerprint=?,created_by=?,created_at=? WHERE event_id=?",
                (
                    revision["target_book_id"],
                    sid,
                    did,
                    evidence,
                    revision["request_fingerprint"],
                    actor.user_id,
                    stamp,
                    event["id"],
                ),
            )
            conn.execute(
                "UPDATE business_events SET payload=?,version=version+1,fact_version=fact_version+1,updated_at=? WHERE id=?",
                (revision["proposed_payload"], stamp, event["id"]),
            )
        conn.execute(
            "UPDATE fact_revisions SET state=?,version=version+1 WHERE id=?",
            (state, revision["id"]),
        )
        conn.execute(
            "INSERT INTO audit_events(firm_id,book_id,user_id,action,object_id,details,created_at) VALUES(?,?,?,?,?,?,?)",
            (
                actor.firm_id,
                revision["old_book_id"],
                actor.user_id,
                "business.fact_revision." + state,
                revision["id"],
                encode(
                    {
                        "reason": request.reason,
                        "event_id": event["id"],
                        "target_book_id": revision["target_book_id"],
                    }
                ),
                stamp,
            ),
        )

    def source_inactive_in_transaction(self, conn, actor, source):
        self.access.book(conn, actor, source["book_id"])
        if self.db.is_postgres:
            return bool(
                conn.execute(
                    "SELECT accounting.revision_source_inactive(?,?)",
                    (source["book_id"], source["id"]),
                ).fetchone()[0]
            )
        return (
            conn.execute(
                "SELECT 1 FROM fact_revision_lineage WHERE old_book_id=? AND old_source_id=?",
                (source["book_id"], source["id"]),
            ).fetchone()
            is not None
        )

    is_source_inactive = source_inactive_in_transaction

    def blocking_issues_in_transaction(self, conn, actor, source):
        if self.source_inactive_in_transaction(conn, actor, source):
            return ["该来源已被独立审批的事实修订替代，原凭据和草稿保留供追溯，请处理当前来源"]
        if self.db.is_postgres:
            blocked = conn.execute(
                "SELECT accounting.revision_source_blocked(?,?)", (source["book_id"], source["id"])
            ).fetchone()[0]
        else:
            blocked = (
                conn.execute(
                    "SELECT 1 FROM fact_revisions WHERE old_book_id=? AND old_source_id=? AND state='pending'",
                    (source["book_id"], source["id"]),
                ).fetchone()
                is not None
            )
        if blocked:
            return ["业务事实修订等待独立复核，暂不可继续审核或导出"]
        return []

    def source_meta_in_transaction(self, conn, actor, source):
        inactive = self.source_inactive_in_transaction(conn, actor, source)
        row = conn.execute(
            "SELECT revision_id,new_book_id,new_source_id,new_draft_id FROM fact_revision_lineage WHERE old_book_id=? AND old_source_id=?",
            (source["book_id"], source["id"]),
        ).fetchone()
        result = dict(row) if row else None
        if result:
            try:
                self.access.book(conn, actor, result["new_book_id"])
            except AccountingError:
                result = {"revision_id": result["revision_id"]}
        return {"inactive": inactive, "revision": result}

    meta_in_transaction = source_meta_in_transaction

    def event_fingerprint_in_transaction(self, conn, actor, event):
        return fingerprint(
            [
                dict(item)
                for item in conn.execute(
                    "SELECT id,state,version,request_fingerprint FROM fact_revisions WHERE event_id=? AND firm_id=? ORDER BY id",
                    (event["id"], actor.firm_id),
                )
            ]
        )

    def historical_books_in_transaction(self, conn, actor, event_id):
        # Discovery only: this must never grant current event/attachment access.
        # Use SourceHistoryService for the pinned old facts and original blobs.
        books = []
        for row in conn.execute(
            "SELECT DISTINCT old_book_id FROM fact_revision_lineage WHERE event_id=? AND firm_id=?",
            (event_id, actor.firm_id),
        ):
            try:
                self.access.book(conn, actor, row["old_book_id"])
                books.append(row["old_book_id"])
            except AccountingError:
                pass
        return books
