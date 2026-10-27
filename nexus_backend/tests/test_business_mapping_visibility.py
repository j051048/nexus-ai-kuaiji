"""The existing mapping filter must explain omission without revealing its IDs."""

import pytest

from app.accounting.domain import AccountingError
from test_business import approved, business, create, handoff


def test_handed_off_employee_receives_restricted_projection_not_missing(business):
    case = business
    event = approved(case)
    receipt = handoff(case, event)
    own = case.service.get_event(case.actors["employee"], event["id"])
    assert own["status"] == "handed_off"
    assert own["mapping"] is None
    assert own.get("mapping_visibility") == "restricted"
    assert not own["permissions"]["handoff"]
    for key in ("draft_id", "book_id", "source_id"):
        assert receipt[key] not in str(own)
    listed = case.service.list_events(case.actors["employee"])["events"]
    assert next(item for item in listed if item["id"] == event["id"])["mapping_visibility"] == "restricted"
    for persona in ("finance", "accountant"):
        view = case.service.get_event(case.actors[persona], event["id"])
        assert view.get("mapping_visibility") == "visible"
        assert view["mapping"]["draft_id"] == receipt["draft_id"]
    with pytest.raises(AccountingError) as error:
        case.service.get_event(case.actors["other_employee"], event["id"])
    assert error.value.status == 404


def test_missing_mapping_is_only_reported_to_existing_finance_reader(business):
    case = business
    event = create(case)
    assert event.get("mapping_visibility") == "not_applicable"
    # Model a damaged read projection without bypassing database state guards:
    # no mutation/invalid handed_off row is ever written to the database.
    with case.db.transaction(actor=case.actors["finance"]) as conn:
        row = dict(conn.execute("SELECT * FROM business_events WHERE id=?", (event["id"],)).fetchone())
        row["status"] = "handed_off"
        finance = case.service._event_json(conn, case.actors["finance"], row)
        employee = case.service._event_json(conn, case.actors["employee"], row)
    assert finance.get("mapping_visibility") == "missing"
    assert employee.get("mapping_visibility") == "restricted"
    assert finance["mapping"] is None and employee["mapping"] is None


def test_revoked_accountant_cannot_reuse_mapping_visibility(business):
    case = business
    event = approved(case)
    handoff(case, event)
    assert case.service.get_event(case.actors["accountant"], event["id"]).get("mapping_visibility") == "visible"
    case.directory.revoke_book_member(case.manager, case.book["id"], "accountant")
    with pytest.raises(AccountingError) as error:
        case.service.get_event(case.actors["accountant"], event["id"])
    assert error.value.status == 404
