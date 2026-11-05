"""Early preparation is advice, never permission to hand off a stale version."""

import pytest

from app.accounting.domain import AccountingError
from app.business.models import HandoffRequest
from test_business import business, approved
from period_fixtures import restore_period_state


def test_employee_checks_period_without_ledger_disclosure(business):
    c = business
    ready = c.service.preparation(c.actors["employee"], c.company["id"], "2026-10-03")
    assert ready["status"] == "ready"
    assert ready["period"] == "2026-10"
    assert set(ready) == {"company_id", "period", "status", "owner_role", "next_action", "advisory"}
    assert ready["advisory"] is True
    assert c.book["id"] not in str(ready)
    missing = c.service.preparation(c.actors["employee"], c.company["id"], "2026-11-03")
    assert missing["status"] == "period_missing"
    assert "2026-11" in missing["next_action"]
    with pytest.raises(AccountingError) as caught:
        c.service.preparation(c.actors["employee"], c.other["id"], "2026-10-03")
    assert caught.value.status == 404


def test_preparation_distinguishes_locked_period_and_missing_assignment(business):
    c = business
    assert (
        c.service.preparation(c.actors["finance"], c.company["id"], "2026-10-03")["status"]
        == "ready"
    )
    c.directory.revoke_book_member(c.manager, c.book["id"], c.actors["finance"].user_id)
    assert (
        c.service.preparation(c.actors["finance"], c.company["id"], "2026-10-03")["status"]
        == "assignment_required"
    )
    restore_period_state(c.db, c.book["id"], "closed")
    assert (
        c.service.preparation(c.actors["employee"], c.company["id"], "2026-10-03")["status"]
        == "period_locked"
    )


def test_preparation_cannot_authorize_handoff_after_permission_change(business):
    c = business
    event = approved(c)
    assert (
        c.service.preparation(c.actors["finance"], c.company["id"], "2026-10-03")["status"]
        == "ready"
    )
    c.directory.revoke_book_member(c.manager, c.book["id"], c.actors["finance"].user_id)
    with pytest.raises(AccountingError):
        c.service.handoff(
            c.actors["finance"],
            event["id"],
            HandoffRequest(
                expected_version=event["version"],
                book_id=c.book["id"],
                idempotency_key="stale-preparation",
            ),
        )
    assert c.service.get_event(c.actors["finance"], event["id"])["status"] == "finance_approved"


@pytest.mark.parametrize("date", ["2026-13-01", "2026-02-30", "2026-10", "2026-1-1", ""])
def test_preparation_rejects_invalid_business_date(business, date):
    with pytest.raises(AccountingError) as caught:
        business.service.preparation(business.actors["employee"], business.company["id"], date)
    assert caught.value.status == 422
