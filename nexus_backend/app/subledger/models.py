"""Subledger contracts; allocations change subledger facts, not posted vouchers."""

from datetime import date
import re
from typing import Literal

from pydantic import Field, StrictStr, field_validator

from ..accounting.models import InputModel, money_to_cents

ItemKind = Literal["receivable", "payable", "advance_received", "advance_paid"]


class ControlAccountCreate(InputModel):
    book_id: StrictStr = Field(min_length=1, max_length=100)
    kind: ItemKind
    control_account_id: StrictStr = Field(min_length=1, max_length=100)
    control_account_version: int = Field(strict=True, ge=1)
    required_aux: list[str] = Field(default_factory=list, max_length=20)
    effective_from: StrictStr
    effective_to: StrictStr | None = None
    reason: StrictStr = Field(min_length=3, max_length=1000)

    @field_validator("effective_from", "effective_to")
    @classmethod
    def valid_period(cls, value):
        if value is not None and not __import__("re").fullmatch(r"\d{4}-(0[1-9]|1[0-2])", value):
            raise ValueError("生效期间必须为 YYYY-MM")
        return value


class OpenItemCreate(InputModel):
    book_id: StrictStr = Field(min_length=1, max_length=100)
    kind: ItemKind
    counterparty_id: StrictStr = Field(min_length=1, max_length=100)
    control_account_id: StrictStr = Field(min_length=1, max_length=100)
    control_account_version: int = Field(strict=True, ge=1)
    original_date: StrictStr
    due_date: StrictStr | None = None
    amount: StrictStr
    source_kind: Literal["opening", "voucher", "manual"] = "manual"
    source_id: StrictStr | None = Field(default=None, max_length=100)
    source_version: int | None = Field(default=None, strict=True, ge=1)
    aux: dict[str, str] = Field(default_factory=dict)
    reason: StrictStr = Field(min_length=3, max_length=1000)

    @field_validator("original_date", "due_date")
    @classmethod
    def valid_date(cls, value):
        if value is not None:
            date.fromisoformat(value)
        return value

    @field_validator("amount")
    @classmethod
    def positive(cls, value):
        if money_to_cents(value) <= 0:
            raise ValueError("未结金额必须大于零")
        return value


class AllocationPart(InputModel):
    item_id: StrictStr = Field(min_length=1, max_length=100)
    amount: StrictStr

    @field_validator("amount")
    @classmethod
    def positive(cls, value):
        if money_to_cents(value) <= 0:
            raise ValueError("分配金额必须大于零")
        return value


class AllocationCreate(InputModel):
    book_id: StrictStr = Field(min_length=1, max_length=100)
    voucher_id: StrictStr = Field(min_length=1, max_length=100)
    parts: list[AllocationPart] = Field(min_length=1, max_length=100)
    idempotency_key: StrictStr = Field(min_length=8, max_length=120)
    reason: StrictStr = Field(min_length=3, max_length=1000)

    @field_validator("parts")
    @classmethod
    def unique_items(cls, value):
        if len({part.item_id for part in value}) != len(value):
            raise ValueError("同一收付款不能重复分配同一未结事项")
        return value


class AllocationReverse(InputModel):
    idempotency_key: StrictStr = Field(min_length=8, max_length=120)
    reason: StrictStr = Field(min_length=3, max_length=1000)


class ControlApplicability(InputModel):
    book_id: StrictStr = Field(min_length=1, max_length=100)
    status: Literal["not_applicable"]
    effective_from: StrictStr = Field(min_length=7, max_length=7)
    effective_to: StrictStr | None = Field(default=None, min_length=7, max_length=7)
    reason: StrictStr = Field(min_length=3, max_length=1000)
    confirmed_by: StrictStr | None = Field(default=None, max_length=100)

    @field_validator("effective_from", "effective_to")
    @classmethod
    def period(cls, value):
        if value is not None and not __import__("re").fullmatch(r"\d{4}-(0[1-9]|1[0-2])", value):
            raise ValueError("生效期间必须为 YYYY-MM")
        return value


class BankAccountCreate(InputModel):
    book_id: StrictStr = Field(min_length=1, max_length=100)
    name: StrictStr = Field(min_length=1, max_length=120)
    currency: Literal["CNY"] = "CNY"
    ledger_account_id: StrictStr = Field(min_length=1, max_length=100)
    ledger_account_version: int = Field(strict=True, ge=1)
    aux: dict[str, str] = Field(default_factory=dict)
    reason: StrictStr = Field(min_length=3, max_length=1000)


class BankCoverageCreate(InputModel):
    book_id: StrictStr = Field(min_length=1, max_length=100)
    account_id: StrictStr = Field(min_length=1, max_length=100)
    period: StrictStr = Field(min_length=7, max_length=7)
    opening: StrictStr
    closing: StrictStr
    source_ref: StrictStr = Field(min_length=1, max_length=500)
    reason: StrictStr = Field(min_length=3, max_length=1000)

    @field_validator("opening", "closing")
    @classmethod
    def money(cls, value):
        money_to_cents(value)
        return value


class BankLineCreate(InputModel):
    book_id: StrictStr = Field(min_length=1, max_length=100)
    bank_account_id: StrictStr | None = Field(default=None, max_length=100)
    line_date: StrictStr
    description: StrictStr = Field(min_length=1, max_length=300)
    amount: StrictStr

    @field_validator("line_date")
    @classmethod
    def valid_date(cls, value):
        date.fromisoformat(value)
        return value

    @field_validator("amount")
    @classmethod
    def signed(cls, value):
        from ..accounting.invariants import signed_money_to_cents

        signed_money_to_cents(value)
        return value


class BankMatch(InputModel):
    voucher_line_id: StrictStr = Field(min_length=1, max_length=100)


class BankAdjustment(InputModel):
    book_id: StrictStr = Field(min_length=1, max_length=100)
    period: StrictStr
    kind: Literal[
        "outstanding_deposit", "outstanding_check", "bank_fee", "interest", "error", "other"
    ]
    amount: StrictStr = Field(min_length=1)
    explanation: StrictStr = Field(min_length=3, max_length=1000)

    @field_validator("amount")
    @classmethod
    def signed(cls, value):
        from ..accounting.invariants import signed_money_to_cents

        signed_money_to_cents(value)
        return value
