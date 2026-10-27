"""P01: delivery configuration and reminders for already-locked months.

Every case runs on the local SQLite engine with fictional data. Nothing is sent:
the only enabled adapter is the local sink, and the disabled adapter must leave
tasks pending instead of pretending they were delivered.
"""

import json

import pytest

from app.accounting.domain import AccountingError
from app.delivery.followup_models import (
    DeliveryGrantRequest,
    DeliveryRevokeRequest,
    DeliveryTaskRequest,
)
from app.delivery.followups import (
    ChannelDisabled,
    DeliveryFollowupService,
    DisabledChannel,
    LocalSinkChannel,
)
from app.delivery.monthly_models import MonthlyPackageAcknowledge, MonthlyPackageCreate
from app.delivery.monthly_package import MonthlyPackageService
from app.reports.models import ReportCommand
from app.reports.service import ReportService
from app.service_plans.models import PlanSave
from app.service_plans.service import ServicePlansService
from test_business import business  # noqa: F401 - pytest fixture chain
from test_p0_close_dependencies import close_case  # noqa: F401 - pytest fixture chain
from test_monthly_delivery_package import ready_case  # noqa: F401 - pytest fixture


@pytest.fixture
def case(ready_case):  # noqa: F811
    plans = ServicePlansService(ready_case.db, ready_case.ledger, ready_case.service)
    ready_case.packages = MonthlyPackageService(ready_case.db, plans)
    ready_case.followups = DeliveryFollowupService(ready_case.db, ready_case.packages, plans)
    return ready_case


def freeze(case):
    return case.packages.create(
        case.reviewer,
        case.book["id"],
        MonthlyPackageCreate(
            idempotency_key="p01-package-1",
            report_id=case.report["report"]["id"],
            expected_report_version=case.report["report"]["version"],
            reason="会计复核后冻结交付",
        ),
    )


def grant(case, recipient="finance", period="2026-10"):
    return case.followups.configure(
        case.reviewer,
        case.book["id"],
        DeliveryGrantRequest(
            period=period, recipient_user_id=recipient, reason="历史账期补齐签收人"
        ),
    )


def queue(case, kind="signature_reminder", period="2026-10"):
    return case.followups.queue(
        case.reviewer,
        case.book["id"],
        DeliveryTaskRequest(period=period, kind=kind, reason="到期提醒客户签收"),
    )


def test_locked_history_package_is_listed_and_sendable(case):
    package = freeze(case)
    state = case.followups.status(case.reviewer, case.book["id"], "2026-10")
    assert state["period_status"] == "closed"
    assert state["event"]["state"] == "published"
    assert state["event"]["package_id"] == package["id"]
    assert state["notification"]["channel"] == "disabled"
    assert state["signature"]["state"] == "awaiting"
    assert "signature_missing" in state["followup"]["reasons"]


def test_missing_contact_is_a_controlled_error_then_grant_unblocks(case):
    freeze(case)
    # The plan contact is deactivated in the client company: no eligible recipient.
    with case.db.transaction(write=True) as conn:
        conn.execute("DELETE FROM company_memberships WHERE user_id='finance'")
    with pytest.raises(AccountingError) as blocked:
        queue(case)
    assert blocked.value.status == 409
    assert "收件人" in blocked.value.detail

    assert (
        case.followups.status(case.reviewer, case.book["id"], "2026-10")["recipient"]["state"]
        == "invalid"
    )
    created = grant(case, recipient="company_admin")
    assert created["version"] == 1
    assert created["previous_recipient_user_id"] is None
    state = case.followups.status(case.reviewer, case.book["id"], "2026-10")["recipient"]
    assert state["state"] == "valid" and state["source"] == "grant"
    assert queue(case)["state"] == "pending"


def test_grant_requires_current_company_member(case):
    freeze(case)
    with pytest.raises(AccountingError) as denied:
        grant(case, recipient="not-a-member")
    assert denied.value.status == 422


def test_repeat_queue_deduplicates_and_revoke_is_immediate(case):
    freeze(case)
    grant(case)
    first = queue(case)
    again = queue(case)
    assert again["deduplicated"] is True
    assert again["notification_id"] == first["notification_id"]

    revoked = case.followups.revoke(
        case.reviewer,
        case.book["id"],
        "2026-10",
        DeliveryRevokeRequest(period="2026-10", reason="客户更换签收人，先撤回"),
    )
    assert revoked["cancelled_notifications"] == 1
    state = case.followups.status(case.reviewer, case.book["id"], "2026-10")
    assert state["notification"]["cancelled"] == 1
    with pytest.raises(AccountingError) as blocked:
        queue(case)
    assert blocked.value.status == 409
    assert "撤回" in blocked.value.detail


