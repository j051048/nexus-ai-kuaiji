from typing import Literal

from pydantic import Field, StrictStr, field_validator, model_validator

from ..accounting.catalog_models import TemplateDefinition, TemplateLine
from ..accounting.models import InputModel


class RuleConditions(InputModel):
    business_type: Literal["reimbursement", "procurement_expense", "daily_cashflow"]
    direction: Literal["expense", "income"]
    category: Literal["office", "travel", "rent", "communication", "other"] | None = None
    payment_status: Literal["paid", "unpaid"]
    counterparty: StrictStr = Field(min_length=1, max_length=200)
    counterparty_id: StrictStr | None = Field(default=None, min_length=1, max_length=100)

    @field_validator("counterparty")
    @classmethod
    def exact_nonblank(cls, value):
        if not value.strip() or value != value.strip():
            raise ValueError("交易对方须为无首尾空白的精确名称")
        return value


class RuleDefinition(InputModel):
    name: StrictStr = Field(min_length=1, max_length=120)
    source_kind: Literal["business", "bank"] = "business"
    conditions: RuleConditions
    lines: list[TemplateLine] = Field(min_length=2, max_length=20)

    @model_validator(mode="after")
    def valid_template(self):
        self.as_template()
        c = self.conditions
        if self.source_kind == "business" and c.category is None:
            raise ValueError("业务规则须限定事项类别")
        if self.source_kind == "bank" and (c.business_type != "daily_cashflow" or c.payment_status != "paid" or c.category is not None or any(line.amount_field != "gross" for line in self.lines)):
            raise ValueError("流水规则仅按交易对方和方向匹配，金额使用总额")
        if (c.business_type == "reimbursement" and (c.direction != "expense" or c.payment_status != "unpaid")) or (c.business_type == "procurement_expense" and c.direction != "expense") or (c.business_type == "daily_cashflow" and c.payment_status != "paid"):
            raise ValueError("规则条件不符合业务模板")
        return self

    def as_template(self):
        c = self.conditions
        return TemplateDefinition(name=self.name, business_type=c.business_type, direction=c.direction,
                                  category=c.category, payment_status=c.payment_status, lines=self.lines)


class RuleCreate(InputModel):
    definition: RuleDefinition
    sample_ids: list[StrictStr] = Field(min_length=1, max_length=100)
    reason: StrictStr = Field(min_length=1, max_length=1000)

    @field_validator("sample_ids")
    @classmethod
    def unique_samples(cls, value):
        if any(not item or len(item) > 100 for item in value) or len(set(value)) != len(value):
            raise ValueError("样例标识不可为空、过长或重复")
        return sorted(value)

    @field_validator("reason")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("须说明会计处理依据")
        return value.strip()


class RuleUpdate(RuleCreate):
    expected_version: int = Field(strict=True, ge=1)
    expected_state_version: int = Field(strict=True, ge=1)


class RuleEvaluate(InputModel):
    expected_version: int = Field(strict=True, ge=1)
    negative_sample_ids: list[StrictStr] = Field(min_length=1, max_length=100)

    @field_validator("negative_sample_ids")
    @classmethod
    def unique_samples(cls, value):
        return RuleCreate.unique_samples(value)


class RulePublish(InputModel):
    expected_version: int = Field(strict=True, ge=1)
    expected_state_version: int = Field(strict=True, ge=1)
    evaluation_id: StrictStr = Field(min_length=1, max_length=100)
    reason: StrictStr = Field(min_length=1, max_length=1000)

    @field_validator("reason")
    @classmethod
    def nonblank(cls, value):
        return RuleCreate.nonblank(value)


class RuleRetire(InputModel):
    expected_version: int = Field(strict=True, ge=1)
    expected_state_version: int = Field(strict=True, ge=1)
    reason: StrictStr = Field(min_length=1, max_length=1000)

    @field_validator("reason")
    @classmethod
    def nonblank(cls, value):
        return RuleCreate.nonblank(value)
