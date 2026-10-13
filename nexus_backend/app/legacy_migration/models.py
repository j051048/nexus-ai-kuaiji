"""Commands for isolated legacy-book migration; no current ledger mutation."""

from typing import Literal
import calendar
from datetime import date, timedelta

from pydantic import Field, StrictStr, field_validator, model_validator

from ..accounting.invariants import validate_date, validate_period
from ..accounting.models import InputModel


class ProjectCreate(InputModel):
    target_book_id: StrictStr = Field(min_length=1, max_length=100)
    source_product: Literal["chanjet_tplus", "yonyou", "kingdee", "generic"] = "generic"
    product_version: StrictStr = Field(default="待确认", min_length=1, max_length=100)
    external_book_id: StrictStr = Field(min_length=1, max_length=100)
    source_system_key: StrictStr = Field(min_length=1, max_length=100)
    history_start: StrictStr
    history_end: StrictStr
    cutover_date: StrictStr
    currency: Literal["CNY"] = "CNY"
    idempotency_key: StrictStr = Field(min_length=8, max_length=120)
    reason: StrictStr = Field(min_length=1, max_length=1000)

    _dates = field_validator("history_start", "history_end", "cutover_date")(validate_date)

    @model_validator(mode="after")
    def ordered_range(self):
        if self.history_start > self.history_end:
            raise ValueError("历史起始期间不能晚于结束期间")
        if (
            self.history_start[8:] != "01"
            or int(self.history_end[8:])
            != calendar.monthrange(int(self.history_end[:4]), int(self.history_end[5:7]))[1]
        ):
            raise ValueError("历史范围须从月首至月末，才能完整复算")
        if (
            self.cutover_date
            != (date.fromisoformat(self.history_end) + timedelta(days=1)).isoformat()
        ):
            raise ValueError("接管日期须为历史结束日的次日（月首），不能有月份空档或重叠")
        for value in (self.history_start, self.history_end, self.cutover_date):
            validate_period(value[:7])
        return self


class AccountMapping(InputModel):
    source_code: StrictStr = Field(min_length=1, max_length=100)
    target_code: StrictStr = Field(min_length=1, max_length=100)
    action: Literal["use_existing", "create"]


class AuxiliaryMapping(InputModel):
    kind: Literal["counterparty", "project", "bank_account", "department", "employee"]
    source_code: StrictStr = Field(min_length=1, max_length=100)
    target_id: StrictStr | None = Field(default=None, min_length=1, max_length=100)
    action: Literal["use_existing", "create"]

    @model_validator(mode="after")
    def target_required(self):
        if self.action == "use_existing" and not self.target_id:
            raise ValueError("使用已有辅助档案时须指定目标 ID")
        return self


class VersionCommand(InputModel):
    expected_version: int = Field(strict=True, ge=1)
    reason: StrictStr = Field(min_length=1, max_length=1000)
    idempotency_key: StrictStr = Field(min_length=8, max_length=120)


class MappingSave(VersionCommand):
    accounts: list[AccountMapping] = Field(default_factory=list, max_length=5000)
    auxiliaries: list[AuxiliaryMapping] = Field(default_factory=list, max_length=20000)
    settlement_accounts: dict[Literal["receivable", "payable"], list[StrictStr]] = Field(
        default_factory=dict, max_length=2
    )

    @model_validator(mode="after")
    def unique_source_keys(self):
        if len({row.source_code for row in self.accounts}) != len(self.accounts):
            raise ValueError("来源科目不能重复映射")
        if len({(row.kind, row.source_code) for row in self.auxiliaries}) != len(self.auxiliaries):
            raise ValueError("来源辅助档案不能重复映射")
        used = set()
        for codes in self.settlement_accounts.values():
            if (
                len(codes) > 100
                or any(not code or len(code) > 100 for code in codes)
                or len(set(codes)) != len(codes)
                or used.intersection(codes)
            ):
                raise ValueError("应收应付范围最多各 100 个科目且不能重复、交叉")
            used.update(codes)
        return self


class MigrationConfirm(VersionCommand):
    validation_id: StrictStr = Field(min_length=1, max_length=100)
    basis_token: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    confirmed: bool = Field(strict=True)

    @model_validator(mode="after")
    def human_confirmed(self):
        if not self.confirmed:
            raise ValueError("须由会计明确确认验收报告")
        return self


class FileMapping(InputModel):
    confirmed: bool = Field(default=False, strict=True)
    columns: dict[str, int | str] = Field(default_factory=dict, max_length=64)
    date_format: Literal["ymd", "dmy", "mdy", "excel_serial"] = "ymd"
    allow_grouping: bool = Field(default=False, strict=True)
    balance_mode: Literal["net_debit", "split"] = "net_debit"
    status_values: dict[str, Literal["posted", "unposted", "void"]] = Field(
        default_factory=dict, max_length=30
    )
    sheet_name: StrictStr | None = Field(default=None, max_length=100)

    @field_validator("columns", mode="before")
    @classmethod
    def strict_columns(cls, columns):
        if not isinstance(columns, dict):
            raise ValueError("列映射须为对象")
        if any(type(value) is not int and type(value) is not str for value in columns.values()):
            raise ValueError("列映射须为整数位置或完整表头")
        if any(type(value) is int and not 0 <= value < 64 for value in columns.values()):
            raise ValueError("列号须为 0–63")
        return columns


class ParseCommand(VersionCommand):
    file_id: StrictStr = Field(min_length=1, max_length=100)
    category: Literal["package", "account", "auxiliary", "voucher", "balance", "open_item"]
    mapping: FileMapping = Field(default_factory=FileMapping)
