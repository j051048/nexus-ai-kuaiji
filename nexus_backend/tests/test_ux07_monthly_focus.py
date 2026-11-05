"""Monthly work filters use current grants and filter before pagination."""

import pytest

from app.accounting.directory import DirectoryService
from app.accounting.domain import AccountingError
from test_business import business  # noqa: F401
from test_business_postgres import business_case, business_postgres  # noqa: F401
from test_stage17_monthly_workbench import imported, month_case  # noqa: F401
from test_stage25_service_plans import generated, plan, plans  # noqa: F401


def test_mine_is_actual_service_assignment_not_book_access(plans):
    c = plans
    assert c.monthly.list(c.reviewer, "2026-10", focus="mine")["rows"] == []
    c.plans.save_plan(c.owner, c.book, plan(c))
    result = c.monthly.list(c.reviewer, "2026-10", focus="mine", limit=1)
    assert result["pagination"]["total"] == 1
    assert result["rows"][0]["book_id"] == c.book
    assert result["rows"][0]["work"]["due_on"] == "2026-11-10"
    assert c.monthly.list(c.reviewer, "2026-10", focus="mine", offset=1)["rows"] == []
    assert c.monthly.list(c.reviewer, "2026-10", focus="mine", offset=1)["pagination"]["total"] == 1
    DirectoryService(c.db).revoke_book_member(c.owner, c.book, c.reviewer.user_id)
    assert c.monthly.list(c.reviewer, "2026-10", focus="mine")["pagination"]["total"] == 0


def test_material_focus_and_next_action_are_from_current_records(plans):
    c = plans
    assert c.monthly.list(c.owner, "2026-10", focus="materials")["rows"] == []
    c.plans.save_plan(c.owner, c.book, plan(c))
    generated(c)
    rows = c.monthly.list(c.reviewer, "2026-10", focus="materials")["rows"]
    assert [r["book_id"] for r in rows] == [c.book]
    assert rows[0]["work"]["target"] == "service"
    assert "资料" in rows[0]["work"]["reason"]
    assert rows[0]["work"]["owner"]
    assert c.monthly.list(c.employee, "2026-10", focus="materials")["rows"] == []


def test_review_focus_is_not_export_receipt_or_client_ack(plans):
    c = plans
    imported(c)
    assert [
        r["book_id"] for r in c.monthly.list(c.reviewer, "2026-10", focus="review")["rows"]
    ] == [c.book]
    assert c.monthly.list(c.reviewer, "2026-10", focus="receipt")["rows"] == []
    assert c.monthly.list(c.employee, "2026-10", focus="review")["rows"] == []


def test_due_uses_service_deadline_not_selected_month(plans):
    c = plans
    c.plans.save_plan(c.owner, c.book, plan(c, due_on="2026-10-10"))
    result = c.monthly.list(c.owner, "2026-10", focus="due")
    assert c.book in [r["book_id"] for r in result["rows"]]
    assert c.monthly.list(c.owner, "2026-11", focus="due")["rows"] == []


def test_unknown_focus_is_rejected_not_silently_all(plans):
    with pytest.raises(AccountingError):
        plans.monthly.list(plans.owner, "2026-10", focus="all OR 1=1")
