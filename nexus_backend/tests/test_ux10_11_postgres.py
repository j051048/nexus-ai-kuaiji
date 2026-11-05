"""Restricted PostgreSQL counterparts for bounded cockpit and assignment authority."""

import pytest

from app.accounting.domain import AccountingError
from app.accounting.models import ImportRequest, SourceRow
from app.cockpit.service import FirmCockpitService
from app.firm_tasks.models import AssignTasks, ClaimTask, TaskRef
from app.firm_tasks.service import FirmTasksService
from test_business_postgres import business_postgres, business_case  # noqa: F401


def test_pg_cockpit_page_and_current_role(business_case):
    c = business_case
    service = FirmCockpitService(c.db, c.service)
    first = service.snapshot(c.owner, "2026-10", limit=1)
    second = service.snapshot(c.owner, "2026-10", limit=1, offset=1)
    assert first["summary"]["clients"] == second["summary"]["clients"] == 2
    assert first["companies"][0]["company_id"] != second["companies"][0]["company_id"]
    assert first["coordination"] == second["coordination"]
    assert first["scope"]["firm_id"] == c.owner.firm_id
    for actor in (c.employee, c.reviewer, c.viewer):
        with pytest.raises(AccountingError):
            service.snapshot(actor, "2026-10")


def test_pg_task_overlay_never_grants_book_access(business_case):
    c = business_case
    c.service.import_sources(
        c.owner,
        c.book,
        ImportRequest(
            source_name="本地虚构任务",
            rows=[
                SourceRow(
                    external_id="ux11",
                    date="2026-10-04",
                    description="办公用品",
                    counterparty="虚构商户",
                    amount="12.00",
                    direction="expense",
                    evidence_ref="虚构原件",
                )
            ],
        ),
    )
    c.service.suggestions(c.owner, c.book)
    draft = c.service.get_book(c.owner, c.book)["drafts"][0]
    service = FirmTasksService(c.db, c.service)
    ref = TaskRef(kind="review", ref=draft["id"])
    service.assign(c.owner, AssignTasks(tasks=[ref], assignee_id=c.reviewer.user_id))
    inbox = service.inbox(c.reviewer)
    row = next(r for r in inbox["items"] if r["ref"] == draft["id"])
    assert row["draft_id"] == draft["id"] and row["action_label"] == "同屏复核"
    assert row["permissions"]["assign"] is False
    assert service.inbox(c.employee)["items"] == []
    with c.db.transaction(write=True, actor=c.owner) as conn:
        conn.execute(
            "DELETE FROM book_access WHERE book_id=? AND user_id=?", (c.book, c.reviewer.user_id)
        )
    assert service.inbox(c.reviewer)["items"] == []
    with pytest.raises(AccountingError) as error:
        service.claim(c.reviewer, ClaimTask(**ref.model_dump()))
    assert error.value.status == 404
