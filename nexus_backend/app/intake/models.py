from typing import Literal

from pydantic import Field, StrictStr

from ..accounting.models import InputModel
from ..business.models import BusinessPayload


class IntakeFields(InputModel):
    occurred_on: StrictStr = Field(default="", max_length=10)
    description: StrictStr = Field(default="", max_length=500)
    counterparty: StrictStr = Field(default="", max_length=200)
    amount: StrictStr = Field(default="", max_length=30)
    direction: Literal["expense", "income"] = "expense"
    payment_status: Literal["paid", "unpaid"] = "unpaid"
    category: Literal["office", "travel", "rent", "communication", "other"] = "other"
    purpose: StrictStr = Field(default="", max_length=1000)
    project: StrictStr = Field(default="", max_length=200)


class IntakeCreate(InputModel):
    company_id: StrictStr = Field(min_length=1, max_length=100)
    department_id: StrictStr = Field(min_length=1, max_length=100)
    template_id: Literal["reimbursement", "procurement_expense", "daily_cashflow"] = "reimbursement"
    fields: IntakeFields = Field(default_factory=IntakeFields)
    idempotency_key: StrictStr = Field(min_length=8, max_length=120)


class IntakeUpdate(InputModel):
    expected_version: int = Field(strict=True, ge=1)
    template_id: Literal["reimbursement", "procurement_expense", "daily_cashflow"]
    fields: IntakeFields


class IntakeRecognize(InputModel):
    expected_version: int = Field(strict=True, ge=1)
    idempotency_key: StrictStr = Field(min_length=8, max_length=120)


class IntakeComplete(InputModel):
    expected_version: int = Field(strict=True, ge=1)
    payload: BusinessPayload
    fields_confirmed: bool = Field(strict=True)
    submit: bool = Field(default=True, strict=True)
    idempotency_key: StrictStr = Field(min_length=8, max_length=120)
