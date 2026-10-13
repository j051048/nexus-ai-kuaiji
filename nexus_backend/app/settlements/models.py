from pydantic import Field, StrictStr, field_validator
from ..accounting.models import InputModel, money_to_cents, cents_to_money


def validate_money(value):
    cents = money_to_cents(value)
    if cents <= 0:
        raise ValueError("金额必须大于零")
    return cents_to_money(cents)


class OpenItemCreate(InputModel):
    source_id: StrictStr = Field(min_length=1, max_length=100)
    counterparty_id: StrictStr = Field(min_length=1, max_length=100)
    expected_draft_version: int = Field(strict=True, ge=1)
    idempotency_key: StrictStr = Field(min_length=8, max_length=120)
    reason: StrictStr = Field(min_length=3, max_length=1000)


class AllocationPart(InputModel):
    item_id: StrictStr = Field(min_length=1, max_length=100)
    amount: StrictStr

    @field_validator("amount")
    @classmethod
    def money(cls, value):
        return validate_money(value)


class AllocationPreview(InputModel):
    bank_source_id: StrictStr = Field(min_length=1, max_length=100)
    parts: list[AllocationPart] = Field(min_length=1, max_length=50)
    reason: StrictStr = Field(min_length=3, max_length=1000)

    @field_validator("parts")
    @classmethod
    def unique(cls, parts):
        if len({part.item_id for part in parts}) != len(parts):
            raise ValueError("每次分配不能重复选择未结事项")
        return sorted(parts, key=lambda part: part.item_id)


class AllocationConfirm(InputModel):
    idempotency_key: StrictStr = Field(min_length=8, max_length=120)


class AllocationReverse(InputModel):
    expected_fingerprint: StrictStr = Field(min_length=64, max_length=64)
    idempotency_key: StrictStr = Field(min_length=8, max_length=120)
    reason: StrictStr = Field(min_length=3, max_length=1000)


class WeightedPart(InputModel):
    key: StrictStr = Field(min_length=1, max_length=100)
    weight: int = Field(strict=True, ge=1, le=1_000_000_000)


class SplitRequest(InputModel):
    total: StrictStr
    parts: list[WeightedPart] = Field(min_length=1, max_length=50)

    @field_validator("total")
    @classmethod
    def money(cls, value):
        return validate_money(value)

    @field_validator("parts")
    @classmethod
    def unique(cls, parts):
        if len({part.key for part in parts}) != len(parts):
            raise ValueError("分摊标识不能重复")
        return sorted(parts, key=lambda part: part.key)
