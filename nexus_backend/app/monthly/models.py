from typing import Literal

from pydantic import ConfigDict, Field, StrictStr, field_validator, model_validator

from ..accounting.invariants import validate_period
from ..accounting.models import DraftLine, InputModel, cents_to_money, money_to_cents


def signed_cents(value):
    return -money_to_cents(value[1:]) if value.startswith("-") else money_to_cents(value)


def signed_money(cents):
    return ("-" if cents < 0 else "") + cents_to_money(abs(cents))


class BalanceLine(InputModel):
    account_code: StrictStr = Field(min_length=1, max_length=30)
    auxiliary_values: dict[
        Literal["department", "counterparty", "employee", "project"], StrictStr
    ] = Field(default_factory=dict, max_length=4)
    balance: StrictStr

    @field_validator("balance")
    @classmethod
    def amount(cls, value):
        return signed_money(signed_cents(value))


class BaselineCreate(InputModel):
    kind: Literal["opening", "closing"]
    external_reference: StrictStr = Field(min_length=1, max_length=500)
    evidence_ref: StrictStr = Field(min_length=1, max_length=500)
    reason: StrictStr = Field(min_length=3, max_length=1000)
    source_kind: Literal["manual", "legacy_cutover", "bridge_import"] = "manual"
    source_id: StrictStr | None = Field(default=None, min_length=1, max_length=100)
    lines: list[BalanceLine] = Field(default_factory=list, max_length=1000)
    idempotency_key: StrictStr = Field(min_length=8, max_length=120)

    @model_validator(mode="after")
    def source(self):
        if self.source_kind == "manual" and (not self.lines or self.source_id):
            raise ValueError("手工外部余额须逐项提供真实金额，不能指定迁移记录")
        if self.source_kind != "manual" and (not self.source_id or self.lines):
            raise ValueError("复用原始余额须指定已确认来源，由服务器读取，不能改写金额")
        identities = [
            (x.account_code, tuple(sorted(x.auxiliary_values.items()))) for x in self.lines
        ]
        if len(identities) != len(set(identities)):
            raise ValueError("同一科目和辅助项余额不可重复")
        return self


class MonthlyReason(InputModel):
    reason: StrictStr = Field(min_length=3, max_length=1000)


class BaselineImport(InputModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)
    kind: Literal["opening", "closing"]
    format: Literal["json", "csv"]
    filename: StrictStr = Field(min_length=1, max_length=180)
    content: StrictStr = Field(min_length=1, max_length=2_000_000)
    external_reference: StrictStr = Field(min_length=1, max_length=500)
    evidence_ref: StrictStr = Field(min_length=1, max_length=500)
    reason: StrictStr = Field(min_length=3, max_length=1000)
    idempotency_key: StrictStr = Field(min_length=8, max_length=120)

    @field_validator("filename")
    @classmethod
    def filename_safe(cls, value):
        if any(x in value for x in ("/", "\\", "\r", "\n", "\x00")):
            raise ValueError("原件文件名须为单一文件名")
        return value

    @field_validator("content")
    @classmethod
    def preserve_utf8(cls, value):
        if "\ufffd" in value or len(value.encode("utf-8")) > 2_000_000:
            raise ValueError("仅支持不超过 2 MB 的有效 UTF-8 余额文本")
        return value


class CheckCreate(InputModel):
    opening_id: StrictStr = Field(min_length=1, max_length=100)
    closing_id: StrictStr = Field(min_length=1, max_length=100)
    idempotency_key: StrictStr = Field(min_length=8, max_length=120)


class PlanCreate(InputModel):
    plan_id: StrictStr | None = Field(default=None, min_length=1, max_length=100)
    expected_version: int = Field(default=0, strict=True, ge=0)
    name: StrictStr = Field(min_length=1, max_length=120)
    start_period: StrictStr
    end_period: StrictStr | None = None
    day: int = Field(default=1, strict=True, ge=1, le=28)
    description: StrictStr = Field(min_length=1, max_length=500)
    evidence_ref: StrictStr = Field(min_length=1, max_length=500)
    reason: StrictStr = Field(min_length=3, max_length=1000)
    lines: list[DraftLine] = Field(min_length=2, max_length=100)
    idempotency_key: StrictStr = Field(min_length=8, max_length=120)

    @field_validator("start_period", "end_period")
    @classmethod
    def period(cls, value):
        return None if value is None else validate_period(value)

    @model_validator(mode="after")
    def balanced(self):
        debit = sum(money_to_cents(x.debit) for x in self.lines)
        credit = sum(money_to_cents(x.credit) for x in self.lines)
        if (
            not debit
            or debit != credit
            or any(
                bool(money_to_cents(x.debit)) == bool(money_to_cents(x.credit)) for x in self.lines
            )
        ):
            raise ValueError("固定事项金额须明确、逐行单边非零且借贷平衡")
        cents_to_money(debit)
        if self.end_period and self.end_period < self.start_period:
            raise ValueError("结束期间不能早于开始期间")
        if bool(self.plan_id) != bool(self.expected_version):
            raise ValueError("修改计划须同时提供计划 ID 和当前版本")
        return self


class PlanApproval(MonthlyReason):
    version: int = Field(strict=True, ge=1)


class PlanGenerate(InputModel):
    version: int = Field(strict=True, ge=1)
    idempotency_key: StrictStr = Field(min_length=8, max_length=120)
