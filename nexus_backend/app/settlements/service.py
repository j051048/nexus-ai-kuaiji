"""Open-period auxiliary settlement allocations with immutable receipts.

Original unpaid business journals stay intact. Fully allocated bank sources
must settle their reviewed payable/receivable accounts, never repeat expenses.
"""

from collections import defaultdict
from datetime import datetime, timedelta, timezone
import json
from uuid import uuid4

from ..accounting.access import AccessControl
from ..accounting.domain import AccountingError
from ..accounting.models import money_to_cents, cents_to_money
from ..accounting.queries import inactive_source_sql
from ..accounting.service import fingerprint, now
from ..business.service import encode
from ..ledgers.service import LedgersService

PAYABLES = {"2202", "2241"}
RECEIVABLES = {"1122", "1221"}


def split_cents(total, parts):
    """Largest remainder, integer cents, stable key tie-break; no float."""
    if (
        type(total) is not int
        or total <= 0
        or not parts
        or len(parts) > 50
        or len({key for key, _ in parts}) != len(parts)
        or any(type(weight) is not int or weight <= 0 for _, weight in parts)
    ):
        raise AccountingError("分摊总额、权重或唯一标识无效", 422)
    denominator = sum(weight for _, weight in parts)
    rows = [
        {
            "key": key,
            "cents": total * weight // denominator,
            "remainder": total * weight % denominator,
        }
        for key, weight in sorted(parts)
    ]
    tail = total - sum(row["cents"] for row in rows)
    for row in sorted(rows, key=lambda value: (-value["remainder"], value["key"]))[:tail]:
        row["cents"] += 1
    return [{"key": row["key"], "amount": cents_to_money(row["cents"])} for row in rows]


