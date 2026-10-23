"""Management workspace must bind service plans to the displayed client period."""

import pytest

from app.accounting.domain import AccountingError
from app.accounting.models import BookCreate
from app.cockpit.service import FirmCockpitService
from app.ledgers.service import LedgersService
from app.service_plans.models import PlanSave
from app.service_plans.service import ServicePlansService
from test_business import business  # noqa: F401


def test_cockpit_plan_owner_deadline_follow_selected_book_and_latest_version(business):
    c = business
    ledgers = LedgersService(c.db)
    future = c.ledger.create_book(
        c.manager,
        BookCreate(name="同企业次月账套", period="2026-11", company_id=c.company["id"]),
    )
    with c.db.transaction(write=True, actor=c.manager) as conn:
        ledgers.ensure_book_in_transaction(conn, c.manager, c.book)
        ledgers.ensure_book_in_transaction(conn, c.manager, future)
    plans = ServicePlansService(c.db, c.ledger, c.service)
    plans.save_plan(
        c.manager,
        c.book["id"],
        PlanSave(
            expected_version=0,
            lead_user_id=c.manager.user_id,
            due_on="2026-11-02",
            reason="十月旧版服务计划",
        ),
    )
    plans.save_plan(
        c.manager,
        c.book["id"],
        PlanSave(
            expected_version=1,
            lead_user_id="accountant",
            due_on="2026-11-10",
            reason="十月最新服务计划",
        ),
    )
    plans.save_plan(
        c.manager,
        future["id"],
        PlanSave(
            expected_version=0,
            lead_user_id=c.manager.user_id,
            due_on="2026-12-08",
            reason="十一月独立服务计划",
        ),
    )
    cockpit = FirmCockpitService(c.db, c.ledger)

    def current(period):
        return next(
            row
            for row in cockpit.snapshot(c.manager, period)["companies"]
            if row["company_id"] == c.company["id"]
        )

    october, november, latest = current("2026-10"), current("2026-11"), current("")
    assert (october["lead_user_id"], october["due_on"]) == ("accountant", "2026-11-10")
    assert (november["lead_user_id"], november["due_on"]) == (c.manager.user_id, "2026-12-08")
    assert (latest["book_id"], latest["lead_user_id"], latest["due_on"]) == (
        future["id"],
        c.manager.user_id,
        "2026-12-08",
    )
    missing = current("2026-12")
    assert missing["book_id"] is None
    assert missing["lead_user_id"] is None and not missing["due_on"]
    unrelated = next(
        row
        for row in cockpit.snapshot(c.manager, "2026-10")["companies"]
        if row["company_id"] == c.other["id"]
    )
    assert unrelated["lead_user_id"] is None and not unrelated["due_on"]


def test_cockpit_requires_current_firm_manager(business):
    c = business
    with pytest.raises(AccountingError) as caught:
        FirmCockpitService(c.db, c.ledger).snapshot(c.actors["employee"], "2026-10")
    assert caught.value.status == 403
