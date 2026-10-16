from datetime import date
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StringConstraints,
    field_validator,
    model_validator,
)

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
UserId = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
Reason = Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=1000)]


class Command(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PlanSave(Command):
    expected_version: Annotated[StrictInt, Field(ge=0)]
    lead_user_id: UserId | None = None
    reviewer_user_id: UserId | None = None
    backup_user_id: UserId | None = None
    contact_user_id: UserId | None = None
    due_on: date
    notes: Annotated[str, StringConstraints(strip_whitespace=True, max_length=2000)] = ""
    reason: Reason

    @field_validator("due_on")
    @classmethod
    def supported_date(cls, value):
        if not 2000 <= value.year <= 2099:
            raise ValueError("服务截止日期应在 2000 至 2099 年内")
        return value

    @model_validator(mode="after")
    def independent(self):
        if self.lead_user_id and self.lead_user_id == self.reviewer_user_id:
            raise ValueError("主办会计与独立复核人必须分开")
        return self


class TemplateItem(Command):
    key: Annotated[str, StringConstraints(pattern=r"^[a-zA-Z0-9_-]{1,60}$")]
    title: Text
    description: Annotated[str, StringConstraints(strip_whitespace=True, max_length=1000)] = ""
    required: StrictBool = True
    due_day: Annotated[StrictInt, Field(ge=1, le=28)] = 10
    due_month_offset: Annotated[StrictInt, Field(ge=0, le=1)] = 1
    assignee_user_id: UserId | None = None


class TemplateCreate(Command):
    name: Text
    items: Annotated[list[TemplateItem], Field(min_length=1, max_length=30)]
    reason: Reason

    @field_validator("items")
    @classmethod
    def unique_keys(cls, items):
        if len({item.key for item in items}) != len(items):
            raise ValueError("资料项目 key 不可重复")
        return items


class Generate(Command):
    expected_version: Annotated[StrictInt, Field(ge=1)]
    reason: Reason


class MaterialSubmit(Command):
    expected_version: Annotated[StrictInt, Field(ge=1)]
    mode: Literal["provided", "no_activity", "not_applicable"]
    note: Reason
    event_ids: Annotated[list[UserId], Field(max_length=30)] = []
    attachment_ids: Annotated[list[UserId], Field(max_length=30)] = []

    @model_validator(mode="after")
    def references(self):
        if len(set(self.event_ids)) != len(self.event_ids) or len(set(self.attachment_ids)) != len(
            self.attachment_ids
        ):
            raise ValueError("业务事项不可重复")
        if (self.mode == "provided") != bool(self.event_ids or self.attachment_ids):
            raise ValueError("提交资料须上传独立原件或关联业务事项；无发生或不适用仅填写说明")
        return self


class MaterialReview(Command):
    expected_version: Annotated[StrictInt, Field(ge=1)]
    action: Literal["accept", "return"]
    reason: Reason
