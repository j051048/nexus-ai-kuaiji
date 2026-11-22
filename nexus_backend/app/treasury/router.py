"""资金付款台 HTTP 入口：指令的创建、列表、审批与状态推进。"""

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, StrictInt

from .adapters import AdapterError, parse_bank_csv, parse_receipt_json
from .imports import BankStatementImportService
from .receipt_matcher import Receipt, StatementLine, match as match_receipts
from .roles import PaymentRoleService
from .service import DEFAULT_DAILY_LIMIT_CENTS, DEFAULT_SINGLE_LIMIT_CENTS, PaymentService


class PaymentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    book_id: str
    period: str
    payee: str
    payee_account: str
    amount_cents: StrictInt
    idempotency_key: str
    single_limit_cents: StrictInt = DEFAULT_SINGLE_LIMIT_CENTS
    daily_limit_cents: StrictInt = DEFAULT_DAILY_LIMIT_CENTS


class PaymentApprove(BaseModel):
    model_config = ConfigDict(extra="forbid")

    voucher_reviewer: str = ""


class PaymentAdvance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target: str
    reason: str = ""


class BankLineImport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    format: str = Field(pattern="^(csv|receipts)$")
    csv_text: str | None = None
    receipts: list[dict] | None = None
    statement_lines: list[dict] | None = None
    source: str = ""


class PaymentRoleGrant(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: str = Field(pattern="^(cashier|treasury_approver)$")
    reason: str = Field(min_length=3, max_length=1000)


class PaymentRoleRevoke(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=3, max_length=1000)


class BankLineImportConfirm(BaseModel):
    model_config = ConfigDict(extra="forbid")

    book_id: str = Field(min_length=1, max_length=100)
    csv_text: str = Field(min_length=1, max_length=2_000_000)
    bank_account_id: str = Field(default="", max_length=100)


def _matcher_inputs(rows: list[dict]) -> list[Receipt] | list[StatementLine]:
    return rows


def build_treasury_router(actor_dependency):
    router = APIRouter(prefix="/api/treasury", tags=["B05资金付款"])

    def service(request: Request) -> PaymentService:
        return request.app.state.payments

    @router.post("/payment-orders")
    def create_order(
        body: PaymentCreate,
        current=Depends(actor_dependency),
        payments: PaymentService = Depends(service),
    ):
        return payments.create(
            current,
            body.book_id,
            payee=body.payee,
            payee_account=body.payee_account,
            amount_cents=body.amount_cents,
            idempotency_key=body.idempotency_key,
            period=body.period,
            single_limit_cents=body.single_limit_cents,
            daily_limit_cents=body.daily_limit_cents,
        )

    @router.get("/payment-orders")
    def list_orders(
        book_id: str = Query(min_length=1, max_length=100),
        status: str | None = Query(default=None, max_length=32),
        current=Depends(actor_dependency),
        payments: PaymentService = Depends(service),
    ):
        return payments.list_orders(current, book_id, status=status)

    @router.post("/payment-orders/{order_id}/submit")
    def submit_order(
        order_id: str,
        current=Depends(actor_dependency),
        payments: PaymentService = Depends(service),
    ):
        return payments.submit(current, order_id)

    @router.post("/payment-orders/{order_id}/approve")
    def approve_order(
        order_id: str,
        body: PaymentApprove,
        current=Depends(actor_dependency),
        payments: PaymentService = Depends(service),
    ):
        return payments.approve(current, order_id, voucher_reviewer=body.voucher_reviewer)

    @router.post("/bank-lines/import-preview")
    def import_preview(body: BankLineImport, current=Depends(actor_dependency)):
        try:
            if body.format == "csv":
                if not body.csv_text:
                    raise AdapterError("csv 格式需要 csv_text")
                lines = parse_bank_csv(body.csv_text, source=body.source or "counter_csv")
            else:
                if not body.receipts:
                    raise AdapterError("receipts 格式需要 receipts 数组")
                lines = parse_receipt_json(
                    {"receipts": body.receipts}, source=body.source or "etax_receipt"
                )
        except AdapterError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

        receipts = [
            Receipt(
                account=line.account,
                serial=line.serial,
                traded_on=line.date,
                amount_cents=line.amount_cents,
                counterparty=line.counterparty,
                receipt_id=f"{line.source}:{line.serial}",
            )
            for line in lines
            if body.format == "receipts"
        ]
        statements = [
            StatementLine(
                account=str(row.get("account", "")),
                serial=str(row.get("serial", "")),
                traded_on=str(row.get("date", "")),
                amount_cents=int(row.get("amount_cents", 0)),
                counterparty=str(row.get("counterparty", "")),
                line_id=str(row.get("line_id", "")),
            )
            for row in (body.statement_lines or [])
        ]
        result = match_receipts(receipts, statements) if receipts else None
        return {
            "lines": [line.as_dict() for line in lines],
            "matched": result.matched if result else [],
            "manual": result.manual if result else [],
            "unmatched_lines": result.unmatched_lines
            if result
            else [row.line_id for row in statements],
            "posted": False,
        }

    @router.post("/payment-orders/{order_id}/advance")
    def advance_order(
        order_id: str,
        body: PaymentAdvance,
        current=Depends(actor_dependency),
        payments: PaymentService = Depends(service),
    ):
        return payments.advance_external(current, order_id, body.target, reason=body.reason)

    @router.post("/bank-lines/import")
    def import_bank_lines(
        body: BankLineImportConfirm,
        current=Depends(actor_dependency),
        request: Request = None,
    ):
        return BankStatementImportService(request.app.state.service.db).confirm(
            current,
            body.book_id,
            csv_text=body.csv_text,
            bank_account_id=body.bank_account_id,
        )

    def roles(request: Request) -> PaymentRoleService:
        return PaymentRoleService(request.app.state.service.db)

    @router.get("/payment-roles")
    def list_payment_roles(
        book_id: str = Query(min_length=1, max_length=100),
        current=Depends(actor_dependency),
        roles_service: PaymentRoleService = Depends(roles),
    ):
        return roles_service.list(current, book_id)

    @router.put("/payment-roles/{user_id}")
    def grant_payment_role(
        user_id: str,
        body: PaymentRoleGrant,
        book_id: str = Query(min_length=1, max_length=100),
        current=Depends(actor_dependency),
        roles_service: PaymentRoleService = Depends(roles),
    ):
        return roles_service.grant(current, book_id, user_id, role=body.role, reason=body.reason)

    @router.post("/payment-roles/{role_id}/revoke")
    def revoke_payment_role(
        role_id: str,
        body: PaymentRoleRevoke,
        book_id: str = Query(min_length=1, max_length=100),
        current=Depends(actor_dependency),
        roles_service: PaymentRoleService = Depends(roles),
    ):
        return roles_service.revoke(current, book_id, role_id, reason=body.reason)

    @router.get("/payment-desk")
    def payment_desk(
        book_id: str = Query(min_length=1, max_length=100),
        current=Depends(actor_dependency),
        payments: PaymentService = Depends(service),
    ):
        return payments.desk(current, book_id)

    return router
