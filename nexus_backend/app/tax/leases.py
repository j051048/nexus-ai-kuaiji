"""Versioned operating-lease revenue plans and confirmation records.

A signed contract is not revenue, a receipt is not revenue, and an invoice is
not revenue. Only an explicit recognition for one service period creates a
balanced draft, and that draft still needs an independent accounting review.
"""

import hashlib
import json
from uuid import uuid4

from pydantic import Field, StrictStr, field_validator

from ..accounting.domain import AccountingError
from ..accounting.invariants import cents_to_money, money_to_cents, validate_period
from ..accounting.models import InputModel
from ..accounting.service import AccountingService, now


def _fingerprint(value):
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _month_index(period):
    year, month = map(int, period.split("-"))
    return year * 12 + month - 1


def _periods(start, end):
    if _month_index(end) < _month_index(start):
        raise AccountingError("租赁结束期间不能早于开始期间", 422)
    result, cursor = [], _month_index(start)
    while cursor <= _month_index(end):
        result.append(f"{cursor // 12:04d}-{cursor % 12 + 1:02d}")
        cursor += 1
    return result


def _positive(value, name):
    cents = money_to_cents(value)
    if cents <= 0:
        raise AccountingError(f"{name}必须大于零", 422)
    return cents


class LeasePolicy(InputModel):
    version: StrictStr = Field(min_length=1, max_length=80)
    receivable_account: StrictStr = Field(default="1122", min_length=1, max_length=40)
    advance_account: StrictStr = Field(default="2203", min_length=1, max_length=40)
    revenue_account: StrictStr = Field(min_length=1, max_length=40)
    refund_account: StrictStr = Field(default="6001", min_length=1, max_length=40)
    bank_account: StrictStr = Field(default="1002", min_length=1, max_length=40)


class LeaseCreate(InputModel):
    customer_name: StrictStr = Field(min_length=1, max_length=200)
    customer_external_id: StrictStr = Field(min_length=1, max_length=120)
    vehicle_external_id: StrictStr = Field(min_length=1, max_length=120)
    start_period: StrictStr
    end_period: StrictStr
    monthly: StrictStr

    @field_validator("start_period", "end_period")
    @classmethod
    def valid_period(cls, value):
        return validate_period(value)


class LeaseConfirmation(InputModel):
    kind: StrictStr
    period: StrictStr
    amount: StrictStr
    reference: StrictStr = Field(min_length=1, max_length=200)

    @field_validator("period")
    @classmethod
    def valid_period(cls, value):
        return validate_period(value)

    @field_validator("kind")
    @classmethod
    def valid_kind(cls, value):
        if value not in {"business", "receipt", "invoice"}:
            raise ValueError("确认类型必须是 business、receipt 或 invoice")
        return value


class LeaseInvoiceRed(InputModel):
    original_reference: StrictStr = Field(min_length=1, max_length=200)
    red_reference: StrictStr = Field(min_length=1, max_length=200)
    period: StrictStr
    amount: StrictStr
    reason: StrictStr = Field(min_length=3, max_length=1000)

    @field_validator("period")
    @classmethod
    def valid_period(cls, value):
        return validate_period(value)


class LeaseRecognition(InputModel):
    period: StrictStr
    policy: LeasePolicy
    reason: StrictStr = Field(min_length=3, max_length=1000)

    @field_validator("period")
    @classmethod
    def valid_period(cls, value):
        return validate_period(value)


class LeaseRefund(InputModel):
    period: StrictStr
    amount: StrictStr
    reference: StrictStr = Field(min_length=1, max_length=200)
    policy: LeasePolicy
    reason: StrictStr = Field(min_length=3, max_length=1000)

    @field_validator("period")
    @classmethod
    def valid_period(cls, value):
        return validate_period(value)


class LeaseApprove(InputModel):
    expected_version: int = Field(strict=True, ge=1)


class LeaseAmend(InputModel):
    expected_version: int = Field(strict=True, ge=1)
    end_period: StrictStr
    monthly: StrictStr
    reason: StrictStr = Field(min_length=3, max_length=1000)

    @field_validator("end_period")
    @classmethod
    def valid_period(cls, value):
        return validate_period(value)


