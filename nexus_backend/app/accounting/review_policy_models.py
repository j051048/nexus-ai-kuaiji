"""Human review intent. Identities always come from the authenticated actor."""

from typing import Literal

from pydantic import Field, StrictStr

from .models import InputModel


class ReviewPolicyChange(InputModel):
    expected_version: int = Field(strict=True, ge=0)
    mode: Literal["independent", "single_operator"]
    reason: StrictStr = Field(min_length=3, max_length=1000)


class PreparationClaim(InputModel):
    expected_version: int = Field(strict=True, ge=1)
    reason: StrictStr = Field(min_length=3, max_length=1000)


class ManualReviewApproval(InputModel):
    expected_version: int = Field(strict=True, ge=1)
    expected_policy_version: int = Field(strict=True, ge=0)
    reason: StrictStr = Field(min_length=3, max_length=1000)


class ManualReviewPost(InputModel):
    expected_version: int = Field(strict=True, ge=1)
    command_key: StrictStr = Field(min_length=8, max_length=120)


class ManualReviewWithdrawal(PreparationClaim):
    """Withdraw an unreviewed version without deleting its original content."""