class SettlementService:
    def __init__(self, db):
        self.db, self.access, self.master = db, AccessControl(), LedgersService(db)

    def _book(self, conn, actor, book_id, write=False):
        book = self.access.book(conn, actor, book_id, "review")
        if write and book["period_status"] != "open":
            raise AccountingError("核销只能写入当前开放期间", 403)
        return book

    def _company_of_book(self, actor, book_id):
        """Resolve the book's company up front for a company-scoped write lock."""
        with self.db.transaction(actor=actor) as conn:
            return self._book(conn, actor, book_id)["company_id"]

    @staticmethod
    def _source(conn, book_id, source_id):
        row = conn.execute(
            "SELECT s.*,d.id AS draft_id,d.status AS draft_status,d.version AS draft_version FROM sources s JOIN drafts d ON d.source_id=s.id AND d.book_id=s.book_id WHERE s.id=? AND s.book_id=?",
            (source_id, book_id),
        ).fetchone()
        if not row:
            raise AccountingError("来源和凭证不存在或无权访问", 404)
        return dict(row)

    def _current(self, conn, source):
        if conn.execute(
            "SELECT 1 FROM sources s WHERE s.id=? AND s.book_id=? AND "
            + inactive_source_sql(self.db.dialect),
            (source["id"], source["book_id"]),
        ).fetchone():
            raise AccountingError("来源已被修订替代或隔离，不能继续核销", 409)
        pending = (
            conn.execute(
                "SELECT accounting.revision_source_blocked(?,?)", (source["book_id"], source["id"])
            ).fetchone()[0]
            if self.db.is_postgres
            else conn.execute(
                "SELECT 1 FROM fact_revisions WHERE old_book_id=? AND old_source_id=? AND state='pending'",
                (source["book_id"], source["id"]),
            ).fetchone()
            is not None
        )
        if pending:
            raise AccountingError("来源存在待独立审批的事实修订，暂不可核销", 409)
        if conn.execute(
            "SELECT 1 FROM ai_bank_links WHERE bank_source_id=?", (source["id"],)
        ).fetchone():
            raise AccountingError("旧完整流水匹配与核销分配互斥", 409)

    @staticmethod
    def _used(conn, field, target):
        if field not in {"item_id", "bank_source_id"}:
            raise ValueError("Invalid settlement balance dimension")
        return conn.execute(
            "SELECT COALESCE(SUM(a.amount_cents),0) FROM settlement_allocations a WHERE a."
            + field
            + "=? AND NOT EXISTS(SELECT 1 FROM settlement_reversals r WHERE r.allocation_id=a.id)",
            (target,),
        ).fetchone()[0]

    def _business(self, conn, actor, book, source):
        self._current(conn, source)
        row = conn.execute(
            "SELECT e.*,m.source_id,m.draft_id FROM business_mappings m JOIN business_events e ON e.id=m.event_id WHERE m.book_id=? AND m.source_id=? AND m.firm_id=?",
            (book["id"], source["id"], actor.firm_id),
        ).fetchone()
        if not row or source["source_kind"] != "business":
            raise AccountingError("未结事项需要当前移交的业务来源", 409)
        payload = json.loads(row["payload"])
        if (
            payload.get("payment_status") != "unpaid"
            or row["status"] != "handed_off"
            or money_to_cents(payload["amount"]) != source["amount_cents"]
            or payload.get("direction") != source["direction"]
            or payload.get("counterparty") != source["counterparty"]
            or payload.get("occurred_on") != source["date"]
        ):
            raise AccountingError("业务原始事实必须为当前未付款或未收款事项", 409)
        if conn.execute("SELECT 1 FROM ai_bank_links WHERE event_id=?", (row["id"],)).fetchone():
            raise AccountingError("旧完整流水匹配与核销分配互斥", 409)
        side = "credit_cents" if source["direction"] == "expense" else "debit_cents"
        allowed = PAYABLES if source["direction"] == "expense" else RECEIVABLES
        rows = conn.execute(
            "SELECT account_code,SUM("
            + side
            + ") AS amount FROM draft_lines WHERE draft_id=? AND book_id=? GROUP BY account_code",
            (source["draft_id"], book["id"]),
        ).fetchall()
        clearing = [row for row in rows if row["account_code"] in allowed and row["amount"] > 0]
        if len(clearing) != 1 or clearing[0]["amount"] != source["amount_cents"]:
            raise AccountingError("业务凭证须以单一应收/应付科目记录全部未结金额", 409)
        return {
            "event_id": row["id"],
            "fact_version": row["fact_version"],
            "clearing_account": clearing[0]["account_code"],
        }

    def _item(self, conn, actor, book, item_id, current=True):
        item = conn.execute(
            "SELECT * FROM settlement_items WHERE id=? AND book_id=? AND firm_id=?",
            (item_id, book["id"], actor.firm_id),
        ).fetchone()
        if not item:
            raise AccountingError("未结事项不存在或无权访问", 404)
        item = dict(item)
        source = self._source(conn, book["id"], item["source_id"])
        if current:
            context = self._business(conn, actor, book, source)
            if (
                context["fact_version"] != item["fact_version"]
                or source["fingerprint"] != item["source_fingerprint"]
                or context["clearing_account"] != item["clearing_account"]
            ):
                raise AccountingError(
                    "未结事项的事实或应收应付科目已变化，请撤销未审银行分配并重新核对", 409
                )
            party = self.master.resolve_in_transaction(
                conn, actor, book["company_id"], "counterparty", source["counterparty"], book["id"]
            )
            if not party or party["entity_id"] != item["counterparty_id"]:
                raise AccountingError("交易对方映射已变化，请重新核验", 409)
        is_current = True
        if not current:
            try:
                context = self._business(conn, actor, book, source)
                party = self.master.resolve_in_transaction(
                    conn,
                    actor,
                    book["company_id"],
                    "counterparty",
                    source["counterparty"],
                    book["id"],
                )
                is_current = (
                    context["fact_version"] == item["fact_version"]
                    and context["clearing_account"] == item["clearing_account"]
                    and source["fingerprint"] == item["source_fingerprint"]
                    and bool(party and party["entity_id"] == item["counterparty_id"])
                )
            except AccountingError:
                is_current = False
        used = self._used(conn, "item_id", item_id)
        return {
            **item,
            "allocated_cents": used,
            "remaining_cents": item["amount_cents"] - used,
            "draft_id": source["draft_id"],
            "draft_status": source["draft_status"],
            "draft_version": source["draft_version"],
            "is_current": is_current,
        }

    @staticmethod
    def _item_json(item):
        result = {
            key: item[key]
            for key in (
                "id",
                "source_id",
                "book_id",
                "counterparty_id",
                "counterparty_original",
                "direction",
                "clearing_account",
                "fact_version",
                "created_at",
                "draft_id",
                "draft_status",
                "draft_version",
            )
        }
        return result | {
            "amount": cents_to_money(item["amount_cents"]),
            "allocated": cents_to_money(item["allocated_cents"]),
            "remaining": cents_to_money(item["remaining_cents"]),
            "currency": "CNY",
            "is_current": item["is_current"],
        }

    def _allocation_fingerprint(self, conn, row):
        if self.db.is_postgres:
            return conn.execute(
                "SELECT accounting.settlement_allocation_fingerprint(?)", (row["id"],)
            ).fetchone()[0]
        bank = self._source(conn, row["book_id"], row["bank_source_id"])
        return fingerprint(
            {
                "allocation_id": row["id"],
                "bank_draft_version": bank["draft_version"],
                "bank_draft_status": bank["draft_status"],
                "amount_cents": row["amount_cents"],
                "item_id": row["item_id"],
                "reversed": bool(
                    conn.execute(
                        "SELECT 1 FROM settlement_reversals WHERE allocation_id=?", (row["id"],)
                    ).fetchone()
                ),
            }
        )

    def open_item(self, actor, book_id, request):
        digest = fingerprint(
            {"book_id": book_id, **request.model_dump(exclude={"idempotency_key"})}
        )
        company_id = self._company_of_book(actor, book_id)
        with self.db.transaction(
            write=True, actor=actor, company_id=company_id, write_scope="company"
        ) as conn:
            book = self._book(conn, actor, book_id)
            prior = conn.execute(
                "SELECT * FROM settlement_items WHERE firm_id=? AND created_by=? AND idempotency_key=?",
                (actor.firm_id, actor.user_id, request.idempotency_key),
            ).fetchone()
            if prior:
                if prior["request_fingerprint"] != digest:
                    raise AccountingError("登记幂等键已用于其它事项", 409)
                return self._item_json(self._item(conn, actor, book, prior["id"], current=False))
            self._book(conn, actor, book_id, True)
            source = self._source(conn, book_id, request.source_id)
            if (
                source["draft_status"] != "draft"
                or source["draft_version"] != request.expected_draft_version
            ):
                raise AccountingError("登记须绑定当前未审核业务草稿版本", 409)
            context = self._business(conn, actor, book, source)
            party = self.master.resolve_in_transaction(
                conn, actor, book["company_id"], "counterparty", source["counterparty"], book_id
            )
            if not party or party["entity_id"] != request.counterparty_id:
                raise AccountingError("请先建立并审核交易对方的主数据或别名", 409)
            if conn.execute(
                "SELECT 1 FROM settlement_items WHERE source_id=?", (source["id"],)
            ).fetchone():
                raise AccountingError("该业务来源已登记未结事项", 409)
            if self.db.is_postgres:
                item_id = conn.execute(
                    "SELECT accounting.settlement_open_item(?,?,?,?,?,?,?)",
                    (
                        book_id,
                        source["id"],
                        request.counterparty_id,
                        request.expected_draft_version,
                        request.idempotency_key,
                        digest,
                        request.reason,
                    ),
                ).fetchone()[0]
            else:
                item_id = uuid4().hex
                conn.execute(
                    "INSERT INTO settlement_items(id,firm_id,company_id,book_id,source_id,event_id,fact_version,source_fingerprint,counterparty_id,counterparty_original,amount_cents,direction,clearing_account,created_by,created_at,idempotency_key,request_fingerprint,reason) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        item_id,
                        actor.firm_id,
                        book["company_id"],
                        book_id,
                        source["id"],
                        context["event_id"],
                        context["fact_version"],
                        source["fingerprint"],
                        request.counterparty_id,
                        source["counterparty"],
                        source["amount_cents"],
                        source["direction"],
                        context["clearing_account"],
                        actor.user_id,
                        now(),
                        request.idempotency_key,
                        digest,
                        request.reason,
                    ),
                )
            return self._item_json(self._item(conn, actor, book, item_id))

    def _plan(self, conn, actor, book, request):
        bank = self._source(conn, book["id"], request.bank_source_id)
        self._current(conn, bank)
        if bank["source_kind"] != "bank" or bank["draft_status"] != "draft":
            raise AccountingError("收付分配需要同一开放期间的未审核银行来源", 409)
        if self.db.is_postgres:
            blocked = conn.execute(
                "SELECT accounting.bank_source_import_blocked(?,?),accounting.case_source_blocked(?,?)",
                (book["id"], bank["id"], book["id"], bank["id"]),
            ).fetchone()
            if blocked[0] or blocked[1]:
                raise AccountingError("银行来源有未处理的重复或异常", 409)
        else:
            from ..accounting.bank_imports import BankImportService
            from ..cases.service import CaseService
            from ..business.service import BusinessService

            if BankImportService(self.db).blocking_issues_in_transaction(
                conn, actor, bank
            ) or CaseService(self.db, BusinessService(self.db)).blocking_issues_in_transaction(
                conn, actor, bank
            ):
                raise AccountingError("银行来源有未处理的重复或异常", 409)
        party = self.master.resolve_in_transaction(
            conn, actor, book["company_id"], "counterparty", bank["counterparty"], book["id"]
        )
        if not party:
            raise AccountingError("银行交易对方尚未匹配已审核主数据", 409)
        remaining = bank["amount_cents"] - self._used(conn, "bank_source_id", bank["id"])
        items, total = [], 0
        for part in request.parts:
            item = self._item(conn, actor, book, part.item_id)
            amount = money_to_cents(part.amount)
            if (
                item["direction"] != bank["direction"]
                or item["counterparty_id"] != party["entity_id"]
                or amount <= 0
                or amount > item["remaining_cents"]
            ):
                raise AccountingError(
                    "分配必须同企业、同交易对方、同收付方向，金额不能超过未结余额", 409
                )
            total += amount
            items.append(
                {
                    "item_id": item["id"],
                    "amount_cents": amount,
                    "remaining_before": item["remaining_cents"],
                    "fact_version": item["fact_version"],
                    "clearing_account": item["clearing_account"],
                    "business_draft_version": item["draft_version"],
                    "business_draft_status": item["draft_status"],
                    "source_fingerprint": item["source_fingerprint"],
                }
            )
        if total > remaining:
            raise AccountingError("分配总额不能超过银行流水未分配余额", 409)
        return {
            "book_id": book["id"],
            "bank_source_id": bank["id"],
            "bank_fingerprint": bank["fingerprint"],
            "bank_draft_version": bank["draft_version"],
            "bank_remaining_before": remaining,
            "counterparty_id": party["entity_id"],
            "parts": items,
            "total_cents": total,
            "reason": request.reason,
        }

    def preview(self, actor, book_id, request):
        company_id = self._company_of_book(actor, book_id)
        with self.db.transaction(
            write=True, actor=actor, company_id=company_id, write_scope="company"
        ) as conn:
            book = self._book(conn, actor, book_id, True)
            plan = self._plan(conn, actor, book, request)
            if self.db.is_postgres:
                preview_id = conn.execute(
                    "SELECT accounting.settlement_make_preview(?,?)",
                    (book_id, encode(request.model_dump())),
                ).fetchone()[0]
            else:
                preview_id = uuid4().hex
                conn.execute(
                    "INSERT INTO settlement_previews(id,book_id,firm_id,company_id,created_by,request_json,plan_json,created_at,expires_at) VALUES(?,?,?,?,?,?,?,?,?)",
                    (
                        preview_id,
                        book_id,
                        actor.firm_id,
                        book["company_id"],
                        actor.user_id,
                        encode(request.model_dump()),
                        encode(plan),
                        now(),
                        (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat(),
                    ),
                )
            return {
                "id": preview_id,
                "plan": plan,
                "fingerprint": fingerprint(plan),
                "currency": "CNY",
                "requires_accounting_review": True,
            }

    def _receipt(self, conn, row):
        return {
            "id": row["id"],
            "book_id": row["book_id"],
            "preview_id": row["preview_id"],
            "created_at": row["created_at"],
            "allocations": [
                dict(item) | {"amount": cents_to_money(item["amount_cents"])}
                for item in conn.execute(
                    "SELECT id,item_id,bank_source_id,amount_cents,clearing_account FROM settlement_allocations WHERE command_id=? ORDER BY id",
                    (row["id"],),
                )
            ],
        }

    def confirm(self, actor, book_id, preview_id, request):
        company_id = self._company_of_book(actor, book_id)
        with self.db.transaction(
            write=True, actor=actor, company_id=company_id, write_scope="company"
        ) as conn:
            book = self._book(conn, actor, book_id)
            preview = conn.execute(
                "SELECT * FROM settlement_previews WHERE id=? AND book_id=? AND firm_id=? AND created_by=?",
                (preview_id, book_id, actor.firm_id, actor.user_id),
            ).fetchone()
            if not preview:
                raise AccountingError("分配预览不存在或无权访问", 404)
            old = conn.execute(
                "SELECT * FROM settlement_commands WHERE firm_id=? AND created_by=? AND idempotency_key=?",
                (actor.firm_id, actor.user_id, request.idempotency_key),
            ).fetchone()
            if old:
                if old["preview_id"] != preview_id:
                    raise AccountingError("分配幂等键已用于其它预览", 409)
                return self._receipt(conn, old)
            self._book(conn, actor, book_id, True)
            body = AllocationPreview.model_validate(json.loads(preview["request_json"]))
            plan = self._plan(conn, actor, book, body)
            if self.db.is_postgres:
                command_id = conn.execute(
                    "SELECT accounting.settlement_confirm(?,?,?)",
                    (book_id, preview_id, request.idempotency_key),
                ).fetchone()[0]
            else:
                if datetime.now(timezone.utc) > datetime.fromisoformat(
                    preview["expires_at"]
                ) or plan != json.loads(preview["plan_json"]):
                    raise AccountingError("预览已过期或事实、分配余额、凭证版本已改变", 409)
                command_id = uuid4().hex
                conn.execute(
                    "INSERT INTO settlement_commands(id,book_id,firm_id,company_id,preview_id,created_by,idempotency_key,created_at) VALUES(?,?,?,?,?,?,?,?)",
                    (
                        command_id,
                        book_id,
                        actor.firm_id,
                        book["company_id"],
                        preview_id,
                        actor.user_id,
                        request.idempotency_key,
                        now(),
                    ),
                )
                for part in plan["parts"]:
                    conn.execute(
                        "INSERT INTO settlement_allocations(id,command_id,book_id,firm_id,company_id,item_id,bank_source_id,amount_cents,clearing_account,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            uuid4().hex,
                            command_id,
                            book_id,
                            actor.firm_id,
                            book["company_id"],
                            part["item_id"],
                            plan["bank_source_id"],
                            part["amount_cents"],
                            part["clearing_account"],
                            actor.user_id,
                            now(),
                        ),
                    )
            return self._receipt(
                conn,
                conn.execute(
                    "SELECT * FROM settlement_commands WHERE id=?", (command_id,)
                ).fetchone(),
            )

    def reversal(self, actor, book_id, allocation_id, request):
        company_id = self._company_of_book(actor, book_id)
        with self.db.transaction(
            write=True, actor=actor, company_id=company_id, write_scope="company"
        ) as conn:
            book = self._book(conn, actor, book_id)
            row = conn.execute(
                "SELECT * FROM settlement_allocations WHERE id=? AND book_id=? AND firm_id=?",
                (allocation_id, book_id, actor.firm_id),
            ).fetchone()
            if not row:
                raise AccountingError("分配记录不存在或无权访问", 404)
            if row["created_by"] == actor.user_id:
                raise AccountingError("撤销分配需要另一名当前账套复核人员独立确认", 403)
            old = conn.execute(
                "SELECT * FROM settlement_reversals WHERE firm_id=? AND created_by=? AND idempotency_key=?",
                (actor.firm_id, actor.user_id, request.idempotency_key),
            ).fetchone()
            if old:
                if (
                    old["allocation_id"] != allocation_id
                    or old["reason"] != request.reason
                    or old["expected_fingerprint"] != request.expected_fingerprint
                ):
                    raise AccountingError("撤销幂等键已绑定不同事实", 409)
                return dict(old)
            self._book(conn, actor, book_id, True)
            bank = self._source(conn, book_id, row["bank_source_id"])
            if bank["draft_status"] != "draft":
                raise AccountingError(
                    "关联银行凭证已审核或导出，只能另行调整，不能撤销历史分配", 409
                )
            if self._allocation_fingerprint(conn, row) != request.expected_fingerprint:
                raise AccountingError("分配记录指纹已变化", 409)
            if self.db.is_postgres:
                reversal_id = conn.execute(
                    "SELECT accounting.settlement_reverse(?,?,?,?,?)",
                    (
                        book_id,
                        allocation_id,
                        request.expected_fingerprint,
                        request.idempotency_key,
                        request.reason,
                    ),
                ).fetchone()[0]
            else:
                if conn.execute(
                    "SELECT 1 FROM settlement_reversals WHERE allocation_id=?", (allocation_id,)
                ).fetchone():
                    raise AccountingError("该分配已撤销", 409)
                reversal_id = uuid4().hex
                conn.execute(
                    "INSERT INTO settlement_reversals(id,allocation_id,book_id,firm_id,company_id,created_by,created_at,idempotency_key,reason,expected_fingerprint) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (
                        reversal_id,
                        allocation_id,
                        book_id,
                        actor.firm_id,
                        book["company_id"],
                        actor.user_id,
                        now(),
                        request.idempotency_key,
                        request.reason,
                        request.expected_fingerprint,
                    ),
                )
            return dict(
                conn.execute(
                    "SELECT * FROM settlement_reversals WHERE id=?", (reversal_id,)
                ).fetchone()
            )

    def source_allocated(self, conn, actor, source):
        self.access.book(conn, actor, source["book_id"])
        return (
            self._used(conn, "bank_source_id", source["id"]) > 0
            if source["source_kind"] == "bank"
            else bool(
                conn.execute(
                    "SELECT 1 FROM settlement_items i JOIN settlement_allocations a ON a.item_id=i.id WHERE i.source_id=? AND NOT EXISTS(SELECT 1 FROM settlement_reversals r WHERE r.allocation_id=a.id)",
                    (source["id"],),
                ).fetchone()
            )
        )

    def source_allocated_in_transaction(self, conn, actor, source):
        return self.source_allocated(conn, actor, source)

    def policy_fingerprint_in_transaction(self, conn, actor, source):
        self.access.book(conn, actor, source["book_id"])
        rows = conn.execute(
            "SELECT a.id,a.amount_cents,a.clearing_account,r.id AS reversal_id FROM settlement_allocations a LEFT JOIN settlement_reversals r ON r.allocation_id=a.id LEFT JOIN settlement_items i ON i.id=a.item_id WHERE a.book_id=? AND (a.bank_source_id=? OR i.source_id=?) ORDER BY a.id",
            (source["book_id"], source["id"], source["id"]),
        ).fetchall()
        return fingerprint([dict(row) for row in rows])

    def bank_clearance(self, conn, actor, source):
        book = self.access.book(conn, actor, source["book_id"])
        rows = conn.execute(
            "SELECT a.* FROM settlement_allocations a WHERE a.bank_source_id=? AND NOT EXISTS(SELECT 1 FROM settlement_reversals r WHERE r.allocation_id=a.id) ORDER BY a.id",
            (source["id"],),
        ).fetchall()
        if not rows:
            return None
        issues, amounts = [], defaultdict(int)
        bank_party = self.master.resolve_in_transaction(
            conn, actor, book["company_id"], "counterparty", source["counterparty"], book["id"]
        )
        if sum(row["amount_cents"] for row in rows) != source["amount_cents"]:
            issues.append("银行流水尚未全部分配，不能审核或导出清偿凭证")
        for row in rows:
            try:
                item = self._item(conn, actor, book, row["item_id"])
                if item["draft_status"] not in {"approved", "exported"}:
                    issues.append("须先复核原业务应收/应付凭证，再复核银行清偿凭证")
                if item["clearing_account"] != row["clearing_account"]:
                    issues.append("原业务应收/应付科目与分配时不一致，须人工处置")
                if not bank_party or bank_party["entity_id"] != item["counterparty_id"]:
                    issues.append("银行交易对方当前主数据映射与未结事项不一致，须人工处置")
            except AccountingError:
                issues.append("核销引用的业务事实或交易对方已变化，须人工处置")
            amounts[row["clearing_account"]] += row["amount_cents"]
        side = "debit" if source["direction"] == "expense" else "credit"
        lines = [
            {
                "account_code": code,
                "debit": cents_to_money(amount if side == "debit" else 0),
                "credit": cents_to_money(amount if side == "credit" else 0),
                "auxiliary_values": {},
            }
            for code, amount in sorted(amounts.items())
        ]
        lines.append(
            {
                "account_code": "1002",
                "debit": cents_to_money(source["amount_cents"] if side == "credit" else 0),
                "credit": cents_to_money(source["amount_cents"] if side == "debit" else 0),
                "auxiliary_values": {},
            }
        )
        return {
            "lines": lines,
            "issues": list(dict.fromkeys(issues)),
            "allocated": cents_to_money(sum(row["amount_cents"] for row in rows)),
            "requires_accounting_review": True,
        }

    def blocking_issues_in_transaction(self, conn, actor, source, lines=None):
        if source["source_kind"] != "bank" or actor is None:
            return []
        clearance = self.bank_clearance(conn, actor, source)
        if clearance is None:
            return []
        issues = list(clearance["issues"])
        actual = defaultdict(lambda: [0, 0])
        if lines is None:
            lines = conn.execute(
                "SELECT l.* FROM draft_lines l JOIN drafts d ON d.id=l.draft_id AND d.book_id=l.book_id WHERE d.source_id=? AND d.book_id=?",
                (source["id"], source["book_id"]),
            ).fetchall()
        for line in lines:
            actual[line["account_code"]][0] += line["debit_cents"]
            actual[line["account_code"]][1] += line["credit_cents"]
        expected = {
            line["account_code"]: [money_to_cents(line["debit"]), money_to_cents(line["credit"])]
            for line in clearance["lines"]
        }
        if dict(actual) != expected:
            issues.append(
                "银行凭证须按核销金额清偿对应应收/应付并记银行存款，不得重复计入费用或收入"
            )
        return list(dict.fromkeys(issues))

    def overview(self, actor, book_id, limit=20, offset=0):
        if type(limit) is not int or not 1 <= limit <= 100 or type(offset) is not int or offset < 0:
            raise AccountingError("分页参数无效", 422)
        with self.db.transaction(actor=actor) as conn:
            book = self._book(conn, actor, book_id)
            total = conn.execute(
                "SELECT COUNT(*) FROM settlement_items WHERE book_id=? AND firm_id=?",
                (book_id, actor.firm_id),
            ).fetchone()[0]
            rows = conn.execute(
                "SELECT id FROM settlement_items WHERE book_id=? AND firm_id=? ORDER BY created_at,id LIMIT ? OFFSET ?",
                (book_id, actor.firm_id, limit, offset),
            ).fetchall()
            items = [
                self._item_json(self._item(conn, actor, book, row["id"], current=False))
                for row in rows
            ]
            candidates = []
            for candidate in conn.execute(
                "SELECT s.*,d.id AS draft_id,d.version AS draft_version,d.status AS draft_status FROM sources s JOIN drafts d ON d.source_id=s.id AND d.book_id=s.book_id WHERE s.book_id=? AND s.source_kind='business' AND d.status='draft' AND NOT EXISTS(SELECT 1 FROM settlement_items i WHERE i.source_id=s.id) ORDER BY s.date,s.id LIMIT 100",
                (book_id,),
            ):
                try:
                    facts = self._business(conn, actor, book, dict(candidate))
                except AccountingError:
                    continue
                party = self.master.resolve_in_transaction(
                    conn,
                    actor,
                    book["company_id"],
                    "counterparty",
                    candidate["counterparty"],
                    book_id,
                )
                candidates.append(
                    {
                        "source_id": candidate["id"],
                        "description": candidate["description"],
                        "date": candidate["date"],
                        "amount": cents_to_money(candidate["amount_cents"]),
                        "counterparty": candidate["counterparty"],
                        "counterparty_id": party["entity_id"] if party else None,
                        "draft_version": candidate["draft_version"],
                        "clearing_account": facts["clearing_account"],
                    }
                )
            banks = []
            for row in conn.execute(
                "SELECT s.*,d.id AS draft_id,d.version AS draft_version,d.status AS draft_status FROM sources s JOIN drafts d ON d.source_id=s.id AND d.book_id=s.book_id WHERE s.book_id=? AND s.source_kind='bank' AND d.status='draft' ORDER BY s.date,s.id LIMIT 100",
                (book_id,),
            ):
                party = self.master.resolve_in_transaction(
                    conn, actor, book["company_id"], "counterparty", row["counterparty"], book_id
                )
                banks.append(
                    {
                        "id": row["id"],
                        "date": row["date"],
                        "description": row["description"],
                        "counterparty": row["counterparty"],
                        "counterparty_id": party["entity_id"] if party else None,
                        "direction": row["direction"],
                        "amount": cents_to_money(row["amount_cents"]),
                        "remaining": cents_to_money(
                            row["amount_cents"] - self._used(conn, "bank_source_id", row["id"])
                        ),
                        "draft_id": row["draft_id"],
                        "draft_version": row["draft_version"],
                    }
                )
            allocations = [
                dict(row)
                | {
                    "amount": cents_to_money(row["amount_cents"]),
                    "fingerprint": self._allocation_fingerprint(conn, row),
                    "can_reverse": row["created_by"] != actor.user_id
                    and not row["reversed"]
                    and book["period_status"] == "open"
                    and self._source(conn, book_id, row["bank_source_id"])["draft_status"]
                    == "draft",
                }
                for row in conn.execute(
                    "SELECT a.*,EXISTS(SELECT 1 FROM settlement_reversals r WHERE r.allocation_id=a.id) AS reversed FROM settlement_allocations a WHERE a.book_id=? ORDER BY a.created_at DESC,a.id LIMIT 100",
                    (book_id,),
                )
            ]
            return {
                "book": {
                    "id": book_id,
                    "name": book["name"],
                    "period": book["period"],
                    "period_status": book["period_status"],
                },
                "items": items,
                "candidates": candidates,
                "banks": banks,
                "allocations": allocations,
                "total": total,
                "limit": limit,
                "offset": offset,
                "has_more": offset + len(items) < total,
                "currency": "CNY",
                "scope": "same_open_period_auxiliary_settlement",
            }

    def clearance(self, actor, book_id, source_id):
        with self.db.transaction(actor=actor) as conn:
            self._book(conn, actor, book_id)
            source = self._source(conn, book_id, source_id)
            result = self.bank_clearance(conn, actor, source)
            if result is None:
                raise AccountingError("该银行来源没有当前核销分配", 404)
            return result | {
                "draft_id": source["draft_id"],
                "draft_version": source["draft_version"],
                "description": source["description"] + "（应收/应付清偿）",
            }


from .models import AllocationPreview
