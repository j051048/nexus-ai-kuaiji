from typing import Literal
from pydantic import Field, StrictInt, StrictStr, model_validator
from ..accounting.models import InputModel

FIELDS = ('external_id', 'stable_transaction_id', 'date', 'description', 'counterparty', 'amount', 'direction', 'income', 'expense', 'evidence_ref')


class PasteRequest(InputModel):
    filename: StrictStr = Field(default='粘贴流水.csv', min_length=1, max_length=150)
    content: StrictStr = Field(min_length=1, max_length=2_097_152)


class MappingSettings(InputModel):
    columns: dict[Literal['external_id', 'stable_transaction_id', 'date', 'description', 'counterparty', 'amount', 'direction', 'income', 'expense', 'evidence_ref'], StrictInt] = Field(max_length=10)
    date_format: Literal['ymd', 'dmy', 'mdy', 'excel_serial'] = 'ymd'
    amount_mode: Literal['direction', 'split', 'income_positive', 'expense_positive'] = 'direction'
    allow_grouping: bool = Field(default=False, strict=True)

    @model_validator(mode='after')
    def bounded_columns(self):
        if any(type(value) is not int or not 0 <= value < 64 for value in self.columns.values()):
            raise ValueError('字段列号须为0–63的整数')
        if len(set(self.columns.values())) != len(self.columns):
            raise ValueError('一列只能对应一个字段，请检查映射')
        required = {'date', 'description'} | ({'income', 'expense'} if self.amount_mode == 'split' else {'amount', 'direction'} if self.amount_mode == 'direction' else {'amount'})
        if not required.issubset(self.columns):
            raise ValueError('请完整对应日期、摘要以及所选金额模式所需字段')
        return self


class PreviewRequest(InputModel):
    file_id: StrictStr = Field(min_length=1, max_length=100)
    namespace: StrictStr = Field(min_length=1, max_length=100)
    bank_account: StrictStr = Field(min_length=1, max_length=100)
    mapping: MappingSettings


class ConfirmRequest(InputModel):
    remember_mapping: bool = Field(default=False, strict=True)
