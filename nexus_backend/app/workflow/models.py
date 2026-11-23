"""Workflow write payloads. Amounts are integer cents; floats are rejected."""

from typing import Any

from pydantic import Field, StrictBool, StrictInt, StrictStr

from ..accounting.models import InputModel


class DefinitionWrite(InputModel):
    company_id: StrictStr = Field(min_length=1, max_length=120)
    process_kind: StrictStr = Field(min_length=1, max_length=40)
    nodes: list[dict[str, Any]]
    routing: dict[str, Any] = Field(default_factory=dict)
    note: StrictStr = Field(default="", max_length=200)
    publish: StrictBool = False


class DefinitionPublish(InputModel):
    note: StrictStr = Field(default="", max_length=200)


class DefinitionRollback(InputModel):
    note: StrictStr = Field(default="", max_length=200)


class InstanceStart(InputModel):
    company_id: StrictStr = Field(min_length=1, max_length=120)
    book_id: StrictStr = Field(min_length=1, max_length=120)
    subject_type: StrictStr = Field(min_length=1, max_length=40)
    subject_id: StrictStr = Field(min_length=1, max_length=120)
    amount_cents: StrictInt = Field(ge=0)
    process_kind: StrictStr = Field(default="", max_length=40)
    definition_id: StrictStr = Field(default="", max_length=120)


class InstanceAction(InputModel):
    action: StrictStr = Field(min_length=1, max_length=40)
    reason: StrictStr = Field(default="", max_length=500)
    metadata: dict[str, Any] = Field(default_factory=dict)


class EscalationSweep(InputModel):
    company_id: StrictStr = Field(min_length=1, max_length=120)
    now: StrictStr = Field(default="", max_length=40)
