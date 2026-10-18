"""Long-term open-item subledger. Allocation never creates a second payment voucher."""

import hashlib
import json
from datetime import date, timedelta
from uuid import uuid4

from ..accounting.domain import AccountingError
from ..accounting.invariants import money_to_cents, signed_money_to_cents
from ..accounting.service import AccountingService, now
from .models import (
    AllocationCreate,
    AllocationReverse,
    BankAdjustment,
    BankLineCreate,
    BankMatch,
    OpenItemCreate,
)


def _fingerprint(value):
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


class SubledgerService:
    def __init__(self, db):
        self.db = db
        self.accounting = AccountingService(db)

    def _book(self, conn, actor, book_id, write=False):
        book = self.accounting.access.book(conn, actor, book_id, "review" if write else "read")
        ledger = conn.execute(
            "SELECT ledger_id FROM ledger_periods WHERE book_id=?", (book["id"],)
        ).fetchone()
        if not ledger:
            raise AccountingError("账套未关联长期账套", 409)
        return book, ledger["ledger_id"]

    def _remaining(self, conn, item_id, as_of=None):
        allocation_sql = (
            "SELECT COALESCE(SUM(a.amount_cents),0) FROM subledger_allocations a WHERE a.item_id=? "
        )
        reversal_sql = (
            "SELECT COALESCE(SUM(a.amount_cents),0) FROM subledger_allocations a "
            "JOIN subledger_allocation_reversals r ON r.allocation_id=a.id "
            "WHERE a.item_id=? "
        )
        params = [item_id]
        if as_of:
            allocation_sql += "AND a.allocated_on<=? "
            reversal_sql += "AND r.created_at<=? "
            params.append(as_of)
        allocated = conn.execute(allocation_sql, tuple(params)).fetchone()[0]
        reversed_amount = conn.execute(reversal_sql, tuple(params)).fetchone()[0]
        return int(allocated) - int(reversed_amount)

    def create_item(self, actor, request: OpenItemCreate):
        with self.db.transaction(write=True, actor=actor) as conn:
            book, ledger_id = self._book(conn, actor, request.book_id, write=True)
            account = conn.execute(
                "SELECT a.current_version,v.code,v.name,v.is_leaf FROM ledger_accounts a "
                "JOIN ledger_account_versions v ON v.account_id=a.id AND v.version=? "
                "WHERE a.id=? AND a.ledger_id=?",
                (request.control_account_version, request.control_account_id, ledger_id),
            ).fetchone()
            if not account or int(account["current_version"]) != request.control_account_version:
                raise AccountingError("控制科目版本已变化或不属于当前账套", 409)
            if not account["is_leaf"]:
                raise AccountingError("控制科目必须是末级科目", 422)
            party = conn.execute(
                "SELECT 1 FROM company_master_entities WHERE id=? AND firm_id=? "
                "AND company_id=? AND kind='counterparty' AND active=1",
                (request.counterparty_id, book["firm_id"], book["company_id"]),
            ).fetchone()
            if not party:
                raise AccountingError("往来方不存在、停用或不属于本企业", 404)
            item_id = uuid4().hex
            conn.execute(
                "INSERT INTO subledger_items(id,firm_id,company_id,ledger_id,book_id,period,kind,"
                "counterparty_id,control_account_id,control_account_version,original_date,due_date,"
                "amount_cents,source_kind,source_id,source_version,status,created_by,created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'open',?,?)",
                (
                    item_id,
                    book["firm_id"],
                    book["company_id"],
                    ledger_id,
                    book["id"],
                    book["period"],
                    request.kind,
                    request.counterparty_id,
                    request.control_account_id,
                    request.control_account_version,
                    request.original_date,
                    request.due_date,
                    money_to_cents(request.amount),
                    request.source_kind,
                    request.source_id,
                    request.source_version,
                    actor.user_id,
                    now(),
                ),
            )
            self._audit(
                conn,
                actor,
                book["id"],
                "subledger.item.create",
                item_id,
                {
                    "kind": request.kind,
                    "amount_cents": money_to_cents(request.amount),
                },
            )
            return self._item_view(conn, item_id)

    def _item_view(self, conn, item_id):
        row = conn.execute("SELECT * FROM subledger_items WHERE id=?", (item_id,)).fetchone()
        if not row:
            raise AccountingError("未结事项不存在", 404)
        result = dict(row)
        result["allocated_cents"] = self._remaining(conn, item_id)
        result["remaining_cents"] = int(row["amount_cents"]) - result["allocated_cents"]
        return result

    def list_items(self, actor, book_id, kind=None, as_of=None):
        with self.db.transaction(actor=actor) as conn:
            book, _ = self._book(conn, actor, book_id)
            if kind:
                rows = conn.execute(
                    "SELECT * FROM subledger_items WHERE book_id=? AND kind=? ORDER BY original_date,id",
                    (book["id"], kind),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM subledger_items WHERE book_id=? ORDER BY original_date,id",
                    (book["id"],),
                ).fetchall()
            result = []
            for row in rows:
                value = dict(row)
                allocated = self._remaining(conn, row["id"], as_of)
                value["allocated_cents"] = allocated
                value["remaining_cents"] = int(row["amount_cents"]) - allocated
                result.append(value)
            return result

    def _voucher_control_amount(self, conn, voucher_id, item):
        column = (
            "credit_cents" if item["kind"] in {"receivable", "advance_received"} else "debit_cents"
        )
        row = conn.execute(
            "SELECT COALESCE(SUM(" + column + "),0) AS total FROM voucher_lines "
            "WHERE voucher_id=? AND account_id=?",
            (voucher_id, item["control_account_id"]),
        ).fetchone()
        return int(row["total"])

    def allocate(self, actor, request: AllocationCreate):
        fingerprint = _fingerprint(request.model_dump())
        with self.db.transaction(write=True, actor=actor) as conn:
            book, ledger_id = self._book(conn, actor, request.book_id, write=True)
            prior = conn.execute(
                "SELECT * FROM subledger_allocation_requests WHERE firm_id=? AND idempotency_key=?",
                (actor.firm_id, request.idempotency_key),
            ).fetchone()
            if prior:
                if prior["request_fingerprint"] != fingerprint:
                    raise AccountingError("核销幂等键已用于不同内容", 409)
                return self._allocation_view(conn, prior["voucher_id"])
            voucher = conn.execute(
                "SELECT * FROM vouchers WHERE id=? AND book_id=?",
                (request.voucher_id, book["id"]),
            ).fetchone()
            if not voucher:
                raise AccountingError("收付款凭证不存在或不属于当前账套", 404)
            items = []
            seen_items = set()
            for part in request.parts:
                item = conn.execute(
                    "SELECT * FROM subledger_items WHERE id=? AND book_id=? AND status IN('open','partial')",
                    (part.item_id, book["id"]),
                ).fetchone()
                if not item:
                    raise AccountingError("未结事项不存在或已结清", 404)
                if item["id"] in seen_items:
                    raise AccountingError("同一请求不能重复分配同一未结事项", 422)
                seen_items.add(item["id"])
                amount = money_to_cents(part.amount)
                if amount > int(item["amount_cents"]) - self._remaining(conn, item["id"]):
                    raise AccountingError("分配金额超过未结余额", 409)
                items.append((item, amount))
            total = sum(amount for _, amount in items)
            control_accounts = {item["control_account_id"] for item, _ in items}
            if len(control_accounts) != 1:
                raise AccountingError("一次核销只能使用一个控制科目", 422)
            control_amount = self._voucher_control_amount(conn, voucher["id"], items[0][0])
            already = conn.execute(
                "SELECT COALESCE(SUM(a.amount_cents),0) FROM subledger_allocations a "
                "WHERE a.voucher_id=? AND a.item_id IN (SELECT id FROM subledger_items WHERE control_account_id=?) "
                "AND NOT EXISTS(SELECT 1 FROM subledger_allocation_reversals r WHERE r.allocation_id=a.id)",
                (voucher["id"], items[0][0]["control_account_id"]),
            ).fetchone()[0]
            if total + int(already) > control_amount:
                raise AccountingError("核销金额超过凭证控制科目金额", 409)
            request_id = uuid4().hex
            conn.execute(
                "INSERT INTO subledger_allocation_requests(id,firm_id,company_id,book_id,voucher_id,"
                "idempotency_key,request_fingerprint,total_cents,created_by,created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    request_id,
                    book["firm_id"],
                    book["company_id"],
                    book["id"],
                    voucher["id"],
                    request.idempotency_key,
                    fingerprint,
                    total,
                    actor.user_id,
                    now(),
                ),
            )
            for item, amount in items:
                conn.execute(
                    "INSERT INTO subledger_allocations(id,firm_id,company_id,book_id,item_id,voucher_id,"
                    "amount_cents,allocated_on,idempotency_key,request_fingerprint,created_by,created_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        uuid4().hex,
                        book["firm_id"],
                        book["company_id"],
                        book["id"],
                        item["id"],
                        voucher["id"],
                        amount,
                        voucher["voucher_date"],
                        request.idempotency_key + ":" + item["id"],
                        fingerprint,
                        actor.user_id,
                        now(),
                    ),
                )
            self._audit(
                conn,
                actor,
                book["id"],
                "subledger.allocate",
                voucher["id"],
                {
                    "items": [item["id"] for item, _ in items],
                    "total_cents": total,
                    "idempotency_key": request.idempotency_key,
                },
            )
            return self._allocation_view(conn, voucher["id"])

    def _allocation_view(self, conn, voucher_id):
        rows = conn.execute(
            "SELECT a.*,r.id AS reversal_id FROM subledger_allocations a "
            "LEFT JOIN subledger_allocation_reversals r ON r.allocation_id=a.id "
            "WHERE a.voucher_id=? ORDER BY a.created_at,a.id",
            (voucher_id,),
        ).fetchall()
        return {"voucher_id": voucher_id, "allocations": [dict(row) for row in rows]}

    def reverse_allocation(self, actor, allocation_id, request: AllocationReverse):
        fingerprint = _fingerprint(request.model_dump())
        with self.db.transaction(write=True, actor=actor) as conn:
            allocation = conn.execute(
                "SELECT a.*,i.book_id FROM subledger_allocations a "
                "JOIN subledger_items i ON i.id=a.item_id WHERE a.id=?",
                (allocation_id,),
            ).fetchone()
            if not allocation:
                raise AccountingError("核销记录不存在", 404)
            book, _ = self._book(conn, actor, allocation["book_id"], write=True)
            prior = conn.execute(
                "SELECT * FROM subledger_allocation_reversals WHERE firm_id=? AND idempotency_key=?",
                (actor.firm_id, request.idempotency_key),
            ).fetchone()
            if prior:
                if prior["request_fingerprint"] != fingerprint:
                    raise AccountingError("撤销核销幂等键已用于不同内容", 409)
                return {"allocation_id": allocation_id, "reversal_id": prior["id"]}
            if conn.execute(
                "SELECT 1 FROM subledger_allocation_reversals WHERE allocation_id=?",
                (allocation_id,),
            ).fetchone():
                raise AccountingError("该核销记录已经撤销", 409)
            conn.execute(
                "INSERT INTO subledger_allocation_reversals(id,firm_id,company_id,book_id,allocation_id,"
                "reason,idempotency_key,request_fingerprint,created_by,created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    uuid4().hex,
                    book["firm_id"],
                    book["company_id"],
                    book["id"],
                    allocation_id,
                    request.reason,
                    request.idempotency_key,
                    fingerprint,
                    actor.user_id,
                    now(),
                ),
            )
            self._audit(conn, actor, book["id"], "subledger.allocate.reverse", allocation_id, {})
            return {"allocation_id": allocation_id, "reversed": True}

    def aging(self, actor, book_id, as_of):
        with self.db.transaction(actor=actor) as conn:
            book, _ = self._book(conn, actor, book_id)
            cutoff = date.fromisoformat(as_of)
            rows = conn.execute(
                "SELECT * FROM subledger_items WHERE book_id=? AND status IN('open','partial') "
                "ORDER BY due_date,original_date,id",
                (book["id"],),
            ).fetchall()
            buckets = {"current": 0, "1_30": 0, "31_60": 0, "61_90": 0, "over_90": 0}
            detail = []
            for row in rows:
                remaining = int(row["amount_cents"]) - self._remaining(conn, row["id"], as_of)
                if remaining <= 0:
                    continue
                due = date.fromisoformat(row["due_date"] or row["original_date"])
                days = (cutoff - due).days
                if days <= 0:
                    bucket = "current"
                elif days <= 30:
                    bucket = "1_30"
                elif days <= 60:
                    bucket = "31_60"
                elif days <= 90:
                    bucket = "61_90"
                else:
                    bucket = "over_90"
                buckets[bucket] += remaining
                detail.append(
                    {**dict(row), "remaining_cents": remaining, "age_days": days, "bucket": bucket}
                )
            return {"as_of": as_of, "buckets": buckets, "items": detail}

    def control_reconciliation(self, actor, book_id, as_of=None):
        with self.db.transaction(actor=actor) as conn:
            return self.control_reconciliation_in_transaction(conn, actor, book_id, as_of)

    def control_reconciliation_in_transaction(self, conn, actor, book_id, as_of=None, trial=None):
        from ..posting.service import PostingService

        groups = {}
        book, _ = self._book(conn, actor, book_id)
        for kind in ("receivable", "payable", "advance_received", "advance_paid"):
            items = conn.execute(
                "SELECT * FROM subledger_items WHERE book_id=? AND kind=? AND status IN('open','partial')",
                (book["id"], kind),
            ).fetchall()
            for item in items:
                remaining = int(item["amount_cents"]) - self._remaining(conn, item["id"], as_of)
                if remaining > 0:
                    groups.setdefault(kind, {})
                    groups[kind][item["control_account_id"]] = (
                        groups[kind].get(item["control_account_id"], 0) + remaining
                    )
        trial = trial or PostingService(self.db).trial_balance_in_transaction(
            conn, actor, book_id, as_of
        )
        balances = {row["account_id"]: row for row in trial["rows"]}
        # Reverse-enumerate configured control accounts from the trial balance.
        # A payable with a book balance but no subledger items must produce a
        # failed row, not an empty all([]) pass.
        known_controls = {
            "1122": "receivable",
            "1123": "advance_paid",
            "2202": "payable",
            "2203": "advance_received",
        }
        for row in trial["rows"]:
            code = str(row.get("code", ""))
            kind = next(
                (value for prefix, value in known_controls.items() if code.startswith(prefix)), None
            )
            if kind and row.get("is_leaf"):
                groups.setdefault(kind, {}).setdefault(row["account_id"], 0)
        result = []
        for kind, by_account in groups.items():
            for account_id, subledger in by_account.items():
                account = balances.get(account_id, {})
                book_balance = int(account.get("closing_cents", 0))
                expected = subledger if kind in {"receivable", "advance_paid"} else -subledger
                result.append(
                    {
                        "kind": kind,
                        "control_account_id": account_id,
                        "subledger_cents": subledger,
                        "book_cents": book_balance,
                        "difference_cents": book_balance - expected,
                        "passed": book_balance == expected,
                    }
                )
        return {"rows": result, "passed": all(row["passed"] for row in result)}

    # ---------- bank evidence ----------

    def add_bank_line(self, actor, request: BankLineCreate):
        with self.db.transaction(write=True, actor=actor) as conn:
            book, _ = self._book(conn, actor, request.book_id, write=True)
            line_id = uuid4().hex
            conn.execute(
                "INSERT INTO bank_statement_lines(id,book_id,period,line_date,description,"
                "amount_cents,status,created_by,created_at) VALUES(?,?,?,?,?,?,'unmatched',?,?)",
                (
                    line_id,
                    book["id"],
                    book["period"],
                    request.line_date,
                    request.description,
                    signed_money_to_cents(request.amount),
                    actor.user_id,
                    now(),
                ),
            )
            return {"id": line_id, **request.model_dump()}

    def match_bank_line(self, actor, line_id, request: BankMatch):
        with self.db.transaction(write=True, actor=actor) as conn:
            line = conn.execute(
                "SELECT * FROM bank_statement_lines WHERE id=?", (line_id,)
            ).fetchone()
            if not line:
                raise AccountingError("银行流水不存在", 404)
            book, _ = self._book(conn, actor, line["book_id"], write=True)
            voucher_line = conn.execute(
                "SELECT l.*,v.book_id,v.voucher_date FROM voucher_lines l "
                "JOIN vouchers v ON v.id=l.voucher_id WHERE l.id=? AND v.book_id=?",
                (request.voucher_line_id, book["id"]),
            ).fetchone()
            if not voucher_line:
                raise AccountingError("正式分录不存在或不属于当前账套", 404)
            if not voucher_line["code_snapshot"].startswith(("1001", "1002")):
                raise AccountingError("只能匹配现金/银行正式分录", 422)
            amount = int(voucher_line["debit_cents"]) - int(voucher_line["credit_cents"])
            if amount != int(line["amount_cents"]):
                raise AccountingError("银行流水金额与正式分录不一致", 409)
            if line["status"] != "unmatched":
                raise AccountingError("银行流水已匹配或已排除", 409)
            conn.execute(
                "UPDATE bank_statement_lines SET matched_voucher_line_id=?,status='matched' WHERE id=?",
                (voucher_line["id"], line_id),
            )
            return {"line_id": line_id, "voucher_line_id": voucher_line["id"], "status": "matched"}

    def add_bank_adjustment(self, actor, request: BankAdjustment):
        with self.db.transaction(write=True, actor=actor) as conn:
            book, _ = self._book(conn, actor, request.book_id, write=True)
            adjustment_id = uuid4().hex
            conn.execute(
                "INSERT INTO bank_recon_adjustments(id,book_id,period,kind,amount_cents,"
                "explanation,status,created_by,created_at) VALUES(?,?,?,?,?,?,'open',?,?)",
                (
                    adjustment_id,
                    book["id"],
                    request.period,
                    request.kind,
                    signed_money_to_cents(request.amount),
                    request.explanation,
                    actor.user_id,
                    now(),
                ),
            )
            return {"id": adjustment_id, "status": "open"}

    def bank_reconciliation(self, actor, book_id):
        with self.db.transaction(actor=actor) as conn:
            return self.bank_reconciliation_in_transaction(conn, actor, book_id)

    def bank_reconciliation_in_transaction(self, conn, actor, book_id, trial=None):
        from ..posting.service import PostingService

        book, _ = self._book(conn, actor, book_id)
        statement = conn.execute(
            "SELECT COALESCE(SUM(amount_cents),0) FROM bank_statement_lines WHERE book_id=?",
            (book["id"],),
        ).fetchone()[0]
        statement_count = conn.execute(
            "SELECT COUNT(*) FROM bank_statement_lines WHERE book_id=?", (book["id"],)
        ).fetchone()[0]
        unmatched = conn.execute(
            "SELECT COUNT(*),COALESCE(SUM(amount_cents),0) FROM bank_statement_lines "
            "WHERE book_id=? AND status='unmatched'",
            (book["id"],),
        ).fetchone()
        adjustments = conn.execute(
            "SELECT * FROM bank_recon_adjustments WHERE book_id=? ORDER BY created_at,id",
            (book["id"],),
        ).fetchall()
        trial = trial or PostingService(self.db).trial_balance_in_transaction(conn, actor, book_id)
        cash_rows = [
            row
            for row in trial["rows"]
            if row["is_leaf"] and row["code"].startswith(("1001", "1002"))
        ]
        cash = sum(int(row["closing_cents"]) for row in cash_rows)
        cash_opening = sum(int(row.get("opening_cents", 0)) for row in cash_rows)
        unexplained = sum(
            int(row["amount_cents"]) for row in adjustments if row["status"] == "open"
        )
        # Missing bank evidence is not a pass. A zero-cash book may legitimately
        # have no statement; any nonzero cash needs at least one evidence line.
        coverage_ok = int(statement_count) > 0 or cash == 0
        difference = cash - (cash_opening + int(statement))
        return {
            "statement_cents": int(statement),
            "statement_line_count": int(statement_count),
            "book_cash_cents": cash,
            "opening_cash_cents": cash_opening,
            "unmatched_count": int(unmatched[0]),
            "unmatched_cents": int(unmatched[1]),
            "adjustments": [dict(row) for row in adjustments],
            "unexplained_cents": unexplained,
            "difference_cents": difference,
            "coverage_ok": coverage_ok,
            "passed": coverage_ok
            and unexplained == 0
            and int(unmatched[0]) == 0
            and difference == 0,
        }

    @staticmethod
    def _audit(conn, actor, book_id, action, object_id, details):
        conn.execute(
            "INSERT INTO audit_events(firm_id,book_id,user_id,action,object_id,details,created_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (
                actor.firm_id,
                book_id,
                actor.user_id,
                action,
                object_id,
                json.dumps(details, ensure_ascii=False),
                now(),
            ),
        )
