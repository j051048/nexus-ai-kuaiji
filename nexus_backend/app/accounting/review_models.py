from pydantic import Field, StrictStr, model_validator

from .models import InputModel


class ReviewItem(InputModel):
    book_id: StrictStr = Field(min_length=1, max_length=100)
    draft_id: StrictStr = Field(min_length=1, max_length=100)
    expected_version: int = Field(strict=True, ge=1)


class ReviewPreviewRequest(InputModel):
    items: list[ReviewItem] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def distinct(self):
        if len({item.draft_id for item in self.items}) != len(self.items):
            raise ValueError("同一凭证只能选择一次")
        self.items.sort(key=lambda item: (item.book_id, item.draft_id))
        return self


class ReviewConfirmRequest(InputModel):
    idempotency_key: StrictStr = Field(min_length=8, max_length=120, pattern=r"^[A-Za-z0-9_.:-]+$")
