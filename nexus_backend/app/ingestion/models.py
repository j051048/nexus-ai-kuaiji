from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]


class ConnectionCreate(BaseModel):
    company_id: Annotated[str, StringConstraints(min_length=1, max_length=100)]
    kind: Literal["csv", "xlsx", "email", "sftp", "webhook", "bank", "invoice", "erp"]
    name: Text
    config: dict = Field(default_factory=dict)
