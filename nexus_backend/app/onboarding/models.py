from typing import Annotated

from pydantic import Field, StringConstraints

from ..accounting.models import InputModel

Reason = Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=1000)]
Period = Annotated[str, StringConstraints(pattern=r"^20\d{2}-(0[1-9]|1[0-2])$")]


class StartOnboarding(InputModel):
    company_id: Annotated[str, StringConstraints(min_length=1, max_length=100)]
    period: Period
    source_company_id: Annotated[str, StringConstraints(max_length=100)] = ""
    reason: Reason


class CopyCatalogTemplates(InputModel):
    source_company_id: Annotated[str, StringConstraints(min_length=1, max_length=100)]
    target_company_id: Annotated[str, StringConstraints(min_length=1, max_length=100)]
    period: Period
    reason: Reason

    @classmethod
    def distinct_companies(cls, value):
        return value
