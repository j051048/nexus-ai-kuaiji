"""B03 formal voucher and posting contracts; no reporting or month-end engine."""

import re
from datetime import date
from typing import Literal

from pydantic import Field, StrictStr, field_validator

from ..accounting.models import InputModel, money_to_cents

VoucherType = Literal["记", "收", "付", "转"]


class OpeningBind(InputModel):
    book_id: StrictStr = Field(min_length=1, max_length=100)
    package_version_id: StrictStr = Field(min_length=1, max_length=100)
    reason: StrictStr = Field(min_length=3, max_length=1000)


class PostDraft(InputModel):
    command_key: StrictStr = Field(min_length=8, max_length=120)
    expected_version: int = Field(strict=True, ge=1)
    voucher_date: StrictStr
    voucher_type: VoucherType = "记"
    description: StrictStr = Field(default="", max_length=500)

    @field_validator("voucher_date")
    @classmethod
    def valid_date(cls, value):
        date.fromisoformat(value)
        return value


class ManualLine(InputModel):
    account_id: StrictStr = Field(min_length=1, max_length=100)
    account_version: int = Field(strict=True, ge=1)
    debit: StrictStr = "0.00"
    credit: StrictStr = "0.00"
    auxiliary_values: dict = Field(default_factory=dict)
    source_ref: StrictStr = Field(default="", max_length=200)

    @field_validator("debit", "credit")
    @classmethod
    def valid_money(cls, value):
        money_to_cents(value)
        return value


class ManualVoucher(InputModel):
    review_request_id: StrictStr | None = Field(default=None, min_length=1, max_length=100)
    command_key: StrictStr = Field(min_length=8, max_length=120)
    book_id: StrictStr = Field(min_length=1, max_length=100)
    voucher_date: StrictStr
    voucher_type: VoucherType = "记"
    description: StrictStr = Field(min_length=1, max_length=500)
    lines: list[ManualLine] = Field(min_length=2, max_length=200)
    relation_kind: Literal["adjustment", "reversal"] | None = None
    original_voucher_id: StrictStr | None = Field(default=None, max_length=100)
    reason: StrictStr = Field(default="手工凭证", min_length=3, max_length=1000)

    @field_validator("voucher_date")
    @classmethod
    def valid_date(cls, value):
        date.fromisoformat(value)
        return value

    @field_validator("lines")
    @classmethod
    def unique_account_aux(cls, value):
        keys = [(line.account_id, str(sorted(line.auxiliary_values.items()))) for line in value]
        if len(keys) != len(set(keys)):
            raise ValueError("同一科目及辅助维度不能在同一凭证中重复")
        return value


class ReverseVoucher(InputModel):
    review_request_id: StrictStr | None = Field(default=None, min_length=1, max_length=100)
    command_key: StrictStr = Field(min_length=8, max_length=120)
    voucher_date: StrictStr
    reason: StrictStr = Field(min_length=3, max_length=1000)

    @field_validator("voucher_date")
    @classmethod
    def valid_date(cls, value):
        date.fromisoformat(value)
        return value


class CommandLookup(InputModel):
    book_id: StrictStr = Field(min_length=1, max_length=100)
    command_key: StrictStr = Field(min_length=8, max_length=120)
