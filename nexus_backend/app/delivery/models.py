from typing import Literal
from pydantic import ConfigDict, Field, StrictStr, field_validator
from ..accounting.models import InputModel, money_to_cents, cents_to_money


class ReceiptLine(InputModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)
    ordinal: int | None = Field(default=None, strict=True, ge=0, le=99)
    account_code: StrictStr | None = Field(default=None, max_length=30)
    debit: StrictStr | None = None
    credit: StrictStr | None = None
    auxiliary_values: dict[StrictStr, StrictStr] | None = None

    @field_validator("debit", "credit")
    @classmethod
    def money(cls, value):
        return None if value is None else cents_to_money(money_to_cents(value))

    @field_validator("auxiliary_values")
    @classmethod
    def auxiliaries(cls, value):
        if value is not None and (set(value) - {"department", "employee", "counterparty", "project"} or any(not item.strip() or len(item) > 200 or "\x00" in item for item in value.values())):
            raise ValueError("辅助值必须为有效部门、人员、交易对方或项目")
        return value


class ReceiptItem(InputModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)
    draft_id: StrictStr = Field(min_length=1, max_length=100)
    external_voucher_id: StrictStr | None = Field(default=None, min_length=1, max_length=120)
    status: Literal["accepted", "rejected", "unknown"]
    date: StrictStr | None = Field(default=None, max_length=10)
    amount: StrictStr | None = None
    description: StrictStr | None = Field(default=None, max_length=500)
    lines: list[ReceiptLine] | None = Field(default=None, max_length=100)

    @field_validator("amount")
    @classmethod
    def money(cls, value):
        return None if value is None else cents_to_money(money_to_cents(value))

    @field_validator("external_voucher_id")
    @classmethod
    def nonblank(cls, value):
        if value is not None and (not value.strip() or "\x00" in value):
            raise ValueError("外部凭证号不能为空")
        return value


class ReceiptFacts(InputModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)
    book_id: StrictStr | None = Field(default=None, max_length=100)
    company_id: StrictStr | None = Field(default=None, max_length=100)
    period: StrictStr | None = Field(default=None, max_length=7)
    currency: StrictStr | None = Field(default=None, max_length=8)
    items: list[ReceiptItem] = Field(min_length=1, max_length=1000)


class ReceiptCreate(ReceiptFacts):
    idempotency_key: StrictStr = Field(min_length=8, max_length=120, pattern=r"^[A-Za-z0-9_.:-]+$")
    reason: StrictStr = Field(min_length=1, max_length=1000)

    @field_validator("reason")
    @classmethod
    def reason_text(cls, value):
        if not value.strip() or "\x00" in value:
            raise ValueError("请填写回执来源与核验依据")
        return value.strip()


class ReceiptImport(InputModel):
    # The raw source text, including BOM/trailing record delimiters, is evidence.
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)
    format: Literal["json", "csv"]
    content: StrictStr = Field(min_length=1, max_length=2_000_000)
    idempotency_key: StrictStr = Field(min_length=8, max_length=120, pattern=r"^[A-Za-z0-9_.:-]+$")
    reason: StrictStr = Field(min_length=1, max_length=1000)
    _reason = field_validator("reason")(ReceiptCreate.reason_text.__func__)


class DeliveryNote(InputModel):
    receipt_id: StrictStr = Field(min_length=1, max_length=100)
    draft_id: StrictStr = Field(min_length=1, max_length=100)
    issue_key: StrictStr = Field(min_length=1, max_length=100)
    idempotency_key: StrictStr = Field(min_length=8, max_length=120, pattern=r"^[A-Za-z0-9_.:-]+$")
    reason: StrictStr = Field(min_length=1, max_length=1000)
    _reason = field_validator("reason")(ReceiptCreate.reason_text.__func__)
