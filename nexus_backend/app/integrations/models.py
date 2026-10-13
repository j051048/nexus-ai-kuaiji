from typing import Literal
from pydantic import Field, StrictStr, field_validator, model_validator
from app.accounting.models import InputModel, money_to_cents, cents_to_money
from app.accounting.invariants import validate_period


class AccountMapping(InputModel):
    local_code: StrictStr = Field(min_length=1, max_length=30)
    external_code: StrictStr = Field(min_length=1, max_length=100)


class AuxiliaryMapping(InputModel):
    kind: Literal['department', 'employee', 'counterparty', 'project']
    local_value: StrictStr = Field(min_length=1, max_length=200)
    external_code: StrictStr = Field(min_length=1, max_length=100)


class BridgeConfiguration(InputModel):
    expected_version: int = Field(strict=True, ge=0)
    external_book_id: StrictStr = Field(min_length=1, max_length=100)
    product_version: StrictStr = Field(default='待确认', min_length=1, max_length=100)
    deployment: Literal['unknown', 'on_premise', 'tplus_cloud'] = 'unknown'
    accounts: list[AccountMapping] = Field(min_length=1, max_length=1000)
    auxiliaries: list[AuxiliaryMapping] = Field(default_factory=list, max_length=2000)
    reason: StrictStr = Field(min_length=1, max_length=1000)
    idempotency_key: StrictStr = Field(min_length=8, max_length=120)

    @model_validator(mode='after')
    def unique_mappings(self):
        if len({x.local_code for x in self.accounts}) != len(self.accounts):
            raise ValueError('本地科目不能重复映射')
        if len({(x.kind, x.local_value) for x in self.auxiliaries}) != len(self.auxiliaries):
            raise ValueError('辅助核算值不能重复映射')
        return self


class BridgeRecord(InputModel):
    kind: Literal['account', 'counterparty', 'project', 'bank_account', 'voucher', 'balance']
    external_id: StrictStr = Field(min_length=1, max_length=100)
    code: StrictStr = Field(default='', max_length=100)
    name: StrictStr = Field(default='', max_length=500)
    period: StrictStr
    debit: StrictStr = '0.00'
    credit: StrictStr = '0.00'
    source_ref: StrictStr = Field(min_length=1, max_length=500)

    _period = field_validator('period')(validate_period)

    @field_validator('debit', 'credit')
    @classmethod
    def amount(cls, value):
        return cents_to_money(money_to_cents(value))


class BridgeImport(InputModel):
    format: Literal['json', 'csv']
    content: StrictStr = Field(min_length=1, max_length=2_000_000)
    reason: StrictStr = Field(min_length=1, max_length=1000)
    idempotency_key: StrictStr = Field(min_length=8, max_length=120)


class BridgeImportConfirm(InputModel):
    fingerprint: StrictStr = Field(min_length=64, max_length=64)


class BridgeExport(InputModel):
    export_id: StrictStr = Field(min_length=1, max_length=100)
    configuration_version: int = Field(strict=True, ge=1)
    reason: StrictStr = Field(min_length=1, max_length=1000)
    idempotency_key: StrictStr = Field(min_length=8, max_length=120)
