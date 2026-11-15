"""Commands for reviewed monthly packages and independent customer receipts."""

from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StringConstraints

Identifier = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
Reason = Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=1000)]
EvidenceText = Annotated[str, StringConstraints(min_length=1, max_length=1_000_000)]


class MonthlyPackageCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    idempotency_key: Identifier
    report_id: Identifier
    expected_report_version: Annotated[StrictInt, Field(ge=1)]
    reason: Reason


class MonthlyPackageAcknowledge(BaseModel):
    model_config = ConfigDict(extra="forbid")
    manifest_sha256: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
    reason: Reason


class ExternalEvidenceCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    report_id: Identifier
    expected_report_version: Annotated[StrictInt, Field(ge=1)]
    source_kind: Literal["tplus_report", "import_result"]
    source_name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=200)]
    source_version: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
    content: EvidenceText
    note: Reason


class ExternalEvidenceReview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["verified", "rejected"]
    note: Reason
