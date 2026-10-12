import re
from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictStr, field_validator


MAX_CENTS = 99_999_999_999_999
MONEY_PATTERN = re.compile(r"(?:0|[1-9][0-9]{0,11})(?:\.[0-9]{1,2})?\Z")
PERIOD_PATTERN = re.compile(r"[0-9]{4}-(?:0[1-9]|1[0-2])\Z")


def money_to_cents(value: str) -> int:
    if not isinstance(value, str) or not MONEY_PATTERN.fullmatch(value):
        raise ValueError("金额必须是非负十进制字符串，最多两位小数，不接受指数或浮点数")
    whole, _, fraction = value.partition(".")
    cents = int(whole) * 100 + int(fraction.ljust(2, "0") or "0")
    if cents > MAX_CENTS:
        raise ValueError("金额超出允许范围")
    return cents


def cents_to_money(cents: int) -> str:
    return f"{cents // 100}.{cents % 100:02d}"


class InputModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class BookCreate(InputModel):
    name: StrictStr = Field(min_length=1, max_length=120)
    period: StrictStr
    company_id: StrictStr | None = Field(default=None, min_length=1, max_length=100)

    @field_validator("period")
    @classmethod
    def valid_period(cls, value: str) -> str:
        if not PERIOD_PATTERN.fullmatch(value) or not 2000 <= int(value[:4]) <= 2099:
            raise ValueError("期间必须是 2000 至 2099 年内的 YYYY-MM")
        return value


class SourceRow(InputModel):
    external_id: StrictStr = Field(min_length=1, max_length=200)
    date: StrictStr
    description: StrictStr = Field(min_length=1, max_length=500)
    counterparty: StrictStr = Field(default="", max_length=200)
    amount: StrictStr
    direction: Literal["expense", "income"]
    evidence_ref: StrictStr = Field(default="", max_length=500)

    @field_validator("date")
    @classmethod
    def valid_date(cls, value: str) -> str:
        if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
            raise ValueError("日期必须是 YYYY-MM-DD")
        date.fromisoformat(value)
        return value

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


class DraftLine(InputModel):
    account_code: StrictStr = Field(min_length=1, max_length=30)
    debit: StrictStr
    credit: StrictStr

    @field_validator("debit", "credit")
    @classmethod
    def valid_amount(cls, value: str) -> str:
        return cents_to_money(money_to_cents(value))


class DraftUpdate(InputModel):
    expected_version: int = Field(strict=True, ge=1)
    description: StrictStr = Field(min_length=1, max_length=500)
    lines: list[DraftLine] = Field(min_length=1, max_length=100)


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
