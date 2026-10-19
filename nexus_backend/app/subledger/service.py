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

    def _control_state(self, conn, ledger_id, period):
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
                row for row in trial["rows"]
                if str(row.get("code", "")).startswith(("1122", "1123", "2202", "2203"))
                and any(int(row.get(field, 0)) for field in (
                    "opening_cents", "debit_cents", "credit_cents", "closing_cents"
                ))
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
            self._audit(conn, actor, book["id"], "subledger.control.not_applicable", ledger_id, {
                "reason": request.reason, "effective_from": request.effective_from,
                "effective_to": request.effective_to,
            })
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
                "SELECT a.current_version,v.is_leaf FROM ledger_accounts a "
                "JOIN ledger_account_versions v ON v.account_id=a.id AND v.version=? "
                "WHERE a.id=? AND a.ledger_id=?",
                (request.control_account_version, request.control_account_id, ledger_id),
            ).fetchone()
            if not account or int(account["current_version"]) != request.control_account_version:
                raise AccountingError("控制科目版本已变化或不属于当前账套", 409)
            if not account["is_leaf"]:
                raise AccountingError("控制科目必须是末级科目", 422)
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
            self._audit(conn, actor, book["id"], "subledger.control.configure", config_id, {
                "kind": request.kind, "account_id": request.control_account_id,
                "effective_from": request.effective_from, "effective_to": request.effective_to,
            })
            return dict(conn.execute(
                "SELECT * FROM subledger_control_accounts WHERE id=?", (config_id,)
            ).fetchone())

    def list_control_accounts(self, actor, book_id, as_of=None):
        with self.db.transaction(actor=actor) as conn:
            book, ledger_id = self._book(conn, actor, book_id)
            period = as_of or book["period"]
            return [dict(row) for row in self._control_configs(conn, ledger_id, as_of=period)]

    def create_item(self, actor, request: OpenItemCreate):
        with self.db.transaction(write=True, actor=actor) as conn:
            book, ledger_id = self._book(conn, actor, request.book_id, write=True)
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
            required_aux = set(json.loads(matched["required_aux_json"] or "[]"))
            if not required_aux.issubset(set(request.aux)):
                raise AccountingError("缺少控制科目要求的必要辅助维度", 422)
            if any(not isinstance(value, str) or not value.strip() for value in request.aux.values()):
                raise AccountingError("辅助维度值不能为空", 422)
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
                    json.dumps(request.aux, ensure_ascii=False, sort_keys=True),
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
        book, ledger_id = self._book(conn, actor, book_id)
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
                row for row in trial["rows"]
                if str(row.get("code", "")).startswith(("1122", "1123", "2202", "2203"))
                and any(int(row.get(field, 0)) for field in (
                    "opening_cents", "debit_cents", "credit_cents", "closing_cents"
                ))
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
            return {"status": "not_applicable", "reason": state["reason"], "rows": [], "passed": True}
        if not configs:
            return {
                "status": "blocked",
                "reason": "已启用子账但当前期间没有控制科目配置",
                "rows": [],
                "passed": False,
            }
        for config in configs:
            groups.setdefault(config["kind"], {}).setdefault(config["control_account_id"], 0)
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
        balances = {row["account_id"]: row for row in trial["rows"]}
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
        return {
            "status": "configured",
            "reason": "",
            "rows": result,
            "passed": all(row["passed"] for row in result),
        }

    # ---------- bank evidence ----------

    def create_bank_account(self, actor, request):
        from .models import BankAccountCreate

        if not isinstance(request, BankAccountCreate):
            raise AccountingError("银行账户配置无效", 422)
        with self.db.transaction(write=True, actor=actor) as conn:
            book, ledger_id = self._book(conn, actor, request.book_id, write=True)
            mapped = conn.execute(
                "SELECT a.current_version,v.is_leaf,v.category FROM ledger_accounts a "
                "JOIN ledger_account_versions v ON v.account_id=a.id AND v.version=? "
                "WHERE a.id=? AND a.ledger_id=?",
                (request.ledger_account_version, request.ledger_account_id, ledger_id),
            ).fetchone()
            if not mapped or int(mapped["current_version"]) != request.ledger_account_version:
                raise AccountingError("银行账户映射的总账科目版本已变化或不属于当前账套", 409)
            if not mapped["is_leaf"] or mapped["category"] != "asset":
                raise AccountingError("银行账户必须映射到末级资产科目", 422)
            account_id = uuid4().hex
            conn.execute(
                "INSERT INTO bank_accounts(id,firm_id,company_id,book_id,name,currency,active,"
                "ledger_account_id,ledger_account_version,aux_json,created_by,created_at) "
                "VALUES(?,?,?,?,?,?,1,?,?,?,?,?)",
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
            self._audit(conn, actor, book["id"], "bank.account.create", account_id, {
                "name": request.name, "currency": request.currency,
            })
            return {"id": account_id, "name": request.name, "currency": request.currency}

    def list_bank_accounts(self, actor, book_id):
        with self.db.transaction(actor=actor) as conn:
            book, _ = self._book(conn, actor, book_id)
            return [dict(row) for row in conn.execute(
                "SELECT * FROM bank_accounts WHERE book_id=? AND active IS TRUE ORDER BY name,id",
                (book["id"],),
            )]

    def declare_bank_coverage(self, actor, request):
        from .models import BankCoverageCreate

        if not isinstance(request, BankCoverageCreate):
            raise AccountingError("银行覆盖声明无效", 422)
        with self.db.transaction(write=True, actor=actor) as conn:
            book, _ = self._book(conn, actor, request.book_id, write=True)
            account = conn.execute(
                "SELECT * FROM bank_accounts WHERE id=? AND book_id=? AND active IS TRUE",
                (request.account_id, book["id"]),
            ).fetchone()
            if not account:
                raise AccountingError("银行账户不存在或不属于当前账套", 404)
            if request.period != book["period"]:
                raise AccountingError("银行覆盖期间与当前账套期间不一致", 422)
            coverage_id = uuid4().hex
            conn.execute(
                "INSERT INTO bank_statement_coverages(id,account_id,book_id,period,opening_cents,"
                "closing_cents,source_ref,verified_by,verified_at,created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    coverage_id,
                    account["id"],
                    book["id"],
                    request.period,
                    money_to_cents(request.opening),
                    money_to_cents(request.closing),
                    request.source_ref,
                    actor.user_id,
                    now(),
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
            self._audit(conn, actor, book["id"], "bank.coverage.declare", coverage_id, {
                "account_id": request.account_id, "period": request.period,
            })
            return {"id": coverage_id, "account_id": account["id"], "period": request.period}

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
                if not bank_account or voucher_line["account_id"] != bank_account["ledger_account_id"]:
                    raise AccountingError("银行流水与凭证账户映射不一致", 409)
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
        accounts = conn.execute(
            "SELECT * FROM bank_accounts WHERE book_id=? AND active IS TRUE ORDER BY name,id",
            (book["id"],),
        ).fetchall()
        if accounts:
            trial = trial or PostingService(self.db).trial_balance_in_transaction(
                conn, actor, book_id
            )
            cash_rows = [
                row for row in trial["rows"]
                if row["is_leaf"] and row["code"].startswith(("1001", "1002"))
            ]
            cash = sum(int(row["closing_cents"]) for row in cash_rows)
            adjustments = conn.execute(
                "SELECT * FROM bank_recon_adjustments WHERE book_id=? AND status='open'",
                (book["id"],),
            ).fetchall()
            unassigned = conn.execute(
                "SELECT COUNT(*) FROM bank_statement_lines WHERE book_id=? AND bank_account_id IS NULL",
                (book["id"],),
            ).fetchone()[0]
            balances = {row["account_id"]: int(row["closing_cents"]) for row in trial["rows"]}
            rows = []
            coverage_closing = 0
            account_book_total = 0
            for account in accounts:
                coverage = conn.execute(
                    "SELECT * FROM bank_statement_coverages WHERE account_id=? AND period=?",
                    (account["id"], book["period"]),
                ).fetchone()
                lines = conn.execute(
                    "SELECT COUNT(*),COALESCE(SUM(amount_cents),0),"
                    "COALESCE(SUM(CASE WHEN status='unmatched' THEN 1 ELSE 0 END),0) "
                    "FROM bank_statement_lines WHERE book_id=? AND bank_account_id=?",
                    (book["id"], account["id"]),
                ).fetchone()
                opening = int(coverage["opening_cents"]) if coverage else 0
                closing = int(coverage["closing_cents"]) if coverage else 0
                net = int(lines[1])
                accounted = balances.get(account["ledger_account_id"])
                coverage_closing += closing if coverage else 0
                account_book_total += int(accounted or 0)
                passed = (
                    coverage is not None
                    and accounted is not None
                    and opening + net == closing
                    and int(accounted) == closing
                    and int(lines[2]) == 0
                )
                rows.append(
                    {
                        "account_id": account["id"],
                        "name": account["name"],
                        "ledger_account_id": account["ledger_account_id"],
                        "coverage": coverage is not None,
                        "opening_cents": opening,
                        "net_cents": net,
                        "closing_cents": closing,
                        "book_cents": accounted,
                        "line_count": int(lines[0]),
                        "unmatched_count": int(lines[2]),
                        "difference_cents": opening + net - closing,
                        "passed": passed,
                    }
                )
            coverage_ok = all(row["coverage"] for row in rows)
            passed = (
                coverage_ok
                and unassigned == 0
                and not adjustments
                and account_book_total == coverage_closing
                and all(row["passed"] for row in rows)
            )
            return {
                "multi_account": True,
                "rows": rows,
                "coverage_ok": coverage_ok,
                "unassigned_line_count": int(unassigned),
                "book_cash_cents": cash,
                "coverage_closing_cents": coverage_closing,
                "unexplained_cents": sum(int(row["amount_cents"]) for row in adjustments),
                "difference_cents": cash - coverage_closing,
                "passed": passed,
            }
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
