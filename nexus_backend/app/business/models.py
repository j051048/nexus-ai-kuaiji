"""业务输入契约：固定人民币、精确字符串金额、严格乐观锁。"""
import re
from datetime import date
from typing import Literal

from pydantic import Field, StrictStr, field_validator

from app.accounting.models import InputModel, cents_to_money, money_to_cents


class BusinessPayload(InputModel):
    occurred_on: StrictStr
    description: StrictStr = Field(min_length=1, max_length=500)
    counterparty: StrictStr = Field(min_length=1, max_length=200)
    amount: StrictStr
    direction: Literal["expense", "income"]
    payment_status: Literal["paid", "unpaid"]
    category: Literal["office", "travel", "rent", "communication", "other"]
    purpose: StrictStr = Field(default="", max_length=1000)
    project: StrictStr = Field(default="", max_length=200)

    @field_validator("occurred_on")
    @classmethod
    def valid_date(cls, value):
        if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
            raise ValueError("发生日期必须是 YYYY-MM-DD")
        parsed = date.fromisoformat(value)
        if not 2000 <= parsed.year <= 2099:
            raise ValueError("发生日期应在 2000 至 2099 年内")
        return value

    @field_validator("amount")
    @classmethod
    def valid_amount(cls, value):
        cents = money_to_cents(value)
        if cents == 0:
            raise ValueError("事项金额必须大于零")
        return cents_to_money(cents)


class BusinessEventCreate(InputModel):
    company_id: StrictStr = Field(min_length=1, max_length=100)
    department_id: StrictStr | None = Field(default=None, min_length=1, max_length=100)
    template_id: Literal["reimbursement", "procurement_expense", "daily_cashflow"]
    template_version: int = Field(default=1, strict=True, ge=1, le=1)
    idempotency_key: StrictStr | None = Field(default=None, min_length=8, max_length=120)
    payload: BusinessPayload


class VersionRequest(InputModel):
    expected_version: int = Field(strict=True, ge=1)


class BusinessEventUpdate(VersionRequest):
    payload: BusinessPayload


class ReturnRequest(VersionRequest):
    reason: StrictStr = Field(min_length=1, max_length=1000)


class HandoffRequest(VersionRequest):
    book_id: StrictStr = Field(min_length=1, max_length=100)
    idempotency_key: StrictStr = Field(min_length=8, max_length=120)


class DepartmentApproverGrant(InputModel):
    user_id: StrictStr = Field(min_length=1, max_length=100)
