"""Explicit human work sessions; elapsed browser-open time is never work time."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, StrictInt, StrictStr, Field, model_validator


class SessionStart(BaseModel):
    model_config = ConfigDict(extra="forbid")
    book_id: StrictStr | None = Field(default=None, min_length=1, max_length=100)
    event_id: StrictStr | None = Field(default=None, min_length=1, max_length=100)
    activity: Literal["review", "correction", "supplement", "month_close", "business_entry"] = "review"
    idempotency_key: StrictStr = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def scope(self):
        if bool(self.book_id) == bool(self.event_id):
            raise ValueError("请选择一个账套或本人的业务事项")
        if self.event_id and self.activity != "business_entry":
            raise ValueError("员工事项计时仅用于本人业务录入")
        if self.book_id and self.activity == "business_entry":
            raise ValueError("业务录入计时需要本人事项")
        return self


class SessionCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["heartbeat", "pause", "resume", "complete"]
    expected_version: StrictInt = Field(ge=1)
    idempotency_key: StrictStr = Field(min_length=1, max_length=100)
