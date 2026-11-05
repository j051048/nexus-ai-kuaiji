"""Assignment overlays reuse live workspace authority and never create access."""

import pytest

from app.accounting.domain import AccountingError
from app.firm_tasks.models import AssignTasks, ClaimTask, TaskRef
from app.firm_tasks.service import FirmTasksService
from test_business import business  # noqa: F401
from test_stage12_workspace import bank


def test_unbuilt_projection_still_has_current_tasks_and_exact_target(business):
    c = business
    draft = bank(c)
    service = FirmTasksService(c.db, c.ledger)
    inbox = service.inbox(c.manager)
    row = next(r for r in inbox["items"] if r["ref"] == draft["id"])
    assert row["kind"] == "review" and row["book_id"] == c.book["id"]
    assert row["action_label"] == "同屏复核"
    assert row["responsible"]
    assert row["permissions"]["assign"] is True
    assert inbox["pagination"]["total"] >= 1
    assert service.inbox(c.actors["employee"])["items"] == []


def test_assignment_cannot_resurrect_revoked_task_access(business):
    c = business
    draft = bank(c)
    service = FirmTasksService(c.db, c.ledger)
    service.assign(
        c.manager,
        AssignTasks(
            tasks=[TaskRef(kind="review", ref=draft["id"])],
            assignee_id="accountant",
            note="虚构接单",
        ),
    )
    before = service.inbox(c.actors["accountant"])
    assert any(r["ref"] == draft["id"] for r in before["items"])
    with c.db.transaction(write=True, actor=c.manager) as conn:
        conn.execute(
            "DELETE FROM book_access WHERE user_id=? AND book_id=?", ("accountant", c.book["id"])
        )
    assert service.inbox(c.actors["accountant"])["items"] == []
    with pytest.raises(AccountingError) as error:
        service.claim(c.actors["accountant"], ClaimTask(kind="review", ref=draft["id"]))
    assert error.value.status == 404


def test_task_page_counts_are_not_silently_capped(business):
    c = business
    bank(c)
    service = FirmTasksService(c.db, c.ledger)
    first = service.inbox(c.manager, limit=1)
    empty = service.inbox(c.manager, limit=1, offset=999)
    assert empty["items"] == []
    assert empty["counts"] == first["counts"]
    assert empty["pagination"]["total"] == first["pagination"]["total"]
