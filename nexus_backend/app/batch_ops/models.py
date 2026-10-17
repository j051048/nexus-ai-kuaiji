from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints

from ..monthly.batch import BatchOpen

IdempotencyKey = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=8, max_length=100)
]


class CreateMonthlyOpenBatch(BatchOpen):
    idempotency_key: IdempotencyKey


class RetryBatchRequest(BaseModel):
    idempotency_key: IdempotencyKey
    reason: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=3, max_length=1000)
    ] = Field(default="重试失败的客户开期")