class LeaseService:
    def __init__(self, db):
        self.db = db
        self.accounting = AccountingService(db)

    def _book(self, conn, actor, book_id, write=False):
        return self.accounting.access.book(conn, actor, book_id, "review" if write else "read")

    @staticmethod
    def _contract_fingerprint(values):
        return _fingerprint(values)

    def _plan(self, conn, contract, *, replace=False):
        if replace:
            conn.execute(
                "DELETE FROM lease_plan_rows WHERE contract_id=? AND status='planned'",
                (contract["id"],),
            )
        existing = {
            row["period"]
            for row in conn.execute(
                "SELECT period FROM lease_plan_rows WHERE contract_id=?", (contract["id"],)
            ).fetchall()
        }
        stamp = now()
        for period in _periods(contract["start_period"], contract["end_period"]):
            if period in existing:
                continue
            conn.execute(
                "INSERT INTO lease_plan_rows(id,contract_id,period,amount_cents,kind,status,created_at) "
                "VALUES(?,?,?,?,?,'planned',?)",
                (uuid4().hex, contract["id"], period, int(contract["monthly_cents"]), "revenue", stamp),
            )

    def create(self, actor, book_id, request: LeaseCreate):
        monthly = _positive(request.monthly, "月租金")
        with self.db.transaction(write=True, actor=actor) as conn:
            book = self._book(conn, actor, book_id, write=True)
            self.accounting._open(book)
            overlap = conn.execute(
                "SELECT 1 FROM lease_contracts WHERE book_id=? AND vehicle_external_id=? "
                "AND status IN ('active','draft') AND start_period<=? AND end_period>=?",
                (book_id, request.vehicle_external_id.strip(), request.end_period, request.start_period),
            ).fetchone()
            if overlap:
                raise AccountingError("同一车辆在该区间已有生效租赁合同", 409)
            contract_id = uuid4().hex
            values = {
                "customer_name": request.customer_name.strip(),
                "customer_external_id": request.customer_external_id.strip(),
                "vehicle_external_id": request.vehicle_external_id.strip(),
                "start_period": request.start_period,
                "end_period": request.end_period,
                "monthly_cents": monthly,
                "version": 1,
            }
            conn.execute(
                "INSERT INTO lease_contracts(id,firm_id,company_id,book_id,customer_name,"
                "customer_external_id,vehicle_external_id,start_period,end_period,monthly_cents,"
                "status,version,approved_version,source_fingerprint,created_by,created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,'active',1,1,?,?,?)",
                (
                    contract_id, actor.firm_id, book["company_id"], book_id,
                    values["customer_name"], values["customer_external_id"],
                    values["vehicle_external_id"], request.start_period, request.end_period,
                    monthly, self._contract_fingerprint(values), actor.user_id, now(),
                ),
            )
            self._plan(conn, {"id": contract_id, **values, "monthly_cents": monthly})
            return self._view(conn, conn.execute("SELECT * FROM lease_contracts WHERE id=?", (contract_id,)).fetchone())

    def list(self, actor, book_id):
        with self.db.transaction(actor=actor) as conn:
            self._book(conn, actor, book_id)
            rows = conn.execute(
                "SELECT * FROM lease_contracts WHERE book_id=? ORDER BY created_at DESC", (book_id,)
            ).fetchall()
            return {"items": [self._view(conn, row) for row in rows]}

    def get(self, actor, book_id, contract_id):
        with self.db.transaction(actor=actor) as conn:
            self._book(conn, actor, book_id)
            row = conn.execute(
                "SELECT * FROM lease_contracts WHERE id=? AND book_id=?", (contract_id, book_id)
            ).fetchone()
            if not row:
                raise AccountingError("租赁合同不存在或无权访问", 404)
            return self._view(conn, row)

    def confirm(self, actor, book_id, contract_id, request: LeaseConfirmation):
        amount = _positive(request.amount, "确认金额")
        with self.db.transaction(write=True, actor=actor) as conn:
            self._book(conn, actor, book_id, write=True)
            contract = conn.execute(
                "SELECT * FROM lease_contracts WHERE id=? AND book_id=?", (contract_id, book_id)
            ).fetchone()
            if not contract:
                raise AccountingError("租赁合同不存在或无权访问", 404)
            if contract["status"] != "active":
                raise AccountingError("只有生效合同可以登记确认", 409)
            if _month_index(request.period) < _month_index(contract["start_period"]) or _month_index(request.period) > _month_index(contract["end_period"]):
                raise AccountingError("确认期间不在合同服务区间内", 422)
            if request.kind == "business":
                planned = conn.execute(
                    "SELECT amount_cents FROM lease_plan_rows WHERE contract_id=? AND period=? AND kind='revenue'",
                    (contract_id, request.period),
                ).fetchone()
                if not planned or amount != int(planned["amount_cents"]):
                    raise AccountingError("业务确认金额必须等于该期间计划金额", 409)
            try:
                conn.execute(
                    "INSERT INTO lease_confirmations(id,contract_id,kind,period,amount_cents,reference,confirmed_by,confirmed_at) "
                    "VALUES(?,?,?,?,?,?,?,?)",
                    (uuid4().hex, contract_id, request.kind, request.period, amount,
                     request.reference.strip(), actor.user_id, now()),
                )
            except Exception as exc:
                if "UNIQUE" in str(exc) or "duplicate" in str(exc).lower():
                    raise AccountingError("同一确认类型、期间与凭证号已登记", 409) from None
                raise
            return self._view(conn, contract)


    def red_invoice(self, actor, book_id, contract_id, request: LeaseInvoiceRed):
        amount = _positive(request.amount, "红冲金额")
        with self.db.transaction(write=True, actor=actor) as conn:
            self._book(conn, actor, book_id, write=True)
            contract = conn.execute(
                "SELECT * FROM lease_contracts WHERE id=? AND book_id=?", (contract_id, book_id)
            ).fetchone()
            if not contract:
                raise AccountingError("租赁合同不存在或无权访问", 404)
            if contract["status"] != "active":
                raise AccountingError("只有生效合同可以登记发票红冲", 409)
            invoice = conn.execute(
                "SELECT * FROM lease_confirmations WHERE contract_id=? AND kind='invoice' AND reference=?",
                (contract_id, request.original_reference.strip()),
            ).fetchone()
            if not invoice:
                raise AccountingError("原发票确认不存在", 404)
            if int(invoice["amount_cents"]) != amount:
                raise AccountingError("红冲金额必须等于原发票金额", 409)
            if _month_index(request.period) < _month_index(invoice["period"]):
                raise AccountingError("红冲期间不能早于原发票期间", 422)
            if conn.execute(
                "SELECT 1 FROM lease_invoice_reds WHERE contract_id=? AND original_reference=?",
                (contract_id, request.original_reference.strip()),
            ).fetchone():
                raise AccountingError("该发票已红冲", 409)
            try:
                conn.execute(
                    "INSERT INTO lease_invoice_reds(id,contract_id,original_reference,red_reference,period,amount_cents,reason,created_by,created_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?)",
                    (uuid4().hex, contract_id, request.original_reference.strip(), request.red_reference.strip(),
                     request.period, amount, request.reason.strip(), actor.user_id, now()),
                )
            except Exception as exc:
                if "UNIQUE" in str(exc) or "duplicate" in str(exc).lower():
                    raise AccountingError("红字发票号已登记", 409) from None
                raise
            return self._view(conn, contract)

    def recognize(self, actor, book_id, contract_id, request: LeaseRecognition):
        with self.db.transaction(write=True, actor=actor) as conn:
            book = self._book(conn, actor, book_id, write=True)
            self.accounting._open(book)
            contract = conn.execute(
                "SELECT * FROM lease_contracts WHERE id=? AND book_id=?", (contract_id, book_id)
            ).fetchone()
            if not contract or contract["status"] != "active":
                raise AccountingError("租赁合同不存在或未生效", 404)
            if int(contract["approved_version"]) != int(contract["version"]):
                raise AccountingError("租赁合同已改版，请先确认当前版本", 409)
            plan = conn.execute(
                "SELECT * FROM lease_plan_rows WHERE contract_id=? AND period=? AND kind='revenue'",
                (contract_id, request.period),
            ).fetchone()
            if not plan:
                raise AccountingError("该期间没有租赁收入计划", 404)
            if plan["status"] == "recognized":
                return self._view(conn, contract)
            if plan["status"] != "planned":
                raise AccountingError("租赁计划已取消，不能确认收入", 409)
            business = conn.execute(
                "SELECT 1 FROM lease_confirmations WHERE contract_id=? AND period=? AND kind='business'",
                (contract_id, request.period),
            ).fetchone()
            if not business:
                raise AccountingError("缺少业务确认，不能确认租赁收入", 409)
            for code in (request.policy.revenue_account, request.policy.receivable_account, request.policy.advance_account):
                if not conn.execute(
                    "SELECT 1 FROM accounts WHERE book_id=? AND code=? AND active=1", (book_id, code)
                ).fetchone():
                    raise AccountingError(f"科目 {code} 不存在或已停用", 422)
            received = int(
                conn.execute(
                    "SELECT COALESCE(SUM(amount_cents),0) FROM lease_confirmations "
                    "WHERE contract_id=? AND period<=? AND kind='receipt'",
                    (contract_id, request.period),
                ).fetchone()[0]
            )
            refunded = int(
                conn.execute(
                    "SELECT COALESCE(SUM(amount_cents),0) FROM lease_plan_rows "
                    "WHERE contract_id=? AND kind='refund' AND period<=?",
                    (contract_id, request.period),
                ).fetchone()[0]
            )
            used_advance = int(
                conn.execute(
                    "SELECT COALESCE(SUM(advance_cents),0) FROM lease_plan_rows "
                    "WHERE contract_id=? AND kind='revenue' AND status='recognized'",
                    (contract_id,),
                ).fetchone()[0]
            )
            available = max(0, received - refunded - used_advance)
            amount = int(plan["amount_cents"])
            source_id, draft_id = uuid4().hex, uuid4().hex
            fp = _fingerprint({"contract": contract_id, "period": request.period, "amount": amount, "policy": request.policy.model_dump()})
            conn.execute(
                "INSERT INTO sources(id,book_id,external_id,source_name,date,description,counterparty,"
                "amount_cents,direction,evidence_ref,fingerprint,source_kind) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,'business')",
                (source_id, book_id, f"lease:{contract_id}:{request.period}", "经营租赁收入确认",
                 f"{request.period}-01", f"租赁收入 {request.period}", contract["customer_name"],
                 amount, "income", f"lease-contract:{contract_id}:{request.period}", fp),
            )
            conn.execute(
                "INSERT INTO drafts(id,book_id,source_id,description,explanation) VALUES(?,?,?,?,?)",
                (draft_id, book_id, source_id, f"租赁收入 {contract['customer_name']} {request.period}",
                 f"政策 {request.policy.version}；业务确认后按期间确认收入；合同版本 {contract['version']}；须独立复核。"),
            )
            lines, ordinal = [], 0
            from_advance = min(available, amount)
            if from_advance:
                lines.append((request.policy.advance_account, from_advance, 0))
            remaining = amount - from_advance
            if remaining:
                lines.append((request.policy.receivable_account, remaining, 0))
            lines.append((request.policy.revenue_account, 0, amount))
            for code, debit, credit in lines:
                conn.execute("INSERT INTO draft_lines VALUES(?,?,?,?,?,?)", (draft_id, book_id, ordinal, code, debit, credit))
                ordinal += 1
            conn.execute(
                "UPDATE lease_plan_rows SET status='recognized',advance_cents=?,draft_id=? WHERE id=?",
                (from_advance, draft_id, plan["id"]),
            )
            conn.execute(
                "INSERT INTO lease_events(id,contract_id,kind,period,amount_cents,reason,created_by,created_at) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (uuid4().hex, contract_id, "recognize", request.period, amount, request.reason.strip(), actor.user_id, now()),
            )
            return self._view(conn, conn.execute("SELECT * FROM lease_contracts WHERE id=?", (contract_id,)).fetchone())

    def refund(self, actor, book_id, contract_id, request: LeaseRefund):
        amount = _positive(request.amount, "退款金额")
        with self.db.transaction(write=True, actor=actor) as conn:
            book = self._book(conn, actor, book_id, write=True)
            self.accounting._open(book)
            contract = conn.execute(
                "SELECT * FROM lease_contracts WHERE id=? AND book_id=?", (contract_id, book_id)
            ).fetchone()
            if not contract:
                raise AccountingError("租赁合同不存在或无权访问", 404)
            received = int(
                conn.execute(
                    "SELECT COALESCE(SUM(amount_cents),0) FROM lease_confirmations "
                    "WHERE contract_id=? AND kind='receipt'",
                    (contract_id,),
                ).fetchone()[0]
            )
            if amount > received:
                raise AccountingError("退款金额不能超过已收款金额", 409)
            try:
                conn.execute(
                    "INSERT INTO lease_plan_rows(id,contract_id,period,amount_cents,kind,status,created_at) "
                    "VALUES(?,?,?,?,?,'recognized',?)",
                    (uuid4().hex, contract_id, request.period, amount, "refund", now()),
                )
            except Exception as exc:
                if "UNIQUE" in str(exc) or "duplicate" in str(exc).lower():
                    raise AccountingError("该期间退款已登记，不能重复", 409) from None
                raise
            source_id, draft_id = uuid4().hex, uuid4().hex
            fp = _fingerprint({"refund": contract_id, "period": request.period, "amount": amount, "reference": request.reference})
            conn.execute(
                "INSERT INTO sources(id,book_id,external_id,source_name,date,description,counterparty,"
                "amount_cents,direction,evidence_ref,fingerprint,source_kind) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,'business')",
                (source_id, book_id, f"lease-refund:{contract_id}:{request.reference}", "租赁退款",
                 f"{request.period}-01", f"租赁退款 {contract['customer_name']}", contract["customer_name"],
                 amount, "expense", f"lease-refund:{request.reference}", fp),
            )
            conn.execute(
                "INSERT INTO drafts(id,book_id,source_id,description,explanation) VALUES(?,?,?,?,?)",
                (draft_id, book_id, source_id, f"租赁退款 {contract['customer_name']} {request.period}",
                 f"退款依据 {request.reference}；政策 {request.policy.version}；须独立复核。"),
            )
            conn.execute("INSERT INTO draft_lines VALUES(?,?,?,?,?,?)", (draft_id, book_id, 0, request.policy.refund_account, amount, 0))
            conn.execute("INSERT INTO draft_lines VALUES(?,?,?,?,?,?)", (draft_id, book_id, 1, request.policy.bank_account, 0, amount))
            conn.execute(
                "INSERT INTO lease_events(id,contract_id,kind,period,amount_cents,reason,created_by,created_at) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (uuid4().hex, contract_id, "refund", request.period, amount, request.reason.strip(), actor.user_id, now()),
            )
            conn.execute("UPDATE lease_plan_rows SET draft_id=? WHERE contract_id=? AND period=? AND kind='refund'", (draft_id, contract_id, request.period))
            return self._view(conn, conn.execute("SELECT * FROM lease_contracts WHERE id=?", (contract_id,)).fetchone())

    def amend(self, actor, book_id, contract_id, request: LeaseAmend):
        monthly = _positive(request.monthly, "月租金")
        with self.db.transaction(write=True, actor=actor) as conn:
            book = self._book(conn, actor, book_id, write=True)
            self.accounting._open(book)
            contract = conn.execute(
                "SELECT * FROM lease_contracts WHERE id=? AND book_id=?", (contract_id, book_id)
            ).fetchone()
            if not contract or contract["status"] != "active":
                raise AccountingError("租赁合同不存在或未生效", 404)
            if int(contract["version"]) != request.expected_version:
                raise AccountingError("租赁合同版本已变化", 409)
            if _month_index(request.end_period) < _month_index(contract["start_period"]):
                raise AccountingError("结束期间不能早于开始期间", 422)
            values = {**dict(contract), "end_period": request.end_period, "monthly_cents": monthly, "version": int(contract["version"]) + 1}
            conn.execute(
                "UPDATE lease_contracts SET end_period=?,monthly_cents=?,version=version+1,"
                "source_fingerprint=? WHERE id=? AND version=?",
                (request.end_period, monthly, self._contract_fingerprint(values), contract_id, request.expected_version),
            )
            self._plan(conn, conn.execute("SELECT * FROM lease_contracts WHERE id=?", (contract_id,)).fetchone(), replace=True)
            conn.execute(
                "INSERT INTO lease_events(id,contract_id,kind,period,amount_cents,reason,created_by,created_at) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (uuid4().hex, contract_id, "amend", request.end_period, 0, request.reason.strip(), actor.user_id, now()),
            )
            return self._view(conn, conn.execute("SELECT * FROM lease_contracts WHERE id=?", (contract_id,)).fetchone())

    def approve(self, actor, book_id, contract_id, expected_version):
        with self.db.transaction(write=True, actor=actor) as conn:
            book = self._book(conn, actor, book_id, write=True)
            self.accounting._open(book)
            contract = conn.execute(
                "SELECT * FROM lease_contracts WHERE id=? AND book_id=?", (contract_id, book_id)
            ).fetchone()
            if not contract or contract["status"] != "active":
                raise AccountingError("租赁合同不存在或未生效", 404)
            if int(contract["version"]) != expected_version:
                raise AccountingError("租赁合同版本已变化", 409)
            conn.execute(
                "UPDATE lease_contracts SET approved_version=? WHERE id=? AND version=?",
                (expected_version, contract_id, expected_version),
            )
            return self._view(conn, conn.execute("SELECT * FROM lease_contracts WHERE id=?", (contract_id,)).fetchone())

    def terminate(self, actor, book_id, contract_id, reason):
        if not str(reason or "").strip():
            raise AccountingError("终止合同必须填写理由", 422)
        with self.db.transaction(write=True, actor=actor) as conn:
            self._book(conn, actor, book_id, write=True)
            contract = conn.execute(
                "SELECT * FROM lease_contracts WHERE id=? AND book_id=?", (contract_id, book_id)
            ).fetchone()
            if not contract:
                raise AccountingError("租赁合同不存在或无权访问", 404)
            conn.execute("UPDATE lease_contracts SET status='terminated' WHERE id=?", (contract_id,))
            conn.execute("UPDATE lease_plan_rows SET status='cancelled' WHERE contract_id=? AND status='planned'", (contract_id,))
            conn.execute(
                "INSERT INTO lease_events(id,contract_id,kind,period,amount_cents,reason,created_by,created_at) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (uuid4().hex, contract_id, "terminate", contract["end_period"], 0, str(reason).strip(), actor.user_id, now()),
            )
            return self._view(conn, conn.execute("SELECT * FROM lease_contracts WHERE id=?", (contract_id,)).fetchone())

    def _view(self, conn, contract):
        plan = conn.execute(
            "SELECT * FROM lease_plan_rows WHERE contract_id=? ORDER BY period,kind", (contract["id"],)
        ).fetchall()
        confirmations = conn.execute(
            "SELECT * FROM lease_confirmations WHERE contract_id=? ORDER BY period,kind,confirmed_at", (contract["id"],)
        ).fetchall()
        events = conn.execute(
            "SELECT * FROM lease_events WHERE contract_id=? ORDER BY created_at", (contract["id"],)
        ).fetchall()
        invoice_reds = conn.execute(
            "SELECT * FROM lease_invoice_reds WHERE contract_id=? ORDER BY created_at", (contract["id"],)
        ).fetchall()
        received = sum(int(row["amount_cents"]) for row in confirmations if row["kind"] == "receipt")
        recognized = sum(int(row["amount_cents"]) for row in plan if row["kind"] == "revenue" and row["status"] == "recognized")
        refunded = sum(int(row["amount_cents"]) for row in plan if row["kind"] == "refund")
        return {
            "id": contract["id"], "book_id": contract["book_id"],
            "customer_name": contract["customer_name"], "customer_external_id": contract["customer_external_id"],
            "vehicle_external_id": contract["vehicle_external_id"],
            "start_period": contract["start_period"], "end_period": contract["end_period"],
            "monthly_cents": int(contract["monthly_cents"]), "status": contract["status"],
            "version": int(contract["version"]), "approved_version": int(contract["approved_version"]),
            "stale_approval": int(contract["approved_version"]) != int(contract["version"]),
            "source_fingerprint": contract["source_fingerprint"],
            "received_cents": received, "recognized_cents": recognized, "refunded_cents": refunded,
            "plan": [dict(row) for row in plan],
            "confirmations": [dict(row) for row in confirmations],
            "events": [dict(row) for row in events],
            "invoice_reds": [dict(row) for row in invoice_reds],
        }
