from datetime import date
from typing import Literal
from pydantic import Field, StrictStr, field_validator
from app.accounting.models import InputModel

CaseState = Literal["open", "awaiting_info", "response_submitted", "in_review", "closed"]


class CaseCreate(InputModel):
    event_id: StrictStr | None = Field(default=None, min_length=1, max_length=100)
    book_id: StrictStr | None = Field(default=None, min_length=1, max_length=100)
    draft_id: StrictStr | None = Field(default=None, min_length=1, max_length=100)
    kind: Literal["exception", "supplement"] = "exception"
    title: StrictStr = Field(min_length=1, max_length=200)
    reason: StrictStr = Field(min_length=1, max_length=2000)
    priority: Literal["normal", "high", "urgent"] = "normal"
    due_on: StrictStr | None = None
    required_fields: list[StrictStr] = Field(default_factory=list, max_length=20)
    attachment_required: bool = False
    idempotency_key: StrictStr = Field(min_length=8, max_length=120)

    @field_validator("title", "reason")
    @classmethod
    def not_blank(cls, value):
        if not value.strip() or "\x00" in value:
            raise ValueError("标题或处理依据不能为空")
        return value.strip()

    @field_validator("required_fields")
    @classmethod
    def fields(cls, value):
        if any(not item.strip() or len(item) > 100 or "\x00" in item for item in value):
            raise ValueError("补件字段须为 1–100 个字符")
        return list(dict.fromkeys(item.strip() for item in value))

    @field_validator("due_on")
    @classmethod
    def deadline(cls, value):
        if value is not None:
            parsed = date.fromisoformat(value)
            if parsed.isoformat() != value or not 2000 <= parsed.year <= 2099:
                raise ValueError("期限必须为 YYYY-MM-DD")
        return value


class CaseCommand(InputModel):
    expected_version: int = Field(strict=True, ge=1)
    idempotency_key: StrictStr = Field(min_length=8, max_length=120)
    reason: StrictStr = Field(min_length=1, max_length=2000)

    @field_validator("reason")
    @classmethod
    def reason_text(cls, value):
        if not value.strip() or "\x00" in value:
            raise ValueError("请填写处理依据")
        return value.strip()


class CaseAssign(CaseCommand):
    assignee_id: StrictStr = Field(min_length=1, max_length=100)
    due_on: StrictStr | None = None
    priority: Literal["normal", "high", "urgent"] = "normal"
    _deadline = field_validator("due_on")(CaseCreate.deadline.__func__)


class CaseComment(CaseCommand):
    audience: Literal["internal", "requester"] = "internal"


class CaseRespond(CaseCommand):
    fields: dict[StrictStr, StrictStr] = Field(default_factory=dict)

    @field_validator("fields")
    @classmethod
    def bounded_fields(cls, value):
        if len(value) > 20 or any(
            not k.strip()
            or len(k) > 100
            or not v.strip()
            or len(v) > 2000
            or "\x00" in k
            or "\x00" in v
            for k, v in value.items()
        ):
            raise ValueError("补件字段最多 20 项，每项须填写有效内容")
        return value


class CaseReviewResponse(CaseCommand):
    decision: Literal["accept", "return"]
    supersedes_attachment_ids: list[StrictStr] = Field(default_factory=list, max_length=10)

    @field_validator("supersedes_attachment_ids")
    @classmethod
    def valid_attachment_ids(cls, value):
        if any(not item or len(item) > 100 for item in value) or len(value) != len(set(value)):
            raise ValueError("替代原件须为互不重复的有效附件 ID")
        return value
