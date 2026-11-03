from typing import Annotated

from pydantic import BaseModel, Field, StrictStr, StringConstraints

Reason = Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=1000)]


class HandoffAssign(BaseModel):
    expected_version: int = Field(strict=True, ge=1)
    assignee_id: StrictStr = Field(min_length=1, max_length=100)
    due_on: StrictStr | None = None
    reason: Reason
    expected_fingerprint: Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{64}$")] | None = None
    expected_assignment_id: StrictStr | None = Field(default=None, max_length=100)
