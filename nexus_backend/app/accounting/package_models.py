from pydantic import Field, StrictStr, model_validator

from .models import InputModel
from .review_models import ReviewPreviewRequest


class PackagePrepare(ReviewPreviewRequest):
    idempotency_key: StrictStr = Field(min_length=8, max_length=120, pattern=r"^[A-Za-z0-9_.:-]+$")


class PackageSelect(InputModel):
    draft_ids: list[StrictStr] = Field(min_length=1, max_length=100)
    idempotency_key: StrictStr = Field(min_length=8, max_length=120, pattern=r"^[A-Za-z0-9_.:-]+$")

    @model_validator(mode="after")
    def distinct(self):
        if any(not 1 <= len(value) <= 100 for value in self.draft_ids) or len(set(self.draft_ids)) != len(self.draft_ids):
            raise ValueError("凭证编号必须有效且不重复")
        self.draft_ids.sort()
        return self
