"""B02 opening preparation contracts; no posting, voucher number or ledger switch."""

import re
from typing import Literal

from pydantic import Field, StrictStr, field_validator

from ..accounting.invariants import signed_money_to_cents, validate_period
from ..accounting.models import InputModel

StandardKind = Literal["small_enterprise", "enterprise"]
TaxpayerKind = Literal["general", "small_scale", "non_taxpayer"]
AccountCategory = Literal["asset", "liability", "equity", "cost", "income", "expense", "other"]
AuxiliaryDimension = Literal["department", "counterparty", "employee", "project"]


class PolicyCreate(InputModel):
    ledger_id: StrictStr = Field(min_length=1, max_length=100)
    standard_kind: StandardKind
    taxpayer_kind: TaxpayerKind
    industry: StrictStr = Field(default="", max_length=120)
    fiscal_year_start_month: int = Field(default=1, strict=True, ge=1, le=12)
    effective_from: StrictStr
    effective_to: StrictStr | None = None
    reason: StrictStr = Field(min_length=3, max_length=1000)

    @field_validator("effective_from", "effective_to")
    @classmethod
    def valid_period(cls, value):
        return None if value is None else validate_period(value)


class AccountCreate(InputModel):
    ledger_id: StrictStr = Field(min_length=1, max_length=100)
    code: StrictStr = Field(min_length=1, max_length=30)
    name: StrictStr = Field(min_length=1, max_length=120)
    parent_account_id: StrictStr | None = Field(default=None, max_length=100)
    is_leaf: bool = Field(default=True, strict=True)
    category: AccountCategory = "other"
    normal_balance: Literal["debit", "credit"] = "debit"
    auxiliary_requirements: list[AuxiliaryDimension] = Field(default_factory=list, max_length=4)
    effective_from: StrictStr
    effective_to: StrictStr | None = None
    reason: StrictStr = Field(min_length=3, max_length=1000)

    @field_validator("code")
    @classmethod
    def valid_code(cls, value):
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,29}", value):
            raise ValueError("科目代码仅允许字母、数字、点、下划线和短横线")
        return value

    @field_validator("auxiliary_requirements")
    @classmethod
    def unique_aux(cls, value):
        if len(value) != len(set(value)):
            raise ValueError("辅助核算要求不可重复")
        return sorted(value)

    @field_validator("effective_from", "effective_to")
    @classmethod
    def valid_period(cls, value):
        return None if value is None else validate_period(value)


class AccountRevision(AccountCreate):
    expected_version: int = Field(strict=True, ge=1)


class MappingCreate(InputModel):
    book_id: StrictStr = Field(min_length=1, max_length=100)
    book_account_code: StrictStr = Field(min_length=1, max_length=30)
    account_id: StrictStr = Field(min_length=1, max_length=100)
    account_version: int = Field(strict=True, ge=1)
    reason: StrictStr = Field(min_length=3, max_length=1000)


class OpeningLineInput(InputModel):
    account_id: StrictStr = Field(min_length=1, max_length=100)
    account_version: int = Field(strict=True, ge=1)
    auxiliary_values: dict = Field(default_factory=dict)
    year_opening: StrictStr
    ytd_debit: StrictStr
    ytd_credit: StrictStr
    opening: StrictStr
    source_row: dict = Field(default_factory=dict)

    @field_validator("year_opening", "ytd_debit", "ytd_credit", "opening")
    @classmethod
    def valid_money(cls, value):
        signed_money_to_cents(value)
        return value


class OpeningOpenItemInput(InputModel):
    kind: Literal["receivable", "payable"]
    counterparty_id: StrictStr = Field(min_length=1, max_length=100)
    original_date: StrictStr
    remaining: StrictStr
    source_ref: StrictStr = Field(default="", max_length=500)

    @field_validator("original_date")
    @classmethod
    def valid_date(cls, value):
        from datetime import date

        date.fromisoformat(value)
        return value

    @field_validator("remaining")
    @classmethod
    def non_negative(cls, value):
        if signed_money_to_cents(value) < 0:
            raise ValueError("未结金额不能为负数")
        return value


class OpeningImport(InputModel):
    idempotency_key: StrictStr = Field(min_length=8, max_length=120)
    reason: StrictStr = Field(min_length=3, max_length=1000)
    lines: list[OpeningLineInput] = Field(min_length=1, max_length=5000)
    open_items: list[OpeningOpenItemInput] = Field(default_factory=list, max_length=5000)


class OpeningCommand(InputModel):
    expected_version: int = Field(strict=True, ge=1)
    reason: StrictStr = Field(min_length=3, max_length=1000)


class OpeningPackageCreate(InputModel):
    ledger_id: StrictStr = Field(min_length=1, max_length=100)
    target_period: StrictStr
    policy_id: StrictStr = Field(min_length=1, max_length=100)

    @field_validator("target_period")
    @classmethod
    def valid_period(cls, value):
        return validate_period(value)
