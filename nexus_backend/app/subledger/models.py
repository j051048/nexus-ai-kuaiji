"""Subledger contracts; allocations change subledger facts, not posted vouchers."""

from datetime import date
from typing import Literal

from pydantic import Field, StrictStr, field_validator

from ..accounting.models import InputModel, money_to_cents

ItemKind = Literal["receivable", "payable", "advance_received", "advance_paid"]


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


class BankLineCreate(InputModel):
    book_id: StrictStr = Field(min_length=1, max_length=100)
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
