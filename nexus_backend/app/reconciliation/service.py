import json
from datetime import date, timedelta
from uuid import uuid4
from app.accounting.access import AccessControl
from app.accounting.domain import AccountingError
from app.accounting.queries import inactive_source_sql
from app.accounting.models import cents_to_money, money_to_cents
from app.accounting.service import AccountingService, fingerprint, now
from app.business.service import BusinessService, encode


class ReconciliationService:
    def __init__(self, db, intelligence=None, bank_import=None):
        from app.accounting.bank_imports import BankImportService

        self.db, self.intelligence, self.bank_import = (
            db,
            intelligence,
            bank_import or BankImportService(db),
        )
        self.access, self.business, self.ledger = (
            AccessControl(),
            BusinessService(db),
            AccountingService(db),
        )

    def _import_issues(self, conn, actor, source):
        return (
            self.bank_import.blocking_issues_in_transaction(conn, actor, source)
            if self.bank_import
            else []
        )

    def _case_issues(self, conn, actor, source):
        return self.ledger._case_guard().blocking_issues_in_transaction(conn, actor, source)

    def _event_input(self, conn, actor, book_id, event_id, edit=False, current=True):
        book = self.access.book(conn, actor, book_id, "edit" if edit else "read")
        event = self.business._event(conn, actor, event_id)
        mapping = conn.execute(
            "SELECT * FROM business_mappings WHERE event_id=? AND book_id=?", (event_id, book_id)
        ).fetchone()
        if (
            not mapping
            or event["company_id"] != book["company_id"]
            or event["status"] != "handed_off"
        ):
            raise AccountingError("业务事项未移交到当前企业账套", 404)
        draft = conn.execute(
            "SELECT * FROM drafts WHERE id=? AND book_id=? AND source_id=?",
            (mapping["draft_id"], book_id, mapping["source_id"]),
        ).fetchone()
        source = conn.execute(
            "SELECT * FROM sources WHERE id=? AND book_id=? AND source_kind='business'",
            (mapping["source_id"], book_id),
        ).fetchone()
        if not draft or not source:
            raise AccountingError("业务来源或凭证不存在", 404)
        payload = json.loads(event["payload"])
        if current:
            self.ledger._open(book)
            if self.ledger._source_state_issues(conn, source):
                raise AccountingError("来源正处于事实修订流程，不能继续关联流水", 409)
            if self.ledger._settlement_guard().source_allocated(conn, actor, source):
                raise AccountingError("来源已有收付分配，请通过核销流程处理", 409)
            if draft["status"] != "draft":
                raise AccountingError("已审核或导出的业务凭证不能建立新流水关联", 409)
            if payload["payment_status"] != "paid":
                raise AccountingError("仅实际已收付的事项可以匹配银行流水", 409)
            if (
                source["amount_cents"] != money_to_cents(payload["amount"])
                or source["direction"] != payload["direction"]
                or source["counterparty"] != payload["counterparty"]
                or source["date"] != payload["occurred_on"]
            ):
                raise AccountingError("当前业务事实与已移交来源不一致，请先人工更正", 409)
        return book, event, draft, source

    def _event_json(self, conn, book, event, draft, source):
        links = [
            dict(row)
            for row in conn.execute(
                "SELECT s.id,s.amount_cents,s.date,s.counterparty FROM ai_bank_links l JOIN sources s ON s.id=l.bank_source_id AND s.book_id=l.book_id WHERE l.event_id=? AND l.book_id=? ORDER BY s.date,s.id",
                (event["id"], book["id"]),
            )
        ]
        return {
            "event_id": event["id"],
            "event_version": event["version"],
            "fact_version": event["fact_version"],
            "business_draft_version": draft["version"],
            "business_source_fingerprint": source["fingerprint"],
            "book_id": book["id"],
            "company_id": book["company_id"],
            "company_name": book["company_name"],
            "description": source["description"],
            "date": source["date"],
            "counterparty": source["counterparty"],
            "amount": cents_to_money(source["amount_cents"]),
            "direction": source["direction"],
            "draft_status": draft["status"],
            "links": [{**row, "amount": cents_to_money(row.pop("amount_cents"))} for row in links],
            "permissions": {
                "confirm": book["permissions"]["edit"]
                and book["period_status"] == "open"
                and draft["status"] == "draft"
            },
        }

    def events(self, actor, book_id, limit=20, offset=0, status="unmatched"):
        if status not in {"unmatched", "matched", "all"} or not 1 <= limit <= 100 or offset < 0:
            raise AccountingError("核对列表分页或状态无效", 422)
        with self.db.transaction(actor=actor) as conn:
            book = self.access.book(conn, actor, book_id)
            paid = (
                "e.payload::jsonb->>'payment_status'='paid'"
                if self.db.is_postgres
                else "json_extract(e.payload,'$.payment_status')='paid'"
            )
            parts = ["m.book_id=?", "m.firm_id=?", "e.status='handed_off'", paid]
            values = [book_id, actor.firm_id]
            if status == "unmatched":
                parts.extend(
                    [
                        "d.status='draft'",
                        "NOT EXISTS(SELECT 1 FROM ai_bank_links l WHERE l.event_id=e.id AND l.book_id=m.book_id)",
                    ]
                )
            elif status == "matched":
                parts.append(
                    "EXISTS(SELECT 1 FROM ai_bank_links l WHERE l.event_id=e.id AND l.book_id=m.book_id)"
                )
            base = (
                " FROM business_mappings m JOIN business_events e ON e.id=m.event_id AND e.firm_id=m.firm_id AND e.company_id=m.company_id JOIN drafts d ON d.id=m.draft_id AND d.book_id=m.book_id JOIN sources s ON s.id=m.source_id AND s.book_id=m.book_id WHERE "
                + " AND ".join(parts)
            )
            base += " AND NOT " + inactive_source_sql(self.db.dialect)
            total = conn.execute("SELECT COUNT(*)" + base, values).fetchone()[0]
            rows = conn.execute(
                "SELECT e.id" + base + " ORDER BY s.date,e.id LIMIT ? OFFSET ?",
                [*values, limit, offset],
            ).fetchall()
            events = []
            for row in rows:
                b, event, draft, source = self._event_input(
                    conn, actor, book_id, row["id"], current=False
                )
                events.append(self._event_json(conn, b, event, draft, source))
            return {
                "book": {
                    "id": book_id,
                    "name": book["name"],
                    "company_name": book["company_name"],
                    "period": book["period"],
                    "period_status": book["period_status"],
                },
                "events": events,
                "total": total,
                "limit": limit,
                "offset": offset,
                "has_more": offset + len(events) < total,
            }

    def candidates(self, actor, book_id, event_id, limit=20, offset=0):
        if not 1 <= limit <= 100 or offset < 0:
            raise AccountingError("候选分页参数无效", 422)
        with self.db.transaction(actor=actor) as conn:
            book, event, draft, source = self._event_input(conn, actor, book_id, event_id)
            day = date.fromisoformat(source["date"])
            values = [
                book_id,
                source["direction"],
                source["counterparty"],
                source["amount_cents"],
                (day - timedelta(days=7)).isoformat(),
                (day + timedelta(days=7)).isoformat(),
            ]
            base = " FROM sources s LEFT JOIN drafts d ON d.source_id=s.id AND d.book_id=s.book_id LEFT JOIN bank_import_keys k ON k.source_id=s.id AND k.book_id=s.book_id WHERE s.book_id=? AND s.source_kind='bank' AND s.direction=? AND s.counterparty=? AND s.amount_cents<=? AND s.date BETWEEN ? AND ? AND (d.status='draft' OR d.id IS NULL) AND NOT EXISTS(SELECT 1 FROM ai_bank_links l WHERE l.bank_source_id=s.id)"
            base += " AND NOT " + inactive_source_sql(self.db.dialect)
            base += " AND NOT EXISTS(SELECT 1 FROM settlement_allocations sa WHERE sa.bank_source_id=s.id AND NOT EXISTS(SELECT 1 FROM settlement_reversals sr WHERE sr.allocation_id=sa.id))"
            total = conn.execute("SELECT COUNT(*)" + base, values).fetchone()[0]
            exact_count = conn.execute(
                "SELECT COUNT(*)" + base + " AND s.amount_cents=?",
                [*values, source["amount_cents"]],
            ).fetchone()[0]
            rows = conn.execute(
                "SELECT s.*,d.id AS draft_id,d.version AS draft_version,COALESCE(k.original_external_id,s.external_id) AS public_external_id,k.stable_transaction_id"
                + base
                + " ORDER BY CASE WHEN s.amount_cents=? THEN 0 ELSE 1 END,s.date,s.id LIMIT ? OFFSET ?",
                [*values, source["amount_cents"], limit, offset],
            )
            candidates = []
            for bank in rows:
                import_issues = self._import_issues(conn, actor, bank)
                case_issues = self._case_issues(conn, actor, bank)
                risks = [*import_issues, *case_issues]
                if bank["draft_version"] is None:
                    risks.append("该流水尚未生成凭证草稿，请先处理流水待办")
                if exact_count > 1 and bank["amount_cents"] == source["amount_cents"]:
                    risks.append(f"共有 {exact_count} 笔同金额候选，必须人工辨别实际收付")
                candidates.append(
                    {
                        "source_id": bank["id"],
                        "source_fingerprint": bank["fingerprint"],
                        "draft_version": bank["draft_version"],
                        "date": bank["date"],
                        "description": bank["description"],
                        "counterparty": bank["counterparty"],
                        "amount": cents_to_money(bank["amount_cents"]),
                        "external_id": bank["public_external_id"],
                        "stable_transaction_id": bank["stable_transaction_id"],
                        "reasons": [
                            "同企业同账套",
                            "收付方向一致",
                            "交易对方精确一致",
                            "发生日期相差不超过 7 天",
                            "金额完全相等"
                            if bank["amount_cents"] == source["amount_cents"]
                            else "分笔收付候选，须核对合计",
                        ],
                        "risks": risks,
                        "selectable": bank["draft_version"] is not None
                        and not import_issues
                        and not case_issues,
                    }
                )
            return {
                "event": self._event_json(conn, book, event, draft, source),
                "candidates": candidates,
                "total": total,
                "exact_amount_count": exact_count,
                "ambiguous": exact_count > 1,
                "limit": limit,
                "offset": offset,
                "has_more": offset + len(candidates) < total,
            }

    def _authorize(self, conn, actor, request, current):
        return [
            (
                item,
                *self._event_input(
                    conn, actor, item.book_id, item.event_id, edit=True, current=current
                ),
            )
            for item in request.items
        ]

    def confirm(self, actor, request):
        digest = fingerprint(request.model_dump())
        with self.db.transaction(write=True, actor=actor) as conn:
            # Authorize every requested company/book before looking up or writing
            # a receipt. A retry never recovers data from a revoked book.
            authorized = self._authorize(conn, actor, request, current=False)
            receipt = conn.execute(
                "SELECT * FROM reconciliation_receipts WHERE firm_id=? AND created_by=? AND idempotency_key=?",
                (actor.firm_id, actor.user_id, request.idempotency_key),
            ).fetchone()
            if receipt:
                if receipt["request_fingerprint"] != digest:
                    raise AccountingError("关联幂等键已用于另一批业务与流水", 409)
                return self._receipt_json(conn, actor, receipt)
            authorized = self._authorize(conn, actor, request, current=True)
            plans = []
            for item, book, event, draft, source in authorized:
                if (
                    item.event_version != event["version"]
                    or item.fact_version != event["fact_version"]
                    or item.business_draft_version != draft["version"]
                    or item.business_source_fingerprint != source["fingerprint"]
                ):
                    raise AccountingError("业务事实或凭证版本已改变，请刷新候选后重新确认", 409)
                selected = {bank.source_id for bank in item.banks}
                prior = {
                    row[0]
                    for row in conn.execute(
                        "SELECT bank_source_id FROM ai_bank_links WHERE event_id=? AND book_id=?",
                        (event["id"], book["id"]),
                    )
                }
                if not prior.issubset(selected):
                    raise AccountingError(
                        "选择必须包含该事项已关联的全部流水，不能静默替换关联", 409
                    )
                banks = []
                for selection in item.banks:
                    bank = conn.execute(
                        "SELECT s.*,d.status AS draft_status,d.version AS draft_version,l.event_id AS linked_event_id FROM sources s JOIN drafts d ON d.source_id=s.id AND d.book_id=s.book_id LEFT JOIN ai_bank_links l ON l.bank_source_id=s.id WHERE s.id=? AND s.book_id=? AND s.source_kind='bank'",
                        (selection.source_id, item.book_id),
                    ).fetchone()
                    if not bank:
                        raise AccountingError("所选流水不存在、尚无草稿或不属于该账套", 404)
                    if (
                        bank["fingerprint"] != selection.source_fingerprint
                        or bank["draft_version"] != selection.draft_version
                    ):
                        raise AccountingError("流水事实或凭证版本已改变，请重新核对", 409)
                    if bank["draft_status"] != "draft" or bank["linked_event_id"] not in {
                        None,
                        event["id"],
                    }:
                        raise AccountingError("流水已审核、导出或关联其他事项，不能重复处理", 409)
                    if self.ledger._settlement_guard().source_allocated(conn, actor, bank):
                        raise AccountingError("流水已有收付分配，不能重复关联完整业务事项", 409)
                    if (
                        bank["direction"] != source["direction"]
                        or bank["counterparty"] != source["counterparty"]
                        or abs(
                            (
                                date.fromisoformat(bank["date"])
                                - date.fromisoformat(source["date"])
                            ).days
                        )
                        > 7
                    ):
                        raise AccountingError(
                            "流水方向、交易对方或发生日期不符合本次确定性匹配，请人工处理", 409
                        )
                    issues = self._import_issues(conn, actor, bank)
                    if issues:
                        raise AccountingError("来源导入尚待会计辨别：" + "；".join(issues), 409)
                    if self._case_issues(conn, actor, bank):
                        raise AccountingError(
                            "所选流水仍有未关闭异常或补件，请由授权人员处理后重新确认", 409
                        )
                    banks.append(bank)
                if sum(bank["amount_cents"] for bank in banks) != source["amount_cents"]:
                    raise AccountingError("所选流水合计必须精确等于已收付业务金额", 409)
                plans.append((item, book, event, banks))
            if self.db.is_postgres:
                result_id = conn.execute(
                    "SELECT accounting.confirm_reconciliation(?,?,?)",
                    (request.idempotency_key, digest, encode(request.model_dump())),
                ).fetchone()[0]
            else:
                result_id = uuid4().hex
                for item, book, event, banks in plans:
                    for bank in banks:
                        if not bank["linked_event_id"]:
                            conn.execute(
                                "INSERT INTO ai_bank_links(bank_source_id,firm_id,company_id,event_id,book_id,created_by,created_at) VALUES(?,?,?,?,?,?,?)",
                                (
                                    bank["id"],
                                    actor.firm_id,
                                    book["company_id"],
                                    event["id"],
                                    book["id"],
                                    actor.user_id,
                                    now(),
                                ),
                            )
                conn.execute(
                    "INSERT INTO reconciliation_receipts(id,firm_id,created_by,idempotency_key,request_fingerprint,request_json,reason,created_at) VALUES(?,?,?,?,?,?,?,?)",
                    (
                        result_id,
                        actor.firm_id,
                        actor.user_id,
                        request.idempotency_key,
                        digest,
                        encode(request.model_dump()),
                        request.reason,
                        now(),
                    ),
                )
            for item, book, event, _ in plans:
                self.business._audit(
                    conn,
                    actor,
                    "reconciliation.confirm",
                    event["id"],
                    {
                        "receipt_id": result_id,
                        "bank_source_ids": [bank.source_id for bank in item.banks],
                        "reason": request.reason,
                    },
                    book["id"],
                )
                if self.intelligence:
                    self.intelligence.capture_snapshot(conn, actor, event)
            return self._receipt_json(
                conn,
                actor,
                conn.execute(
                    "SELECT * FROM reconciliation_receipts WHERE id=?", (result_id,)
                ).fetchone(),
            )

    def _receipt_json(self, conn, actor, row):
        request = json.loads(row["request_json"])
        items = []
        for item in request["items"]:
            self._event_input(
                conn, actor, item["book_id"], item["event_id"], edit=True, current=False
            )
            actual = {
                bank[0]
                for bank in conn.execute(
                    "SELECT bank_source_id FROM ai_bank_links WHERE event_id=? AND book_id=?",
                    (item["event_id"], item["book_id"]),
                )
            }
            selected = sorted(bank["source_id"] for bank in item["banks"])
            items.append(
                {
                    "book_id": item["book_id"],
                    "event_id": item["event_id"],
                    "bank_source_ids": selected,
                    "links_current": actual == set(selected),
                }
            )
        return {
            "id": row["id"],
            "created_at": row["created_at"],
            "reason": row["reason"],
            "items": items,
            "review_required": True,
        }
