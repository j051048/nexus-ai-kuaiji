"""Periodic draft contracts. Generated lines must still be reviewed and posted by B03."""

from datetime import date
from typing import Literal

from pydantic import Field, StrictStr, field_validator

from ..accounting.models import InputModel, money_to_cents


class AccrualRule(InputModel):
    book_id: StrictStr = Field(min_length=1, max_length=100)
    name: StrictStr = Field(min_length=1, max_length=120)
    amount: StrictStr
    debit_account_id: StrictStr = Field(min_length=1, max_length=100)
    debit_account_version: int = Field(strict=True, ge=1)
    credit_account_id: StrictStr = Field(min_length=1, max_length=100)
    credit_account_version: int = Field(strict=True, ge=1)
    reason: StrictStr = Field(min_length=3, max_length=1000)

    @field_validator("amount")
    @classmethod
    def positive(cls, value):
        if money_to_cents(value) <= 0:
            raise ValueError("计提金额必须大于零")
        return value


class DepreciableAsset(InputModel):
    book_id: StrictStr = Field(min_length=1, max_length=100)
    kind: Literal["fixed", "intangible"]
    name: StrictStr = Field(min_length=1, max_length=120)
    cost: StrictStr
    residual: StrictStr = "0.00"
    life_months: int = Field(strict=True, ge=1, le=600)
    start_period: StrictStr
    expense_account_id: StrictStr = Field(min_length=1, max_length=100)
    expense_account_version: int = Field(strict=True, ge=1)
    accum_account_id: StrictStr = Field(min_length=1, max_length=100)
    accum_account_version: int = Field(strict=True, ge=1)
    reason: StrictStr = Field(min_length=3, max_length=1000)

    @field_validator("start_period")
    @classmethod
    def valid_start(cls, value):
        from ..accounting.invariants import validate_period

        return validate_period(value)

    @field_validator("cost", "residual")
    @classmethod
    def money(cls, value):
        money_to_cents(value)
        return value


class GeneratePeriodic(InputModel):
    book_id: StrictStr = Field(min_length=1, max_length=100)
    period: StrictStr
    idempotency_key: StrictStr = Field(min_length=8, max_length=120)
    reason: StrictStr = Field(min_length=3, max_length=1000)

    @field_validator("period")
    @classmethod
    def valid_period(cls, value):
        from ..accounting.invariants import validate_period

        return validate_period(value)


class ClosingGenerate(GeneratePeriodic):
    profit_account_id: StrictStr = Field(min_length=1, max_length=100)
    profit_account_version: int = Field(strict=True, ge=1)


class DraftCommand(InputModel):
    expected_version: int = Field(strict=True, ge=1)
    reason: StrictStr = Field(min_length=3, max_length=1000)


class AssetReconcile(InputModel):
    book_id: StrictStr = Field(min_length=1, max_length=100)
    expected_fingerprint: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    reason: StrictStr = Field(min_length=3, max_length=1000)


class DraftPost(DraftCommand):
    command_key: StrictStr = Field(min_length=8, max_length=120)
    voucher_date: StrictStr

    @field_validator("voucher_date")
    @classmethod
    def valid_date(cls, value):
        date.fromisoformat(value)
        return value
