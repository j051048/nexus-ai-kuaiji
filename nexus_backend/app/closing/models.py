from pydantic import Field, StrictBool, StrictStr, field_validator
from ..accounting.models import InputModel


class PreflightRequest(InputModel):
    idempotency_key: StrictStr = Field(min_length=8, max_length=120)


class SnapshotReview(InputModel):
    reason: StrictStr = Field(min_length=3, max_length=1000)
    materials_confirmed: StrictBool

    @field_validator("materials_confirmed")
    @classmethod
    def explicit_coverage(cls, value):
        if value is not True:
            raise ValueError("关期前须明确确认本期资料及银行流水完整性")
        return value


class CloseRequest(InputModel):
    snapshot_id: StrictStr = Field(min_length=1, max_length=100)
    reason: StrictStr = Field(min_length=3, max_length=1000)
