"""Long-term open-item subledger. Allocation never creates a second payment voucher."""

import hashlib
from calendar import monthrange
import json
from datetime import date, timedelta
from uuid import uuid4

from ..accounting.domain import AccountingError
from ..accounting.invariants import money_to_cents, signed_money_to_cents
from ..accounting.service import AccountingService, now
from .reconciliation import auxiliary, settlement_side, control_rows, bank_rows
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
    def __init__(self, db, storage=None):
        self.db = db
        self.storage = storage
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
            reversal_sql += "AND SUBSTR(r.created_at,1,10)<=? "
            params.append(as_of)
        allocated = conn.execute(allocation_sql, tuple(params)).fetchone()[0]
        reversed_amount = conn.execute(reversal_sql, tuple(params)).fetchone()[0]
        return int(allocated) - int(reversed_amount)

    def _control_state(self, conn, ledger_id, period):
        period = period[:7]
        row = conn.execute(
            "SELECT * FROM subledger_control_applicability WHERE ledger_id=? "
            "AND effective_from<=? AND (effective_to IS NULL OR effective_to>=?) "
            "ORDER BY effective_from DESC LIMIT 1",
            (ledger_id, period, period),
        ).fetchone()
        if row:
            return row
        if self._control_configs(conn, ledger_id, as_of=period):
            return {
                "status": "configured",
                "reason": "",
                "effective_from": period,
                "effective_to": None,
            }
        return None

    def _has_any_control_config(self, conn, ledger_id):
        return bool(
            conn.execute(
                "SELECT 1 FROM subledger_control_accounts WHERE ledger_id=? LIMIT 1",
                (ledger_id,),
            ).fetchone()
        )

    def set_control_applicability(self, actor, request):
        from .models import ControlApplicability

        if not isinstance(request, ControlApplicability):
            raise AccountingError("控制科目适用性声明无效", 422)
        if request.confirmed_by and request.confirmed_by != actor.user_id:
            raise AccountingError("确认身份必须来自当前登录主体", 403)
        with self.db.transaction(write=True, actor=actor) as conn:
            book, ledger_id = self._book(conn, actor, request.book_id, write=True)
            if request.effective_to and request.effective_to < request.effective_from:
                raise AccountingError("适用性期间起止倒置", 422)
            from ..posting.service import PostingService

            trial = PostingService(self.db).trial_balance_in_transaction(conn, actor, book["id"])
            legacy_nonzero = [
                row
                for row in trial["rows"]
                if str(row.get("code", "")).startswith(("1122", "1123", "2202", "2203"))
                and any(
                    int(row.get(field, 0))
                    for field in ("opening_cents", "debit_cents", "credit_cents", "closing_cents")
                )
            ]
            if (
                self._has_any_control_config(conn, ledger_id)
                or conn.execute(
                    "SELECT 1 FROM subledger_items WHERE book_id=? LIMIT 1", (book["id"],)
                ).fetchone()
                or legacy_nonzero
            ):
                raise AccountingError("已有控制科目余额、配置或子账事项，不能声明无需子账", 409)
            if conn.execute(
                "SELECT 1 FROM subledger_control_applicability WHERE ledger_id=? "
                "AND effective_from=?",
                (ledger_id, request.effective_from),
            ).fetchone():
                raise AccountingError("该期间已有适用性声明", 409)
            stamp = now()
            declaration_id = uuid4().hex
            conn.execute(
                "INSERT INTO subledger_control_applicability(id,ledger_id,firm_id,company_id,status,"
                "effective_from,effective_to,reason,confirmed_by,confirmed_at,created_at) "
                "VALUES(?,?,?,?,'not_applicable',?,?,?,?,?,?)",
                (
                    declaration_id,
                    ledger_id,
                    book["firm_id"],
                    book["company_id"],
                    request.effective_from,
                    request.effective_to,
                    request.reason,
                    actor.user_id,
                    stamp,
                    stamp,
                ),
            )
            if not self.db.is_postgres:
                conn.execute(
                    "INSERT INTO close_dependency_revisions(scope_kind,scope_id,revision) "
                    "VALUES('ledger',?,1) ON CONFLICT(scope_kind,scope_id) DO UPDATE "
                    "SET revision=close_dependency_revisions.revision+1",
                    (ledger_id,),
                )
            self._audit(
                conn,
                actor,
                book["id"],
                "subledger.control.not_applicable",
                ledger_id,
                {
                    "reason": request.reason,
                    "effective_from": request.effective_from,
                    "effective_to": request.effective_to,
                },
            )
            return dict(self._control_state(conn, ledger_id, request.effective_from))

    def _control_configs(self, conn, ledger_id, kind=None, as_of=None):
        sql = (
            "SELECT * FROM subledger_control_accounts WHERE ledger_id=? AND active IS TRUE "
            "AND effective_from<=? AND (effective_to IS NULL OR effective_to>=?)"
        )
        period = (as_of or "")[:7]
        params = [ledger_id, period, period]
        if kind:
            sql += " AND kind=?"
            params.append(kind)
        return conn.execute(sql, tuple(params)).fetchall()

    def configure_control_account(self, actor, request):
        from .models import ControlAccountCreate

        if not isinstance(request, ControlAccountCreate):
            raise AccountingError("控制科目配置无效", 422)
        with self.db.transaction(write=True, actor=actor) as conn:
            book, ledger_id = self._book(conn, actor, request.book_id, write=True)
            if request.effective_from and len(request.effective_from) not in {7, 10}:
                raise AccountingError("控制科目生效期间格式必须为 YYYY-MM", 422)
            if request.effective_to and request.effective_to < request.effective_from:
                raise AccountingError("控制科目生效期间起止倒置", 422)
            if not all(isinstance(value, str) and value.strip() for value in request.required_aux):
                raise AccountingError("必要辅助维度名称不能为空", 422)
            if len(set(request.required_aux)) != len(request.required_aux):
                raise AccountingError("必要辅助维度不能重复", 422)
            overlap_sql = (
                "SELECT 1 FROM subledger_control_accounts WHERE ledger_id=? AND kind=? "
                "AND active IS TRUE AND (effective_to IS NULL OR effective_to>=?)"
            )
            overlap_params = [ledger_id, request.kind, request.effective_from]
            if request.effective_to is not None:
                overlap_sql += " AND effective_from<=?"
                overlap_params.append(request.effective_to)
            overlap_sql += " LIMIT 1"
            if conn.execute(overlap_sql, tuple(overlap_params)).fetchone():
                raise AccountingError("控制科目生效期间与现有配置重叠", 409)
            existing = conn.execute(
                "SELECT 1 FROM subledger_control_accounts WHERE ledger_id=? AND kind=? AND "
                "control_account_id=? AND effective_from=?",
                (ledger_id, request.kind, request.control_account_id, request.effective_from),
            ).fetchone()
            if existing:
                raise AccountingError("相同控制科目配置已存在", 409)
            account = conn.execute(
                "SELECT a.current_version,v.is_leaf,v.auxiliary_requirements FROM ledger_accounts a "
                "JOIN ledger_account_versions v ON v.account_id=a.id AND v.version=? "
                "WHERE a.id=? AND a.ledger_id=?",
                (request.control_account_version, request.control_account_id, ledger_id),
            ).fetchone()
            if not account or int(account["current_version"]) != request.control_account_version:
                raise AccountingError("控制科目版本已变化或不属于当前账套", 409)
            if not account["is_leaf"]:
                raise AccountingError("控制科目必须是末级科目", 422)
            from .auxiliary import validate_required_aux

            validate_required_aux(
                request.required_aux, json.loads(account["auxiliary_requirements"])
            )
            config_id = uuid4().hex
            conn.execute(
                "INSERT INTO subledger_control_accounts(id,firm_id,company_id,ledger_id,kind,"
                "control_account_id,control_account_version,required_aux_json,effective_from,"
                "effective_to,active,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,TRUE,?,?)",
                (
                    config_id,
                    book["firm_id"],
                    book["company_id"],
                    ledger_id,
                    request.kind,
                    request.control_account_id,
                    request.control_account_version,
                    json.dumps(sorted(set(request.required_aux)), ensure_ascii=False),
                    request.effective_from,
                    request.effective_to,
                    actor.user_id,
                    now(),
                ),
            )
            if not self.db.is_postgres:
                conn.execute(
                    "INSERT INTO close_dependency_revisions(scope_kind,scope_id,revision) "
                    "VALUES('ledger',?,1) ON CONFLICT(scope_kind,scope_id) DO UPDATE "
                    "SET revision=close_dependency_revisions.revision+1",
                    (ledger_id,),
                )
            self._audit(
                conn,
                actor,
                book["id"],
                "subledger.control.configure",
                config_id,
                {
                    "kind": request.kind,
                    "account_id": request.control_account_id,
                    "effective_from": request.effective_from,
                    "effective_to": request.effective_to,
                },
            )
            return dict(
                conn.execute(
                    "SELECT * FROM subledger_control_accounts WHERE id=?", (config_id,)
                ).fetchone()
            )

    def list_control_accounts(self, actor, book_id, as_of=None):
        with self.db.transaction(actor=actor) as conn:
            book, ledger_id = self._book(conn, actor, book_id)
            period = as_of or book["period"]
            return [dict(row) for row in self._control_configs(conn, ledger_id, as_of=period)]

    def create_item(self, actor, request: OpenItemCreate):
        with self.db.transaction(write=True, actor=actor) as conn:
            book, ledger_id = self._book(conn, actor, request.book_id, write=True)
            if date.fromisoformat(request.original_date).strftime("%Y-%m") > book["period"]:
                raise AccountingError("未结事项原始日期不能晚于当前账套期末", 422)
            state = self._control_state(conn, ledger_id, request.original_date)
            if not state or state["status"] != "configured":
                raise AccountingError("控制科目配置待确认，不能新建子账事项", 409)
            configs = self._control_configs(conn, ledger_id, request.kind, request.original_date)
            if not configs:
                raise AccountingError("当前期间缺少该子账类型的控制科目配置", 409)
            matched = next(
                (
                    row
                    for row in configs
                    if row["control_account_id"] == request.control_account_id
                    and int(row["control_account_version"]) == request.control_account_version
                ),
                None,
            )
            if not matched:
                raise AccountingError("控制科目未在核算配置中声明或版本不匹配", 409)
            from .auxiliary import validate_auxiliary_entities

            auxiliary = validate_auxiliary_entities(
                conn,
                book,
                json.loads(matched["required_aux_json"] or "[]"),
                request.aux,
                request.counterparty_id,
            )
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
                "amount_cents,source_kind,source_id,source_version,status,aux_json,created_by,created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'open',?,?,?)",
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
                    json.dumps(auxiliary, ensure_ascii=False, sort_keys=True),
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
        result["aux"] = json.loads(result.get("aux_json") or "{}")
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
                if as_of and row["original_date"] > as_of:
                    continue
                value = dict(row)
                allocated = self._remaining(conn, row["id"], as_of)
                value["allocated_cents"] = allocated
                value["remaining_cents"] = int(row["amount_cents"]) - allocated
                result.append(value)
            return result

    def _voucher_control_amount(self, conn, voucher_id, item):
        column = settlement_side(item["kind"])
        return sum(
            int(row[column])
            for row in conn.execute(
                "SELECT aux_json,debit_cents,credit_cents FROM voucher_lines WHERE voucher_id=? AND account_id=?",
                (voucher_id, item["control_account_id"]),
            )
            if auxiliary(row["aux_json"]) == auxiliary(item["aux_json"])
        )

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
                if item["original_date"] > voucher["voucher_date"]:
                    raise AccountingError("核销凭证不能早于未结事项原始日期", 422)
                amount = money_to_cents(part.amount)
                if amount > int(item["amount_cents"]) - self._remaining(conn, item["id"]):
                    raise AccountingError("分配金额超过未结余额", 409)
                items.append((item, amount))
            total = sum(amount for _, amount in items)
            control_accounts = {item["control_account_id"] for item, _ in items}
            if len(control_accounts) != 1:
                raise AccountingError("一次核销只能使用一个控制科目", 422)
            grouped = {}
            for item, amount in items:
                key = (
                    item["control_account_id"],
                    auxiliary(item["aux_json"]),
                    settlement_side(item["kind"]),
                )
                grouped.setdefault(key, [item, 0])[1] += amount
            for key, (item, amount) in grouped.items():
                already = sum(
                    int(row["amount_cents"])
                    for row in conn.execute(
                        "SELECT a.amount_cents,i.aux_json,i.kind FROM subledger_allocations a JOIN subledger_items i ON i.id=a.item_id "
                        "WHERE a.voucher_id=? AND i.control_account_id=? "
                        "AND NOT EXISTS(SELECT 1 FROM subledger_allocation_reversals r WHERE r.allocation_id=a.id)",
                        (voucher["id"], item["control_account_id"]),
                    )
                    if auxiliary(row["aux_json"]) == key[1]
                    and settlement_side(row["kind"]) == key[2]
                )
                if amount + already > self._voucher_control_amount(conn, voucher["id"], item):
                    raise AccountingError("核销金额超过凭证同科目同辅助维度的结算金额", 409)
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
                if (
                    prior["request_fingerprint"] != fingerprint
                    or prior["allocation_id"] != allocation_id
                ):
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
                if row["original_date"] > as_of:
                    continue
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
        result = self._control_reconciliation_in_transaction(conn, actor, book_id, as_of, trial)
        from .period_bridge import continuity

        result["continuity"] = continuity(self, conn, actor, book_id)
        result["passed"] = result["passed"] and result["continuity"]["passed"]
        return result

    def _control_reconciliation_in_transaction(self, conn, actor, book_id, as_of=None, trial=None):
        from ..posting.service import PostingService

        book, ledger_id = self._book(conn, actor, book_id)
        year, month = map(int, book["period"].split("-"))
        period_end = f"{book['period']}-{monthrange(year, month)[1]:02d}"
        future_count = conn.execute(
            "SELECT COUNT(*) FROM subledger_items WHERE book_id=? AND original_date>?",
            (book["id"], period_end),
        ).fetchone()[0]
        if future_count:
            return {
                "status": "blocked",
                "reason": "历史未结事项存在晚于本账套期末的原始日期，须复核原始事实",
                "future_item_count": int(future_count),
                "rows": [],
                "passed": False,
            }
        as_of = as_of or period_end
        trial = trial or PostingService(self.db).trial_balance_in_transaction(
            conn, actor, book_id, as_of
        )
        state = self._control_state(conn, ledger_id, as_of or book["period"])
        configs = self._control_configs(conn, ledger_id, as_of=as_of or book["period"])
        if not state or state["status"] == "pending":
            return {
                "status": "blocked",
                "reason": "控制科目配置待确认，不能通过勾稽",
                "rows": [],
                "passed": False,
            }
        if state["status"] == "not_applicable":
            legacy_nonzero = [
                row
                for row in trial["rows"]
                if str(row.get("code", "")).startswith(("1122", "1123", "2202", "2203"))
                and any(
                    int(row.get(field, 0))
                    for field in ("opening_cents", "debit_cents", "credit_cents", "closing_cents")
                )
            ]
            if (
                self._has_any_control_config(conn, ledger_id)
                or conn.execute(
                    "SELECT 1 FROM subledger_items WHERE book_id=? LIMIT 1", (book["id"],)
                ).fetchone()
                or legacy_nonzero
            ):
                return {
                    "status": "blocked",
                    "reason": "已声明无需子账但存在控制科目余额、历史配置或事项",
                    "rows": [],
                    "passed": False,
                }
            return {
                "status": "not_applicable",
                "reason": state["reason"],
                "rows": [],
                "passed": True,
            }
        if not configs:
            return {
                "status": "blocked",
                "reason": "已启用子账但当前期间没有控制科目配置",
                "rows": [],
                "passed": False,
            }
        return control_rows(self, conn, book, configs, as_of)

    # ---------- bank evidence ----------

    def create_bank_account(self, actor, request):
        from .models import BankAccountCreate

        if not isinstance(request, BankAccountCreate):
            raise AccountingError("银行账户配置无效", 422)
        with self.db.transaction(write=True, actor=actor) as conn:
            book, ledger_id = self._book(conn, actor, request.book_id, write=True)
            mapped = conn.execute(
                "SELECT a.current_version,v.is_leaf,v.category,v.auxiliary_requirements FROM ledger_accounts a "
                "JOIN ledger_account_versions v ON v.account_id=a.id AND v.version=? "
                "WHERE a.id=? AND a.ledger_id=?",
                (request.ledger_account_version, request.ledger_account_id, ledger_id),
            ).fetchone()
            if not mapped or int(mapped["current_version"]) != request.ledger_account_version:
                raise AccountingError("银行账户映射的总账科目版本已变化或不属于当前账套", 409)
            if not mapped["is_leaf"] or mapped["category"] != "asset":
                raise AccountingError("银行账户必须映射到末级资产科目", 422)
            if any(
                auxiliary(row["aux_json"]) == auxiliary(request.aux)
                for row in conn.execute(
                    "SELECT aux_json FROM bank_accounts WHERE book_id=? AND ledger_account_id=? AND active IS TRUE",
                    (book["id"], request.ledger_account_id),
                )
            ):
                raise AccountingError("银行账户必须使用可区分的总账科目和辅助维度", 409)
            from .auxiliary import DIMENSIONS, _entity_valid

            if not set(json.loads(mapped["auxiliary_requirements"])).issubset(request.aux):
                raise AccountingError("缺少银行映射科目要求的必要辅助维度", 422)

            if set(request.aux) - DIMENSIONS or any(
                not _entity_valid(conn, dict(book), k, v) for k, v in request.aux.items()
            ):
                raise AccountingError("银行辅助维度必须为本企业有效实体", 422)
            account_id = uuid4().hex
            conn.execute(
                "INSERT INTO bank_accounts(id,firm_id,company_id,book_id,name,currency,active,"
                "ledger_account_id,ledger_account_version,aux_json,created_by,created_at) "
                "VALUES(?,?,?,?,?,?,TRUE,?,?,?,?,?)",
                (
                    account_id,
                    book["firm_id"],
                    book["company_id"],
                    book["id"],
                    request.name,
                    request.currency,
                    request.ledger_account_id,
                    request.ledger_account_version,
                    json.dumps(request.aux, ensure_ascii=False, sort_keys=True),
                    actor.user_id,
                    now(),
                ),
            )
            if not self.db.is_postgres:
                conn.execute(
                    "INSERT INTO close_dependency_revisions(scope_kind,scope_id,revision) "
                    "VALUES('book',?,1) ON CONFLICT(scope_kind,scope_id) DO UPDATE "
                    "SET revision=close_dependency_revisions.revision+1",
                    (book["id"],),
                )
            self._audit(
                conn,
                actor,
                book["id"],
                "bank.account.create",
                account_id,
                {
                    "name": request.name,
                    "currency": request.currency,
                },
            )
            return {"id": account_id, "name": request.name, "currency": request.currency}

    def list_bank_accounts(self, actor, book_id):
        with self.db.transaction(actor=actor) as conn:
            book, _ = self._book(conn, actor, book_id)
            return [
                dict(row)
                for row in conn.execute(
                    "SELECT * FROM bank_accounts WHERE book_id=? AND active IS TRUE ORDER BY name,id",
                    (book["id"],),
                )
            ]

    def declare_bank_coverage(self, actor, request):
        from .bank_evidence import declare_coverage

        return declare_coverage(self, actor, request)

    def review_bank_coverage(self, actor, coverage_id, request):
        from .bank_evidence import review_coverage

        return review_coverage(self, actor, coverage_id, request)

    def list_bank_coverages(self, actor, book_id):
        with self.db.transaction(actor=actor) as conn:
            book, _ = self._book(conn, actor, book_id)
            return [
                dict(row)
                for row in conn.execute(
                    "SELECT * FROM bank_statement_coverages WHERE book_id=? ORDER BY created_at DESC,id DESC",
                    (book["id"],),
                )
            ]

    def add_bank_line(self, actor, request: BankLineCreate):
        with self.db.transaction(write=True, actor=actor) as conn:
            book, _ = self._book(conn, actor, request.book_id, write=True)
            account_id = request.bank_account_id
            if account_id:
                if not conn.execute(
                    "SELECT 1 FROM bank_accounts WHERE id=? AND book_id=? AND active IS TRUE",
                    (account_id, book["id"]),
                ).fetchone():
                    raise AccountingError("银行账户不存在或不属于当前账套", 404)
            line_id = uuid4().hex
            conn.execute(
                "INSERT INTO bank_statement_lines(id,book_id,period,line_date,description,"
                "amount_cents,status,created_by,created_at,bank_account_id) "
                "VALUES(?,?,?,?,?,?,'unmatched',?,?,?)",
                (
                    line_id,
                    book["id"],
                    book["period"],
                    request.line_date,
                    request.description,
                    signed_money_to_cents(request.amount),
                    actor.user_id,
                    now(),
                    account_id,
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
            if line["bank_account_id"]:
                bank_account = conn.execute(
                    "SELECT * FROM bank_accounts WHERE id=? AND book_id=?",
                    (line["bank_account_id"], book["id"]),
                ).fetchone()
                if (
                    not bank_account
                    or voucher_line["account_id"] != bank_account["ledger_account_id"]
                    or auxiliary(voucher_line["aux_json"]) != auxiliary(bank_account["aux_json"])
                ):
                    raise AccountingError("银行流水与凭证账户映射不一致", 409)
            amount = int(voucher_line["debit_cents"]) - int(voucher_line["credit_cents"])
            if amount != int(line["amount_cents"]):
                raise AccountingError("银行流水金额与正式分录不一致", 409)
            if conn.execute(
                "SELECT 1 FROM bank_statement_lines WHERE matched_voucher_line_id=? AND id<>?",
                (voucher_line["id"], line_id),
            ).fetchone():
                raise AccountingError("正式分录已被另一银行流水使用", 409)
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
            if request.period != book["period"]:
                raise AccountingError("调整项期间不属于当前账套", 422)
            if signed_money_to_cents(request.amount) and not request.bank_account_id:
                raise AccountingError("非零银行调整项必须归属具体银行账户", 422)
            if (
                request.bank_account_id
                and not conn.execute(
                    "SELECT 1 FROM bank_accounts WHERE id=? AND book_id=? AND active IS TRUE",
                    (request.bank_account_id, book["id"]),
                ).fetchone()
            ):
                raise AccountingError("银行账户不存在或不属于当前账套", 404)
            adjustment_id = uuid4().hex
            conn.execute(
                "INSERT INTO bank_recon_adjustments(id,book_id,period,kind,amount_cents,"
                "explanation,status,created_by,created_at,bank_account_id) VALUES(?,?,?,?,?,?,'open',?,?,?)",
                (
                    adjustment_id,
                    book["id"],
                    request.period,
                    request.kind,
                    signed_money_to_cents(request.amount),
                    request.explanation,
                    actor.user_id,
                    now(),
                    request.bank_account_id,
                ),
            )
            return {"id": adjustment_id, "status": "open"}

    def resolve_bank_adjustment(self, actor, adjustment_id, request):
        with self.db.transaction(actor=actor) as conn:
            adjustment = conn.execute(
                "SELECT * FROM bank_recon_adjustments WHERE id=?", (adjustment_id,)
            ).fetchone()
            if not adjustment:
                raise AccountingError("银行调整项不存在", 404)
            book, _ = self._book(conn, actor, adjustment["book_id"], write=True)
            company_id = book["company_id"]
        with self.db.transaction(
            write=True, actor=actor, write_scope="company", company_id=company_id
        ) as conn:
            book, _ = self._book(conn, actor, book["id"], write=True)
            adjustment = conn.execute(
                "SELECT * FROM bank_recon_adjustments WHERE id=? AND book_id=?",
                (adjustment_id, book["id"]),
            ).fetchone()
            if adjustment["created_by"] == actor.user_id:
                raise AccountingError("银行调整必须由另一位复核人员解决", 403)
            prior = conn.execute(
                "SELECT * FROM bank_adjustment_resolutions WHERE adjustment_id=?", (adjustment_id,)
            ).fetchone()
            if prior:
                if (
                    prior["bank_line_id"] != request.bank_line_id
                    or prior["reason"] != request.reason
                    or prior["created_by"] != actor.user_id
                ):
                    raise AccountingError("调整项已经使用其他证据解决", 409)
                return dict(prior)
            line = conn.execute(
                "SELECT * FROM bank_statement_lines WHERE id=? AND book_id=?",
                (request.bank_line_id, book["id"]),
            ).fetchone()
            if (
                not line
                or not adjustment["bank_account_id"]
                or line["bank_account_id"] != adjustment["bank_account_id"]
                or line["status"] != "matched"
                or line["amount_cents"] != adjustment["amount_cents"]
                or not int(adjustment["amount_cents"])
            ):
                raise AccountingError("解决证据必须为同账户同金额的已核验正式流水", 409)
            if conn.execute(
                "SELECT 1 FROM bank_adjustment_resolutions WHERE bank_line_id=?", (line["id"],)
            ).fetchone():
                raise AccountingError("此流水已用于解决其他调整项", 409)
            resolution_id = uuid4().hex
            conn.execute(
                "INSERT INTO bank_adjustment_resolutions(id,book_id,firm_id,company_id,adjustment_id,bank_line_id,reason,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    resolution_id,
                    book["id"],
                    book["firm_id"],
                    company_id,
                    adjustment_id,
                    line["id"],
                    request.reason,
                    actor.user_id,
                    now(),
                ),
            )
            self._audit(
                conn,
                actor,
                book["id"],
                "bank.adjustment.resolve",
                resolution_id,
                {"adjustment_id": adjustment_id, "bank_line_id": line["id"]},
            )
            return dict(
                conn.execute(
                    "SELECT * FROM bank_adjustment_resolutions WHERE id=?", (resolution_id,)
                ).fetchone()
            )

    def bank_reconciliation(self, actor, book_id):
        with self.db.transaction(actor=actor) as conn:
            return self.bank_reconciliation_in_transaction(conn, actor, book_id)

    def bank_reconciliation_in_transaction(self, conn, actor, book_id, trial=None):
        from ..posting.service import PostingService

        book, _ = self._book(conn, actor, book_id)
        trial = trial or PostingService(self.db).trial_balance_in_transaction(conn, actor, book_id)
        result = bank_rows(self, conn, actor, book, trial)
        from .period_bridge import continuity

        result["continuity"] = continuity(self, conn, actor, book_id)
        result["identity_status"] = result["continuity"]["identity_status"]
        result["continuity_not_verified"] = result["continuity"]["continuity_not_verified"]
        result["passed"] = result["passed"] and result["continuity"]["passed"]
        return result

    def continuity(self, actor, book_id):
        from .period_bridge import continuity

        with self.db.transaction(actor=actor) as conn:
            return continuity(self, conn, actor, book_id)

    def create_bank_identity(self, actor, request):
        from .period_bridge import create_identity

        return create_identity(self, actor, request)

    def bind_bank_identity(self, actor, request):
        from .period_bridge import bind_identity

        return bind_identity(self, actor, request)

    def review_bank_binding(self, actor, binding_id, request):
        from .period_bridge import review_binding

        return review_binding(self, actor, binding_id, request)

    def create_period_bridge(self, actor, request):
        from .period_bridge import create_bridge

        return create_bridge(self, actor, request)

    def bank_identities(self, actor, book_id):
        with self.db.transaction(actor=actor) as conn:
            book, ledger = self._book(conn, actor, book_id)
            return {
                "identities": [
                    dict(x)
                    for x in conn.execute(
                        "SELECT * FROM ledger_bank_identities WHERE ledger_id=?", (ledger,)
                    )
                ],
                "bindings": [
                    dict(x)
                    for x in conn.execute(
                        "SELECT b.*,r.decision,r.created_by AS reviewed_by FROM bank_identity_bindings b LEFT JOIN bank_identity_reviews r ON r.binding_id=b.id WHERE b.book_id=?",
                        (book["id"],),
                    )
                ],
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
