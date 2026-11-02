"""Manual evidence never rewrites the transport outcome or authorizes a resend."""

import sqlite3
import pytest
from app.accounting.domain import AccountingError
from app.delivery.followups import LocalSinkChannel
from test_delivery_followups import (  # noqa: F401
    business,
    case,
    close_case,
    ready_case,
    freeze,
    grant,
    queue,
)
from test_n03_notifications import notification


def unknown(case):
    freeze(case)
    grant(case)
    queue(case)

    class Lost(LocalSinkChannel):
        def deliver(self, payload):
            raise TimeoutError("synthetic uncertain outcome")

    case.followups.dispatch(Lost())
    return notification(case)


def command(row, **changes):
    from app.delivery.notification_investigations import InvestigationCreate

    return InvestigationCreate(
        **(
            {
                "expected_revision": 0,
                "expected_attempts": row["attempts"],
                "expected_package_version": row["package_version"],
                "expected_manifest_sha256": row["manifest_sha256"],
                "expected_recipient_user_id": row["recipient_user_id"],
                "conclusion": "not_sent",
                "evidence_ref": "local ticket SYNTHETIC-001",
                "reason": "在本地模拟通道中核对记录",
                "idempotency_key": "synthetic-investigation-001",
            }
            | changes
        )
    )


def record(case, row, actor=None, **changes):
    from app.delivery.notification_investigations import NotificationInvestigations

    return NotificationInvestigations(case.db).create(
        actor or case.manager, case.book["id"], row["id"], command(row, **changes)
    )


def test_manual_evidence_is_idempotent_and_does_not_resend(case):
    row = unknown(case)
    first = record(case, row)
    assert record(case, row) == first
    assert first["revision"] == 1
    assert notification(case)["state"] == "unknown"
    assert case.followups.dispatch(LocalSinkChannel())["sent"] == 0
    assert notification(case)["attempts"] == 1
    with case.db.transaction() as conn:
        assert (
            conn.execute("SELECT COUNT(*) FROM monthly_delivery_acknowledgements").fetchone()[0]
            == 0
        )
    with pytest.raises(AccountingError) as conflict:
        record(case, row, reason="同一个幂等键更改结论证据")
    assert conflict.value.status == 409


@pytest.mark.parametrize(
    "field,value",
    [
        ("expected_revision", 1),
        ("expected_attempts", 2),
        ("expected_package_version", 999),
        ("expected_manifest_sha256", "a" * 64),
        ("expected_recipient_user_id", "foreign-user"),
    ],
)
def test_stale_or_foreign_identity_is_rejected(case, field, value):
    row = unknown(case)
    with pytest.raises(AccountingError) as caught:
        record(case, row, **{field: value})
    assert caught.value.status == 409


def test_reviewer_cannot_register_manager_investigation(case):
    row = unknown(case)
    with pytest.raises(AccountingError) as caught:
        record(case, row, actor=case.actors["accountant"])
    assert caught.value.status == 403


def test_history_is_append_only_and_current_scope_bound(case):
    row = unknown(case)
    first = record(case, row)
    second = record(
        case,
        row,
        expected_revision=1,
        conclusion="unresolved",
        idempotency_key="synthetic-investigation-002",
    )
    assert second["revision"] == 2
    with pytest.raises(sqlite3.IntegrityError):
        with case.db.transaction(write=True) as conn:
            conn.execute(
                "UPDATE notification_investigations SET reason='rewrite' WHERE id=?", (first["id"],)
            )
    from app.delivery.notification_investigations import NotificationInvestigations

    with pytest.raises(AccountingError) as caught:
        NotificationInvestigations(case.db).history(case.manager, case.other_book["id"], row["id"])
    assert caught.value.status == 404


def test_list_projects_bound_history(case):
    row = unknown(case)
    first = record(case, row)
    item = case.followups.notifications(case.manager, case.book["id"], "2026-10")["items"][0]
    assert item["package"]["manifest_sha256"] == row["manifest_sha256"]
    assert item["investigation"] == first
    assert item["can_investigate"] is True
