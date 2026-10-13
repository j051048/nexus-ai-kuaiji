from pydantic import Field, StrictStr, field_validator
from app.accounting.models import InputModel


class SameTransaction(InputModel):
    expected_version: int = Field(strict=True, ge=1)
    source_fingerprint: StrictStr = Field(pattern=r"^[a-f0-9]{64}$")
    canonical_fingerprint: StrictStr = Field(pattern=r"^[a-f0-9]{64}$")
    idempotency_key: StrictStr = Field(min_length=8, max_length=120)
    reason: StrictStr = Field(min_length=3, max_length=1000)

    @field_validator("reason", "idempotency_key")
    @classmethod
    def trimmed(cls, value):
        if value != value.strip() or not value.strip():
            raise ValueError("请填写无首尾空格的核验依据和操作编号")
        return value
