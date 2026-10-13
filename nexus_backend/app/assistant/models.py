from typing import Literal
from pydantic import Field, StrictInt, StrictStr, field_validator, model_validator
from app.accounting.models import InputModel
from app.intelligence.models import OutputModel


class ConversationCreate(InputModel):
    company_id: StrictStr = Field(min_length=1, max_length=100)
    book_ids: list[StrictStr] = Field(default_factory=list, max_length=12)
    event_ids: list[StrictStr] = Field(default_factory=list, max_length=20)
    draft_id: StrictStr | None = Field(default=None, min_length=1, max_length=100)
    title: StrictStr = Field(default="会计助手", min_length=1, max_length=120)
    idempotency_key: StrictStr = Field(min_length=1, max_length=100)

    @field_validator("book_ids", "event_ids")
    @classmethod
    def bounded_ids(cls, value):
        if len(set(value)) != len(value) or any(not item.strip() or len(item) > 100 for item in value):
            raise ValueError("范围 ID 必须唯一，且每项为 1–100 字")
        return sorted(value)

    @model_validator(mode="after")
    def draft_scope(self):
        if self.draft_id and len(self.book_ids) != 1:
            raise ValueError("当前凭证必须绑定一个明确账套")
        return self


class TurnCreate(InputModel):
    question: StrictStr = Field(min_length=1, max_length=4000)
    expected_scope_version: StrictInt = Field(ge=1, le=1)
    idempotency_key: StrictStr = Field(min_length=1, max_length=100)


class TurnCancel(InputModel):
    reason: StrictStr = Field(min_length=1, max_length=1000)


class AssistantProposal(OutputModel):
    kind: Literal["ocr_batch", "proposal_batch", "bank_drafts"]
    reason: StrictStr = Field(min_length=1, max_length=1000)


class AssistantReply(OutputModel):
    answer: StrictStr = Field(min_length=1, max_length=12000)
    citation_ids: list[StrictStr] = Field(max_length=100)
    proposals: list[AssistantProposal] = Field(max_length=3)

    @field_validator("citation_ids")
    @classmethod
    def citations(cls, value):
        if len(set(value)) != len(value) or any(not item or len(item) > 200 for item in value):
            raise ValueError("证据引用 ID 无效")
        return value
