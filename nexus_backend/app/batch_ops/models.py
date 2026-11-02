from typing import Annotated

from pydantic import BaseModel, Field, StrictStr, StringConstraints

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


class BatchItemReason(BaseModel):
    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=1000)]


class ReviewExportPreview(BaseModel):
    company_ids: list[StrictStr] = Field(min_length=1, max_length=200)
    period: StrictStr = Field(pattern=r"^20\d{2}-(0[1-9]|1[0-2])$")


class CreateReviewExport(ReviewExportPreview):
    confirmed_manifest: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    idempotency_key: StrictStr = Field(min_length=8, max_length=120)
