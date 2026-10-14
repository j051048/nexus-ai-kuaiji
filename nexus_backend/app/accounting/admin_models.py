"""Explicit, versioned commands for organization administration."""

from typing import Literal

from pydantic import Field, StrictStr

from .models import InputModel


class AdminCompanyUpdate(InputModel):
    name: StrictStr = Field(min_length=1, max_length=120)
    tax_id: StrictStr = Field(default="", max_length=30)
    reason: StrictStr = Field(min_length=1, max_length=1000)
    expected_version: int = Field(strict=True, ge=1)


class AdminCompanyStatus(InputModel):
    status: Literal["active", "suspended", "archived"]
    reason: StrictStr = Field(min_length=1, max_length=1000)
    expected_version: int = Field(strict=True, ge=1)
