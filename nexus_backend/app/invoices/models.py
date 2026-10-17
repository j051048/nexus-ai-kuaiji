from pydantic import Field, StrictInt, StrictStr, field_validator

from ..accounting.models import InputModel


class InvoiceImportCreate(InputModel):
    upload_id: StrictStr = Field(pattern=r"^[0-9a-f]{32}$")


class InvoiceAccrualItem(InputModel):
    line: StrictInt = Field(ge=1, le=10_000_000)
    settlement_account: StrictStr = Field(min_length=1, max_length=30)
    profit_account: StrictStr = Field(min_length=1, max_length=30)
    tax_account: StrictStr = Field(default="", max_length=30)


class InvoiceAccrualRequest(InputModel):
    items: list[InvoiceAccrualItem] = Field(min_length=1, max_length=100)
    reason: StrictStr = Field(min_length=1, max_length=500)

    @field_validator("items")
    @classmethod
    def unique_lines(cls, value):
        if len({item.line for item in value}) != len(value):
            raise ValueError("同一发票行不可重复生成挂账草稿")
        return value
