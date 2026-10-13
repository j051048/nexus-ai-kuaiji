import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictStr, field_validator, model_validator


from .invariants import (
    MAX_CENTS,
    MONEY_PATTERN,
    PERIOD_PATTERN,
    money_to_cents,
    cents_to_money,
    validate_period,
    validate_date,
)


class InputModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class BookCreate(InputModel):
    name: StrictStr = Field(min_length=1, max_length=120)
    period: StrictStr
    company_id: StrictStr | None = Field(default=None, min_length=1, max_length=100)

    @field_validator("period")
    @classmethod
    def valid_period(cls, value: str) -> str:
        return validate_period(value)


class SourceRow(InputModel):
    external_id: StrictStr = Field(min_length=1, max_length=200)
    date: StrictStr
    description: StrictStr = Field(min_length=1, max_length=500)
    counterparty: StrictStr = Field(default="", max_length=200)
    amount: StrictStr
    direction: Literal["expense", "income"]
    evidence_ref: StrictStr = Field(default="", max_length=500)
    stable_transaction_id: StrictStr = Field(default="", max_length=200)

    @field_validator("date")
    @classmethod
    def valid_date(cls, value: str) -> str:
        return validate_date(value)

    @field_validator("amount")
    @classmethod
    def valid_amount(cls, value: str) -> str:
        cents = money_to_cents(value)
        if cents == 0:
            raise ValueError("流水金额必须大于零")
        return cents_to_money(cents)


class ImportRequest(InputModel):
    source_name: StrictStr = Field(min_length=1, max_length=120)
    rows: list[SourceRow] = Field(min_length=1, max_length=1000)
    namespace: StrictStr = Field(default="", max_length=100)
    bank_account: StrictStr = Field(default="", max_length=100)

    @model_validator(mode="after")
    def identity_scope(self):
        if bool(self.namespace) != bool(self.bank_account):
            raise ValueError("来源标识与银行账号标识须同时填写")
        if not self.namespace and any(row.stable_transaction_id for row in self.rows):
            raise ValueError("稳定交易编号须配合来源和银行账号标识")
        return self


class DraftLine(InputModel):
    account_code: StrictStr = Field(min_length=1, max_length=30)
    debit: StrictStr
    credit: StrictStr
    auxiliary_values: dict[
        Literal["department", "counterparty", "employee", "project"], StrictStr
    ] = Field(default_factory=dict, max_length=4)

    @field_validator("auxiliary_values")
    @classmethod
    def valid_auxiliaries(cls, value):
        if any(not item.strip() or len(item) > 200 for item in value.values()):
            raise ValueError("辅助核算值须为1–200字的非空文本")
        return {key: item.strip() for key, item in value.items()}

    @field_validator("debit", "credit")
    @classmethod
    def valid_amount(cls, value: str) -> str:
        return cents_to_money(money_to_cents(value))


class DraftUpdate(InputModel):
    expected_version: int = Field(strict=True, ge=1)
    description: StrictStr = Field(min_length=1, max_length=500)
    lines: list[DraftLine] = Field(min_length=1, max_length=100)
    correction_reason: StrictStr = Field(default="", max_length=1000)


class ApprovalRequest(InputModel):
    expected_version: int = Field(strict=True, ge=1)


class ExportRequest(InputModel):
    draft_ids: list[StrictStr] = Field(min_length=1, max_length=1000)
    idempotency_key: StrictStr = Field(min_length=8, max_length=120)

    @field_validator("draft_ids")
    @classmethod
    def unique_ids(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)) or any(not x or len(x) > 100 for x in value):
            raise ValueError("凭证 ID 必须有效且不可重复")
        return sorted(value)


class EvidenceRequest(InputModel):
    evidence_ref: StrictStr = Field(min_length=1, max_length=500)


class FirmCreate(InputModel):
    name: StrictStr = Field(min_length=1, max_length=120)


class CompanyCreate(InputModel):
    name: StrictStr = Field(min_length=1, max_length=120)
    tax_id: StrictStr = Field(default="", max_length=30)


class FirmMemberGrant(InputModel):
    email: StrictStr = Field(min_length=3, max_length=254)
    role: Literal["admin", "operator", "reviewer", "viewer"]

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value):
            raise ValueError("请输入有效邮箱")
        return value.lower()


class BookMemberGrant(InputModel):
    role: Literal["editor", "reviewer", "viewer"]


class CompanyMemberGrant(FirmMemberGrant):
    role: Literal["owner", "admin", "finance", "employee"]
    department_id: StrictStr | None = Field(default=None, min_length=1, max_length=100)


class DepartmentCreate(InputModel):
    name: StrictStr = Field(min_length=1, max_length=100)
