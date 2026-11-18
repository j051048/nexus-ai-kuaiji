from pydantic import Field, StrictStr, field_validator

from ..accounting.invariants import validate_period
from ..accounting.models import InputModel


class ReportGenerate(InputModel):
    book_id: StrictStr = Field(min_length=1, max_length=100)
    period: StrictStr

    @field_validator("period")
    @classmethod
    def valid_period(cls, value):
        return validate_period(value)


class ReportCommand(InputModel):
    expected_version: int = Field(strict=True, ge=1)
    reason: StrictStr = Field(min_length=3, max_length=1000)


class ReportNote(InputModel):
    note_key: StrictStr = Field(min_length=1, max_length=80)
    text: StrictStr = Field(min_length=1, max_length=4000)
    reason: StrictStr = Field(min_length=3, max_length=1000)


class ReportReconcile(InputModel):
    reference_name: StrictStr = Field(min_length=1, max_length=200)
    csv_text: StrictStr = Field(min_length=1, max_length=512_000)


class ReportDifferenceNote(InputModel):
    reference_sha256: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    line_key: StrictStr = Field(pattern=r"^(balance_sheet|profit_statement|cash_flow)\.[a-z_]+$")
    explanation_type: StrictStr = Field(pattern=r"^(rounding|reclassification|basis|unknown)$")
    note: StrictStr = Field(min_length=1, max_length=2000)
    reason: StrictStr = Field(min_length=3, max_length=1000)
