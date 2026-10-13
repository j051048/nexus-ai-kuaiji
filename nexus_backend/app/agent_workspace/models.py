from pydantic import Field, StrictStr

from app.accounting.models import InputModel


class ExplanationRequest(InputModel):
    question: StrictStr = Field(min_length=1, max_length=1000)
