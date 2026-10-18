from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
Note = Annotated[str, StringConstraints(strip_whitespace=True, max_length=1000)]


class TaskRef(BaseModel):
    kind: Text
    ref: Text


class AssignTasks(BaseModel):
    tasks: list[TaskRef] = Field(min_length=1, max_length=100)
    assignee_id: Annotated[str, StringConstraints(min_length=1, max_length=100)]
    due_on: str | None = Field(
        default=None, pattern=r"^20\d{2}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$"
    )
    note: Note = ""


class ClaimTask(TaskRef):
    note: Note = ""


class RemindTasks(BaseModel):
    tasks: list[TaskRef] = Field(min_length=1, max_length=100)
    message: Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=1000)]
