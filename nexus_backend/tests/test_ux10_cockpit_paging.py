"""Bounded client reads preserve firm totals, exact scope and actual assignments."""

import pytest

from app.accounting.domain import AccountingError
from app.accounting.models import BookCreate, CompanyCreate
from app.cockpit.service import FirmCockpitService
from test_business import business  # noqa: F401


def test_page_search_and_coordination_do_not_change_firm_totals(business):
    c = business
    for name in ("00分页客户", "01分页客户", "100%_客户"):
        c.directory.create_company(c.manager, CompanyCreate(name=name))
    service = FirmCockpitService(c.db, c.ledger)
    first = service.snapshot(c.manager, "2026-10", limit=2)
    second = service.snapshot(c.manager, "2026-10", limit=2, offset=2)
    assert len(first["companies"]) == len(second["companies"]) == 2
    assert not (
        {r["company_id"] for r in first["companies"]}
        & {r["company_id"] for r in second["companies"]}
    )
    assert first["summary"] == second["summary"]
    assert first["summary"]["clients"] == 5
    assert first["coordination"] == second["coordination"]
    assert first["pagination"] == {"limit": 2, "offset": 0, "total": 5, "has_more": True}
    found = service.snapshot(c.manager, "2026-10", search="%_")
    assert [r["company_name"] for r in found["companies"]] == ["100%_客户"]
    assert found["summary"]["clients"] == 5
    assert found["scope"] == {
        "firm_id": c.manager.firm_id,
        "user_id": c.manager.user_id,
        "period": "2026-10",
        "search": "%_",
    }
    empty = service.snapshot(c.manager, "2026-10", offset=100)
    assert empty["companies"] == [] and empty["pagination"]["total"] == 5


@pytest.mark.parametrize(
    "kwargs",
    [{"limit": 101}, {"limit": True}, {"offset": -1}, {"offset": 0.5}, {"search": "a" * 201}],
)
def test_invalid_page_rejected(business, kwargs):
    with pytest.raises(AccountingError):
        FirmCockpitService(business.db, business.ledger).snapshot(business.manager, **kwargs)


def test_multiple_books_never_choose_an_arbitrary_work_target(business):
    c = business
    c.ledger.create_book(
        c.manager, BookCreate(name="同月第二账套", period="2026-10", company_id=c.company["id"])
    )
    result = FirmCockpitService(c.db, c.ledger).snapshot(c.manager, "2026-10")
    row = next(r for r in result["companies"] if r["company_id"] == c.company["id"])
    assert row["book_count"] == 2 and row["book_id"] is None
    assert row["stage"] != "unopened"
    assert result["summary"]["opened"] == 2


def test_actual_assignment_is_separate_from_service_plan_workload(business):
    from test_firm_tasks import test_firm_inbox_assignment_claim_and_client_message

    c = business
    test_firm_inbox_assignment_claim_and_client_message(c)
    result = FirmCockpitService(c.db, c.ledger).snapshot(c.manager, "2026-10")
    assigned = result["assignments"]
    assert assigned["basis"] == "active_task_assignments"
    assert assigned["total"] == 1
    assert assigned["people"][0]["user_id"] == c.actors["accountant"].user_id
    assert assigned["people"][0]["pending"] == 1
    # A detached assignment must not count as live work.
    with c.db.transaction(write=True, actor=c.manager) as conn:
        conn.execute("DELETE FROM workspace_task_projections WHERE firm_id=?", (c.manager.firm_id,))
    assert FirmCockpitService(c.db, c.ledger).snapshot(c.manager)["assignments"]["total"] == 0


def test_missing_deadline_is_not_an_overdue_task(business):
    from test_firm_tasks import test_firm_inbox_assignment_claim_and_client_message

    c = business
    test_firm_inbox_assignment_claim_and_client_message(c)
    with c.db.transaction(write=True, actor=c.manager) as conn:
        conn.execute(
            "UPDATE workspace_task_projections SET due_on='' WHERE firm_id=?", (c.manager.firm_id,)
        )
        conn.execute(
            "UPDATE firm_task_assignments SET due_on=NULL WHERE firm_id=?", (c.manager.firm_id,)
        )
    result = FirmCockpitService(c.db, c.ledger).snapshot(c.manager, "2026-10")
    assert result["summary"]["overdue"] == 0
    assert result["assignments"]["people"][0]["overdue"] == 0
