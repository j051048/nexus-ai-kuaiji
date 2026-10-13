from typing import Literal

from pydantic import Field, StrictStr, field_validator

from ..accounting.models import BookCreate, InputModel
from ..learning.models import RuleCreate, RuleEvaluate


class PeriodCreate(InputModel):
    period: StrictStr
    name: StrictStr = Field(min_length=1, max_length=120)
    source_book_id: StrictStr = Field(min_length=1, max_length=100)
    inherit_user_ids: list[StrictStr] = Field(default_factory=list, max_length=100)
    inherit_rule_ids: list[StrictStr] = Field(default_factory=list, max_length=100)
    idempotency_key: StrictStr = Field(min_length=8, max_length=120)

    @field_validator("period")
    @classmethod
    def valid_period(cls, value):
        return BookCreate.valid_period(value)

    @field_validator("inherit_user_ids", "inherit_rule_ids")
    @classmethod
    def unique(cls, value):
        if len(value) != len(set(value)) or any(not item or len(item) > 100 for item in value):
            raise ValueError("继承标识不得为空、重复或过长")
        return sorted(value)


class PeriodLink(InputModel):
    source_book_id: StrictStr = Field(min_length=1, max_length=100)
    reason: StrictStr = Field(min_length=3, max_length=1000)
    expected_ledger_id: StrictStr = Field(min_length=1, max_length=150)


class ManagerGrant(InputModel):
    user_id: StrictStr = Field(min_length=1, max_length=100)
    reason: StrictStr = Field(min_length=3, max_length=1000)


class EntityCreate(InputModel):
    kind: Literal["counterparty", "project", "bank_account"]
    code: StrictStr = Field(min_length=1, max_length=100)
    name: StrictStr = Field(min_length=1, max_length=200)


class EntityUpdate(InputModel):
    expected_version: int = Field(strict=True, ge=1)
    name: StrictStr = Field(min_length=1, max_length=200)
    active: bool = Field(strict=True)
    reason: StrictStr = Field(min_length=3, max_length=1000)


class AliasCreate(InputModel):
    entity_id: StrictStr = Field(min_length=1, max_length=100)
    original: StrictStr = Field(min_length=1, max_length=200)
    reason: StrictStr = Field(min_length=3, max_length=1000)


class AliasDecision(InputModel):
    expected_version: int = Field(strict=True, ge=1)
    decision: Literal["approve", "reject", "retire"]
    reason: StrictStr = Field(min_length=3, max_length=1000)


class InheritanceActivate(InputModel):
    sample_ids: list[StrictStr] = Field(min_length=1, max_length=100)
    negative_sample_ids: list[StrictStr] = Field(min_length=1, max_length=100)
    reason: StrictStr = Field(min_length=3, max_length=1000)

    @field_validator("sample_ids")
    @classmethod
    def positive(cls, value):
        return RuleCreate.unique_samples(value)

    @field_validator("negative_sample_ids")
    @classmethod
    def negative(cls, value):
        return RuleEvaluate.unique_samples(value)
