"""Messages are scoped to the recipient; a read receipt never grants access."""

import pytest

from app.accounting.domain import AccountingError
from app.firm_tasks.service import FirmTasksService
from test_business import business


def message(c, identifier, recipient=None, company=None, firm=None):
    with c.db.transaction(write=True, actor=c.manager) as conn:
        conn.execute(
            "INSERT INTO client_messages(id,firm_id,company_id,recipient_user_id,kind,subject,"
            "body,created_by,created_at) VALUES(?,?,?,?,'return',?,?,?,?)",
            (
                identifier,
                firm or c.manager.firm_id,
                company or c.company["id"],
                recipient,
                identifier,
                "虚构补件",
                c.manager.user_id,
                "2026-11-03T00:00:00+00:00",
            ),
        )
    return FirmTasksService(c.db, c.service)


def test_other_recipient_and_other_company_cannot_be_marked(business):
    c = business
    service = message(c, "peer-message", c.actors["peer"].user_id)
    message(c, "other-company", company=c.other["id"])
    for identifier in ("peer-message", "other-company"):
        with pytest.raises(AccountingError) as caught:
            service.mark_message_read(c.actors["employee"], identifier)
        assert caught.value.status == 404


def test_broadcast_receipt_is_per_user_and_duplicate_read_is_idempotent(business):
    c = business
    service = message(c, "broadcast")
    service.mark_message_read(c.actors["employee"], "broadcast")
    service.mark_message_read(c.actors["employee"], "broadcast")
    assert service.messages(c.actors["employee"])["unread"] == 0
    assert service.messages(c.actors["peer"])["unread"] == 1


def test_recipient_membership_revocation_hides_history_and_rejects_old_action(business):
    c = business
    service = message(c, "own-message", c.actors["employee"].user_id)
    assert service.messages(c.actors["employee"])["unread"] == 1
    c.directory.revoke_company_member(c.manager, c.company["id"], c.actors["employee"].user_id)
    assert service.messages(c.actors["employee"])["messages"] == []
    with pytest.raises(AccountingError) as caught:
        service.mark_message_read(c.actors["employee"], "own-message")
    assert caught.value.status == 404
