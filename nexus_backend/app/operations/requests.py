"""Operations inputs keep money exact and never accept client authority fields."""

import re

from pydantic import Field, StrictStr, field_validator, model_validator

from ..accounting.models import InputModel, money_to_cents


def exact_money(value):
    if not re.fullmatch(r"(?:0|[1-9][0-9]{0,11})\.[0-9]{2}", value):
        raise ValueError("金额须为非负、精确两位小数的字符串")
    money_to_cents(value)
    return value


class PolicyRequest(InputModel):
    expected_version: int = Field(strict=True, ge=0)
    quota_enabled: bool = Field(default=False, strict=True)
    monthly_limit: StrictStr = "0.00"
    ocr_reserve: StrictStr = "0.00"
    proposal_reserve: StrictStr = "0.00"
    chat_reserve: StrictStr | None = None
    failure_threshold: int = Field(default=3, strict=True, ge=1, le=1000)
    failure_window_minutes: int = Field(default=60, strict=True, ge=1, le=10080)
    backlog_threshold: int = Field(default=10, strict=True, ge=1, le=1000)
    backlog_age_minutes: int = Field(default=60, strict=True, ge=1, le=10080)
    queue_wait_minutes: int = Field(default=10, strict=True, ge=1, le=10080)
    worker_stale_seconds: int = Field(default=90, strict=True, ge=30, le=3600)

    @field_validator("monthly_limit", "ocr_reserve", "proposal_reserve", "chat_reserve")
    @classmethod
    def valid_money(cls, value):
        return exact_money(value) if value is not None else value

    @model_validator(mode="after")
    def enabled_budget_must_be_positive(self):
        if self.quota_enabled and any(
            money_to_cents(getattr(self, name)) <= 0
            for name in ("monthly_limit", "ocr_reserve", "proposal_reserve")
        ):
            raise ValueError("启用额度时，月额度及两类请求预留必须大于零")
        return self


class AcknowledgeRequest(InputModel):
    reason: StrictStr = Field(min_length=1, max_length=1000)
    expected_version: int = Field(strict=True, ge=1)


class ReconcileRequest(AcknowledgeRequest):
    confirmed_cost: StrictStr

    @field_validator("confirmed_cost")
    @classmethod
    def valid_money(cls, value):
        return exact_money(value) if value is not None else value
