"""Commands for delivery configuration and reminder tasks."""

from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, StringConstraints

Identifier = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
Reason = Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=1000)]


class DeliveryGrantRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    period: Annotated[str, StringConstraints(pattern=r"^20[0-9]{2}-(0[1-9]|1[0-2])$")]
    recipient_user_id: Identifier
    reason: Reason
    expected_source_fingerprint: (
        Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{64}$")] | None
    ) = None


class DeliveryRevokeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    period: Annotated[str, StringConstraints(pattern=r"^20[0-9]{2}-(0[1-9]|1[0-2])$")]
    reason: Reason


class DeliveryTaskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    period: Annotated[str, StringConstraints(pattern=r"^20[0-9]{2}-(0[1-9]|1[0-2])$")]
    kind: Literal["signature_reminder", "missing_contact_followup", "signature_overdue"]
    reason: Reason
