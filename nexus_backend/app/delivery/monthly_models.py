"""Commands for reviewed monthly packages and independent customer receipts."""

from typing import Annotated
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StringConstraints

Identifier = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
Reason = Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=1000)]


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
