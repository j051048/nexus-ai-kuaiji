"""Strict HTTP inputs; job identity and privileges are always assigned by the server."""

from pydantic import Field, StrictStr, field_validator

from ..accounting.models import InputModel


class BatchItem(InputModel):
    event_id: StrictStr = Field(min_length=1, max_length=100)
    attachment_id: StrictStr = Field(min_length=1, max_length=100)
    expected_version: int = Field(strict=True, ge=1)


class RetryRequest(InputModel):
    idempotency_key: StrictStr = Field(min_length=8, max_length=120)

    @field_validator("idempotency_key")
    @classmethod
    def meaningful_key(cls, value):
        if not value.strip():
            raise ValueError("幂等键不能为空白")
        return value


class BatchRequest(RetryRequest):
    company_id: StrictStr = Field(min_length=1, max_length=100)
    items: list[BatchItem] = Field(min_length=1, max_length=100)


class CancelRequest(InputModel):
    reason: StrictStr = Field(min_length=1, max_length=1000)

    @field_validator("reason")
    @classmethod
    def meaningful_reason(cls, value):
        if not value.strip():
            raise ValueError("取消原因不能为空白")
        return value


class ProposalRetryRequest(RetryRequest):
    reason: StrictStr = Field(min_length=3, max_length=1000)

    @field_validator("reason")
    @classmethod
    def meaningful_reason(cls, value):
        if len(value.strip()) < 3:
            raise ValueError("重试理由至少三个字符")
        return value.strip()


class AutomationPolicyRequest(InputModel):
    expected_version: int = Field(strict=True, ge=0)
    enabled: bool = Field(strict=True)
    allow_verified_supplements: bool = Field(default=True, strict=True)
    reason: StrictStr = Field(min_length=3, max_length=1000)

    @field_validator("reason")
    @classmethod
    def meaningful_reason(cls, value):
        if len(value.strip()) < 3:
            raise ValueError("配置依据至少三个字符")
        return value.strip()
