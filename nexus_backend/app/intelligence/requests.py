from pydantic import Field, StrictStr
from ..accounting.models import InputModel


class OCRRequest(InputModel):
    attachment_id: StrictStr = Field(min_length=1, max_length=100)
    expected_version: int = Field(strict=True, ge=1)
    idempotency_key: StrictStr = Field(min_length=8, max_length=120)


class ProposalRequest(InputModel):
    expected_version: int = Field(strict=True, ge=1)
    idempotency_key: StrictStr = Field(min_length=8, max_length=120)


class ApplyRequest(InputModel):
    expected_version: int = Field(strict=True, ge=1)


class ResolutionRequest(InputModel):
    reason: StrictStr = Field(min_length=1, max_length=1000)


class BankLinkRequest(ResolutionRequest):
    bank_source_ids: list[StrictStr] = Field(min_length=1, max_length=20)


class CompanyAISettings(InputModel):
    enabled: bool = Field(strict=True)
