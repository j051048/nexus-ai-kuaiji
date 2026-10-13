from pydantic import Field, StrictStr, field_validator
from app.accounting.models import InputModel


class BankSelection(InputModel):
    source_id: StrictStr = Field(min_length=1, max_length=100)
    source_fingerprint: StrictStr = Field(min_length=1, max_length=100)
    draft_version: int = Field(strict=True, ge=1)


class MatchItem(InputModel):
    book_id: StrictStr = Field(min_length=1, max_length=100)
    event_id: StrictStr = Field(min_length=1, max_length=100)
    event_version: int = Field(strict=True, ge=1)
    fact_version: int = Field(strict=True, ge=1)
    business_draft_version: int = Field(strict=True, ge=1)
    business_source_fingerprint: StrictStr = Field(min_length=1, max_length=100)
    banks: list[BankSelection] = Field(min_length=1, max_length=20)

    @field_validator("banks")
    @classmethod
    def unique_banks(cls, value):
        if len({item.source_id for item in value}) != len(value):
            raise ValueError("同一事项不能重复选择银行流水")
        return sorted(value, key=lambda item: item.source_id)


class ConfirmRequest(InputModel):
    items: list[MatchItem] = Field(min_length=1, max_length=20)
    idempotency_key: StrictStr = Field(min_length=8, max_length=120)
    reason: StrictStr = Field(min_length=1, max_length=1000)

    @field_validator("reason")
    @classmethod
    def useful_reason(cls, value):
        if not value.strip() or "\x00" in value:
            raise ValueError("请填写已核对金额、交易对方和收付事实的依据")
        return value.strip()

    @field_validator("items")
    @classmethod
    def unique_items(cls, value):
        events = [item.event_id for item in value]
        banks = [bank.source_id for item in value for bank in item.banks]
        if len(events) != len(set(events)) or len(banks) != len(set(banks)):
            raise ValueError("整批中同一事项或银行流水只能使用一次")
        return sorted(value, key=lambda item: (item.book_id, item.event_id))
