"""Additional N03 boundary tests; all deliveries are synthetic in-process sinks."""

import pytest

from app.accounting.domain import AccountingError
from app.delivery.followup_models import DeliveryTaskRequest
from app.delivery.followups import LocalSinkChannel
from test_delivery_followups import (  # noqa: F401 - fixture chain
    business,
    case,
    close_case,
    ready_case,
    freeze,
    grant,
    queue,
)


def test_old_sending_fence_cannot_settle_a_restored_epoch(case):
    freeze(case)
    grant(case)
    queue(case)
    claim = case.followups.claim("old-process")
    started = case.followups.begin(claim["id"], claim["lease_token"])
    assert started["state"] == "sending"
    with case.db.transaction(write=True) as conn:
        conn.execute(
            "INSERT INTO restore_state(instance_id,hold,restore_id,updated_at) "
            "VALUES('restored',0,'new-restore-instance','2026-10-29')"
        )
    assert not case.followups.finish(claim["id"], claim["lease_token"], "sink_written", "old-sink")
    case.followups.recover()
    with case.db.transaction() as conn:
        row = conn.execute(
            "SELECT state,sink_ref FROM delivery_notifications WHERE id=?", (claim["id"],)
        ).fetchone()
    assert row["state"] == "unknown" and row["sink_ref"] == ""


def test_unknown_cannot_be_resent_by_switching_reminder_kind(case):
    class Lost(LocalSinkChannel):
        def deliver(self, payload):
            raise TimeoutError("synthetic uncertain send")

    freeze(case)
    grant(case)
    queue(case)
    assert case.followups.dispatch(Lost())["unknown"] == 1
    with pytest.raises(AccountingError) as failure:
        case.followups.queue(
            case.reviewer,
            case.book["id"],
            DeliveryTaskRequest(
                period="2026-10",
                kind="signature_overdue",
                reason="不能借超期提醒重发未知消息",
            ),
        )
    assert failure.value.status == 409


@pytest.mark.parametrize("limit", [True, 0, -1, 101, 1.1, "1"])
def test_dispatch_rejects_invalid_limit_before_transport(case, limit):
    with pytest.raises(AccountingError) as failure:
        case.followups.dispatch(LocalSinkChannel(), limit=limit)
    assert failure.value.status == 422


def test_unknown_result_is_a_firm_followup_not_a_customer_wait(case):
    class Lost(LocalSinkChannel):
        def deliver(self, payload):
            raise TimeoutError("synthetic uncertain send")

    freeze(case)
    grant(case)
    queued = queue(case)
    case.followups.dispatch(Lost())
    item = next(
        item
        for item in case.followups.followups(case.reviewer)["items"]
        if item["book_id"] == case.book["id"]
    )
    assert item["owner"] == "firm_manager"
    assert item["next_step"] == "reconcile_notification"
    assert "notification_unknown" in item["reasons"]
    status = case.followups.status(case.reviewer, case.book["id"], "2026-10")
    assert (
        status["notification"]["manual_review"][0]["notification_id"] == queued["notification_id"]
    )
    assert "payload_json" not in status["notification"]["manual_review"][0]


def test_followup_feed_does_not_disclose_other_company_to_employee(case):
    freeze(case)
    assert case.followups.followups(case.actors["employee"])["items"] == []


def test_recovery_hold_quarantines_a_started_attempt_before_lease_expiry(case):
    freeze(case)
    grant(case)
    queue(case)
    claim = case.followups.claim("held-process")
    case.followups.begin(claim["id"], claim["lease_token"])
    with case.db.transaction(write=True) as conn:
        conn.execute(
            "INSERT INTO restore_state(instance_id,hold,restore_id,updated_at) VALUES('additional',1,'held','2026-10-29')"
        )
    assert case.followups.recover() == 1
    with case.db.transaction() as conn:
        row = conn.execute("SELECT state,lease_token FROM delivery_notifications").fetchone()
    assert row["state"] == "unknown" and row["lease_token"] == claim["lease_token"]


@pytest.mark.parametrize("revocation", ["book_read", "plan_contact"])
@pytest.mark.parametrize("boundary", ["queue", "claim", "begin"])
def test_recipient_must_still_be_able_to_open_and_sign_the_frozen_package(
    case, revocation, boundary
):
    from app.service_plans.models import PlanSave

    freeze(case)
    grant(case)
    if boundary != "queue":
        queue(case)
    claimed = case.followups.claim("recipient-boundary") if boundary == "begin" else None
    if revocation == "book_read":
        with case.db.transaction(write=True) as conn:
            conn.execute(
                "DELETE FROM book_access WHERE book_id=? AND user_id='finance'", (case.book["id"],)
            )
    else:
        with case.db.transaction() as conn:
            plan = case.followups.plans._plan(conn, case.book["id"])
        case.followups.plans.save_plan(
            case.manager,
            case.book["id"],
            PlanSave(
                expected_version=plan["version"],
                lead_user_id=plan["lead_user_id"],
                reviewer_user_id=plan["reviewer_user_id"],
                contact_user_id="company_admin",
                due_on=plan["due_on"],
                reason="客户改由新的联系人接收交付",
            ),
        )
    status = case.followups.status(case.reviewer, case.book["id"], "2026-10")
    assert status["recipient"]["state"] == "invalid"
    item = next(
        item
        for item in case.followups.followups(case.reviewer)["items"]
        if item["book_id"] == case.book["id"]
    )
    assert item["owner"] == "firm_manager" and item["next_step"] == "fix_recipient"
    if boundary == "queue":
        with pytest.raises(AccountingError) as failure:
            queue(case)
        assert failure.value.status == 409
        with case.db.transaction() as conn:
            assert conn.execute("SELECT COUNT(*) FROM delivery_notifications").fetchone()[0] == 0
        return
    if boundary == "begin":
        assert case.followups.begin(claimed["id"], claimed["lease_token"]) is None
    else:
        assert case.followups.dispatch(LocalSinkChannel())["sent"] == 0
    with case.db.transaction() as conn:
        assert conn.execute("SELECT state FROM delivery_notifications").fetchone()[0] == "cancelled"
