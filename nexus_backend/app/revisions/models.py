from typing import Literal
from pydantic import Field, StrictStr, field_validator
from app.accounting.models import InputModel
from app.business.models import BusinessPayload


class RevisionSubmit(InputModel):
    expected_event_version: int = Field(strict=True, ge=1)
    expected_fact_version: int = Field(strict=True, ge=1)
    expected_draft_version: int = Field(strict=True, ge=1)
    target_book_id: StrictStr = Field(min_length=1, max_length=100)
    payload: BusinessPayload
    reason: StrictStr = Field(min_length=3, max_length=1000)
    idempotency_key: StrictStr = Field(min_length=8, max_length=120)

    @field_validator("reason", "idempotency_key")
    @classmethod
    def trimmed(cls, value):
        if value != value.strip():
            raise ValueError("修订依据与操作编号不得包含首尾空格")
        return value


class RevisionReview(InputModel):
    expected_version: int = Field(strict=True, ge=1)
    decision: Literal["accept", "reject"]
    reason: StrictStr = Field(min_length=3, max_length=1000)
    idempotency_key: StrictStr = Field(min_length=8, max_length=120)

    @field_validator("reason", "idempotency_key")
    @classmethod
    def trimmed(cls, value):
        if value != value.strip():
            raise ValueError("复核依据与操作编号不得包含首尾空格")
        return value
