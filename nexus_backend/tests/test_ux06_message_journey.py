import pytest
from app.accounting.domain import AccountingError
from app.business.models import ReturnRequest
from app.firm_tasks.service import FirmTasksService
from test_business import business, submitted  # noqa: F401
from test_monthly_delivery_package import ready_case, freeze  # noqa: F401
from test_p0_close_dependencies import close_case  # noqa: F401


def test_return_creates_one_private_actionable_notification(business):
    c = business
    event = submitted(c)
    returned = c.service.return_event(
        c.actors["approver"],
        event["id"],
        ReturnRequest(expected_version=event["version"], reason="缺少用途说明"),
    )
    service = FirmTasksService(c.db, c.ledger)
    rows = service.messages(c.actors["employee"])["messages"]
    assert len(rows) == 1
    assert rows[0]["related_ref"] == returned["id"]
    target = service.message_target(c.actors["employee"], rows[0]["id"])
    assert target["kind"] == "business" and target["event_id"] == event["id"]
    assert target["company_id"] == c.company["id"]
    assert service.messages(c.actors["peer"])["messages"] == []
    with pytest.raises(AccountingError):
        service.message_target(c.actors["peer"], rows[0]["id"])
    c.directory.revoke_company_member(c.manager, c.company["id"], c.actors["employee"].user_id)
    with pytest.raises(AccountingError):
        service.message_target(c.actors["employee"], rows[0]["id"])


def test_message_target_does_not_treat_notification_as_authority(business):
    from test_client_messages_boundaries import message

    c = business
    event = submitted(c, actor="peer")
    service = message(c, "forged-reference", c.actors["employee"].user_id)
    with c.db.transaction(write=True, actor=c.manager) as conn:
        conn.execute(
            "UPDATE client_messages SET related_kind='business',related_ref=? WHERE id='forged-reference'",
            (event["id"],),
        )
    with pytest.raises(AccountingError):
        service.message_target(c.actors["employee"], "forged-reference")


def test_package_notice_pins_manifest_and_does_not_acknowledge(ready_case):
    c = ready_case
    package = freeze(c)
    assert freeze(c)["id"] == package["id"]
    service = FirmTasksService(c.db, c.ledger)
    rows = [
        r
        for r in service.messages(c.actors["finance"])["messages"]
        if r["related_kind"] == "monthly_package"
    ]
    assert len(rows) == 1
    target = service.message_target(c.actors["finance"], rows[0]["id"])
    assert target["package_id"] == package["id"] and target["version"] == package["version"]
    assert target["manifest_sha256"] == package["manifest_sha256"]
    assert (
        c.packages.detail(c.actors["finance"], c.book["id"], package["id"])["package"][
            "customer_receipt_status"
        ]
        == "not_recorded"
    )
