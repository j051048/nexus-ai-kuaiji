from pydantic import Field, StrictStr, field_validator

from ..accounting.invariants import validate_period
from ..accounting.models import InputModel


class ClosePreflight(InputModel):
    book_id: StrictStr = Field(min_length=1, max_length=100)
    period: StrictStr
    report_id: StrictStr = Field(min_length=1, max_length=100)
    idempotency_key: StrictStr = Field(min_length=8, max_length=120)
    reason: StrictStr = Field(min_length=3, max_length=1000)

    @field_validator("period")
    @classmethod
    def valid_period(cls, value):
        return validate_period(value)


class CloseCommand(InputModel):
    expected_version: int = Field(strict=True, ge=1)
    reason: StrictStr = Field(min_length=3, max_length=1000)
