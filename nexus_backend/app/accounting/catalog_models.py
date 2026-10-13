"""Client-maintained charts and deterministic, versioned journal templates."""
import re
from typing import Literal

from pydantic import Field, StrictStr, field_validator, model_validator

from .models import InputModel, cents_to_money, money_to_cents
from ..business.models import BusinessPayload

AuxiliaryDimension = Literal["department", "counterparty", "employee", "project"]
AccountCategory = Literal["asset", "liability", "equity", "cost", "income", "expense", "other"]


class AccountDefinition(InputModel):
    code: StrictStr = Field(min_length=1, max_length=30)
    name: StrictStr = Field(min_length=1, max_length=120)
    category: AccountCategory = "other"
    normal_balance: Literal["debit", "credit"] = "debit"
    active: bool = Field(default=True, strict=True)
    auxiliary_requirements: list[AuxiliaryDimension] = Field(default_factory=list, max_length=4)

    @field_validator("code")
    @classmethod
    def valid_code(cls, value):
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,29}", value):
            raise ValueError("科目代码仅允许字母、数字、点、下划线和短横线")
        return value

    @field_validator("auxiliary_requirements")
    @classmethod
    def unique_dimensions(cls, value):
        if len(value) != len(set(value)):
            raise ValueError("辅助核算要求不可重复")
        return sorted(value)


class AccountUpdate(AccountDefinition):
    expected_version: int = Field(strict=True, ge=1)


class AccountImportRow(AccountDefinition):
    expected_version: int | None = Field(default=None, strict=True, ge=1)


class AccountImportRequest(InputModel):
    rows: list[AccountImportRow] = Field(min_length=1, max_length=1000)

    @field_validator("rows")
    @classmethod
    def unique_codes(cls, value):
        if len({row.code for row in value}) != len(value):
            raise ValueError("同一批次的科目代码不可重复")
        return value


class TemplateLine(InputModel):
    account_code: StrictStr = Field(min_length=1, max_length=30)
    side: Literal["debit", "credit"]
    amount_field: Literal["gross", "net", "tax"] = "gross"


class TemplateDefinition(InputModel):
    name: StrictStr = Field(min_length=1, max_length=120)
    business_type: Literal["reimbursement", "procurement_expense", "daily_cashflow"]
    direction: Literal["expense", "income"]
    category: Literal["office", "travel", "rent", "communication", "other"] | None = None
    payment_status: Literal["paid", "unpaid"] | None = None
    active: bool = Field(default=True, strict=True)
    lines: list[TemplateLine] = Field(min_length=2, max_length=20)

    @model_validator(mode="after")
    def balanced_formula(self):
        # Verify the formula for every valid gross=net+tax, without evaluating
        # user expressions. A gross-only template has no implied tax judgment.
        coefficients = {"debit": {"net": 0, "tax": 0}, "credit": {"net": 0, "tax": 0}}
        for line in self.lines:
            for component in (("net", "tax") if line.amount_field == "gross" else (line.amount_field,)):
                coefficients[line.side][component] += 1
        if coefficients["debit"] != coefficients["credit"] or coefficients["debit"] != {"net": 1, "tax": 1}:
            raise ValueError("模板借贷两侧必须各等于一份含税总额，不允许重复计入金额")
        return self


class TemplateUpdate(TemplateDefinition):
    expected_version: int = Field(strict=True, ge=1)


class TemplatePreviewRequest(InputModel):
    template: TemplateDefinition
    payload: BusinessPayload
    department_id: StrictStr | None = Field(default=None, max_length=100)
    applicant_id: StrictStr | None = Field(default=None, max_length=100)
    recognized_net: StrictStr | None = None
    recognized_tax: StrictStr | None = None

    @field_validator("recognized_net", "recognized_tax")
    @classmethod
    def valid_recognized_amount(cls, value):
        return None if value is None else cents_to_money(money_to_cents(value))