def test_disabled_channel_keeps_tasks_pending(case):
    freeze(case)
    grant(case)
    queue(case)
    result = case.followups.dispatch(DisabledChannel())
    assert result["blocked"] == 1 and result["sent"] == 0
    state = case.followups.status(case.reviewer, case.book["id"], "2026-10")
    assert state["notification"]["pending"] == 1
    assert state["notification"]["sink_written"] == 0


def test_local_sink_writes_once_and_keeps_signature_awaiting(case):
    freeze(case)
    grant(case)
    queue(case)
    first = case.followups.dispatch(LocalSinkChannel())
    second = case.followups.dispatch(LocalSinkChannel())
    assert first["sent"] == 1
    assert second["sent"] == 0
    state = case.followups.status(case.reviewer, case.book["id"], "2026-10")
    assert state["notification"]["sink_written"] == 1
    assert state["signature"]["state"] == "awaiting"
    with case.db.transaction() as conn:
        row = conn.execute("SELECT sink_ref,dispatched_at FROM delivery_notifications").fetchone()
    assert row["sink_ref"].startswith("local-sink:")
    assert row["dispatched_at"]


def test_transient_failure_is_recoverable(case):
    class Flaky(LocalSinkChannel):
        def deliver(self, payload):
            raise TimeoutError("网络暂时不可用")

    freeze(case)
    grant(case)
    queue(case)
    failed = case.followups.dispatch(Flaky())
    assert failed["failed"] == 1
    state = case.followups.status(case.reviewer, case.book["id"], "2026-10")
    assert state["notification"]["failed"] == 1

    recovered = case.followups.dispatch(LocalSinkChannel())
    assert recovered["sent"] == 1
    assert (
        case.followups.status(case.reviewer, case.book["id"], "2026-10")["notification"][
            "sink_written"
        ]
        == 1
    )


def test_authorization_change_cancels_in_flight_task(case):
    freeze(case)
    grant(case)
    queue(case)
    with case.db.transaction(write=True) as conn:
        conn.execute("DELETE FROM company_memberships WHERE user_id='finance'")
    result = case.followups.dispatch(LocalSinkChannel())
    assert result["cancelled"] == 1 and result["sent"] == 0


def test_restore_hold_blocks_outbound(case, tmp_path, monkeypatch):
    freeze(case)
    grant(case)
    queue(case)
    marker = tmp_path / "restore-hold.json"
    marker.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("ACCOUNTING_RESTORE_HOLD_FILE", str(marker))
    with pytest.raises(AccountingError) as held:
        case.followups.dispatch(LocalSinkChannel())
    assert held.value.status == 409
    assert (
        case.followups.status(case.reviewer, case.book["id"], "2026-10")["notification"]["pending"]
        == 1
    )


def test_contact_change_cannot_sign_an_old_package(case):
    package = freeze(case)
    grant(case, recipient="company_admin")
    # The package keeps the contact frozen at generation time.
    with pytest.raises(AccountingError):
        case.packages.acknowledge(
            case.actors["company_admin"],
            case.book["id"],
            package["id"],
            MonthlyPackageAcknowledge(
                manifest_sha256=package["manifest_sha256"],
                reason="新签收人尝试签收旧包",
            ),
        )
    signed = case.packages.acknowledge(
        case.actors["finance"],
        case.book["id"],
        package["id"],
        MonthlyPackageAcknowledge(
            manifest_sha256=package["manifest_sha256"],
            reason="历史签收人确认收到交付包",
        ),
    )
    assert signed["acknowledgement"]["acknowledged_by"] == "finance"


def test_feed_lists_only_periods_that_need_a_step(case):
    freeze(case)
    feed = case.followups.followups(case.reviewer)
    items = [item for item in feed["items"] if item["book_id"] == case.book["id"]]
    assert len(items) == 1
    assert set(items[0]["reasons"]) == {"recipient_missing", "signature_missing"}
    assert items[0]["next_step"] == "configure_recipient"


def test_audit_trail_records_configuration(case):
    freeze(case)
    grant(case)
    queue(case)
    with case.db.transaction() as conn:
        rows = conn.execute(
            "SELECT action FROM audit_events WHERE action LIKE 'delivery.%' ORDER BY action"
        ).fetchall()
    assert {row["action"] for row in rows} >= {
        "delivery.recipient.configure",
        "delivery.notification.queue",
    }
