"""N03 synthetic notification boundaries; no real transport or model is called."""

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.accounting.domain import AccountingError
from app.delivery.followups import DeliveryNotSent, LocalSinkChannel
from app.delivery.monthly_models import MonthlyPackageAcknowledge
from app.delivery import notification_dispatch
from app.delivery.followup_models import DeliveryRevokeRequest
from test_delivery_followups import (  # noqa: F401 - fixture dependencies
    business,
    case,
    close_case,
    freeze,
    grant,
    queue,
    ready_case,
)


def notification(case):
    with case.db.transaction() as conn:
        return dict(conn.execute("SELECT * FROM delivery_notifications").fetchone())


def test_legacy_null_grant_is_cancelled_when_contact_changes(case):
    freeze(case)
    queue(case)
    assert notification(case)["grant_id"] is None
    grant(case, recipient="company_admin")
    result = case.followups.dispatch(LocalSinkChannel())
    assert result["sent"] == 0
    assert notification(case)["state"] == "cancelled"


def test_legacy_null_grant_cannot_survive_revoke(case):
    freeze(case)
    queue(case)
    grant(case)
    case.followups.revoke(
        case.reviewer,
        case.book["id"],
        "2026-10",
        DeliveryRevokeRequest(period="2026-10", reason="停止此客户的待发通知"),
    )
    assert case.followups.dispatch(LocalSinkChannel())["sent"] == 0
    assert notification(case)["state"] == "cancelled"


def test_delivery_occurs_outside_database_write_transaction(case):
    freeze(case)
    grant(case)
    queue(case)
    errors = []

    class Probe(LocalSinkChannel):
        def deliver(self, payload):
            connection = sqlite3.connect(case.db.path, timeout=0)
            try:
                connection.execute("BEGIN IMMEDIATE")
                connection.rollback()
            except sqlite3.OperationalError as exc:
                errors.append(str(exc))
            finally:
                connection.close()
            return super().deliver(payload)

    assert case.followups.dispatch(Probe())["sent"] == 1
    assert errors == [], "Transport ran while SQLite write lock was held"


def test_timeout_after_transport_entry_stays_unknown_and_never_retries(case):
    freeze(case)
    grant(case)
    queue(case)
    calls = []

    class LostResponse(LocalSinkChannel):
        def deliver(self, payload):
            calls.append(payload["notification_id"])
            raise TimeoutError("Synthetic response lost after send")

    case.followups.dispatch(LostResponse())
    assert notification(case)["state"] == "unknown"
    for _ in range(3):
        assert case.followups.dispatch(LostResponse())["sent"] == 0
    assert len(calls) == 1
    assert notification(case)["attempts"] == 1


@pytest.mark.parametrize("field", ["recipient_user_id", "company_id", "payload_json"])
def test_notification_identity_cannot_be_rewritten_with_native_sql(case, field):
    freeze(case)
    grant(case)
    queue(case)
    original = notification(case)
    values = {
        "recipient_user_id": "company_admin",
        "company_id": case.other_book["company_id"],
        "payload_json": json.dumps({"recipient_user_id": "company_admin"}),
    }
    with pytest.raises(sqlite3.IntegrityError):
        with case.db.transaction(write=True) as conn:
            conn.execute(
                f"UPDATE delivery_notifications SET {field}=? WHERE id=?",
                (values[field], original["id"]),
            )
    assert notification(case)[field] == original[field]


@pytest.mark.parametrize("change", ["kind", "grant"])
def test_unknown_cannot_be_reissued_by_changing_kind_or_grant(case, change):
    freeze(case)
    grant(case)
    queue(case)

    class LostResponse(LocalSinkChannel):
        def deliver(self, payload):
            raise TimeoutError("Synthetic uncertain delivery")

    case.followups.dispatch(LostResponse())
    assert notification(case)["state"] == "unknown"
    if change == "grant":
        grant(case)
    with pytest.raises(AccountingError) as blocked:
        queue(case, kind="signature_overdue" if change == "kind" else "signature_reminder")
    assert blocked.value.status == 409
    with case.db.transaction() as conn:
        assert conn.execute("SELECT COUNT(*) FROM delivery_notifications").fetchone()[0] == 1


@pytest.fixture
def clock(monkeypatch):
    class Clock:
        value = datetime(2030, 1, 1, tzinfo=timezone.utc)

        def timestamp(self, seconds=0):
            return (self.value + timedelta(seconds=seconds)).isoformat()

        def advance(self, seconds):
            self.value += timedelta(seconds=seconds)

    clock = Clock()
    monkeypatch.setattr(notification_dispatch, "timestamp", clock.timestamp)
    return clock


def test_claim_is_exclusive_and_only_exact_fence_can_settle(case):
    freeze(case)
    grant(case)
    queue(case)
    first = case.followups.claim("worker-one")
    assert first["state"] == "claimed" and first["attempts"] == 0
    assert case.followups.claim("worker-two") is None
    assert case.followups.begin(first["id"], "wrong-token") is None
    started = case.followups.begin(first["id"], first["lease_token"])
    assert started["state"] == "sending" and started["attempts"] == 1
    assert not case.followups.finish(first["id"], "wrong-token", "sink_written", "local:wrong")
    assert case.followups.finish(
        first["id"], first["lease_token"], "sink_written", "local:confirmed"
    )
    assert not case.followups.finish(
        first["id"], first["lease_token"], "sink_written", "local:duplicate"
    )
    assert notification(case)["sink_ref"] == "local:confirmed"


def test_crash_before_send_marker_recovers_without_consuming_attempt(case, clock):
    freeze(case)
    grant(case)
    queue(case)
    first = case.followups.claim("crashed-before-marker", lease_seconds=1)
    clock.advance(2)
    assert case.followups.recover() == 1
    assert notification(case)["state"] == "pending"
    assert notification(case)["attempts"] == 0
    second = case.followups.claim("replacement")
    assert second["lease_token"] != first["lease_token"]
    assert case.followups.begin(first["id"], first["lease_token"]) is None
    assert case.followups.begin(second["id"], second["lease_token"])["attempts"] == 1


def test_crash_after_send_marker_recovers_unknown_without_resend(case, clock):
    freeze(case)
    grant(case)
    queue(case)
    first = case.followups.claim("crashed-after-marker")
    case.followups.begin(first["id"], first["lease_token"], lease_seconds=1)
    clock.advance(2)
    assert not case.followups.finish(
        first["id"], first["lease_token"], "sink_written", "late-receipt"
    )
    assert case.followups.recover() == 1
    row = notification(case)
    assert row["state"] == "unknown" and row["attempts"] == 1
    assert row["lease_token"] == first["lease_token"] and row["send_started_at"]
    assert case.followups.claim("restart") is None
    assert case.followups.dispatch(LocalSinkChannel())["sent"] == 0


def test_claimed_notification_rechecks_revocation_before_transport(case):
    freeze(case)
    grant(case)
    queue(case)
    claimed = case.followups.claim("waiting-for-transport")
    case.followups.revoke(
        case.reviewer,
        case.book["id"],
        "2026-10",
        DeliveryRevokeRequest(period="2026-10", reason="发送前撤回客户授权"),
    )
    assert case.followups.begin(claimed["id"], claimed["lease_token"]) is None
    assert notification(case)["state"] == "cancelled"
    assert notification(case)["attempts"] == 0


def test_explicit_not_sent_is_retryable_after_backoff_only(case, clock):
    freeze(case)
    grant(case)
    queue(case)
    calls = []

    class BeforeSendFailure(LocalSinkChannel):
        def deliver(self, payload):
            calls.append(payload["notification_id"])
            raise DeliveryNotSent("Synthetic proof that no bytes were sent")

    result = case.followups.dispatch(BeforeSendFailure())
    assert result["failed"] == 1
    assert notification(case)["state"] == "failed"
    assert case.followups.dispatch(BeforeSendFailure())["failed"] == 0
    assert len(calls) == 1
    clock.advance(31)
    assert case.followups.dispatch(LocalSinkChannel())["sent"] == 1
    assert notification(case)["attempts"] == 2


@pytest.mark.parametrize("receipt", [None, "", {}])
def test_invalid_transport_receipt_is_unknown_not_success(case, receipt):
    freeze(case)
    grant(case)
    queue(case)

    class InvalidReceipt(LocalSinkChannel):
        def deliver(self, payload):
            return receipt

    result = case.followups.dispatch(InvalidReceipt())
    assert result["unknown"] == 1 and result["sent"] == 0
    assert notification(case)["state"] == "unknown"
    assert case.followups.dispatch(LocalSinkChannel())["sent"] == 0


def test_internal_missing_contact_task_is_never_dispatched(case):
    freeze(case)
    grant(case)
    queue(case, kind="missing_contact_followup")
    assert case.followups.claim("worker") is None
    assert case.followups.dispatch(LocalSinkChannel())["sent"] == 0
    assert notification(case)["attempts"] == 0


def test_signed_package_stops_reminder_and_overdue_tasks(case):
    package = freeze(case)
    grant(case)
    queue(case)
    case.packages.acknowledge(
        case.actors["finance"],
        case.book["id"],
        package["id"],
        MonthlyPackageAcknowledge(
            manifest_sha256=package["manifest_sha256"], reason="客户确认收到当前版本"
        ),
    )
    assert case.followups.dispatch(LocalSinkChannel())["sent"] == 0
    assert notification(case)["state"] == "cancelled"
    for kind in ("signature_reminder", "signature_overdue"):
        with pytest.raises(AccountingError) as blocked:
            queue(case, kind=kind)
        assert blocked.value.status == 409
    feed = case.followups.followups(case.reviewer)
    assert not [item for item in feed["items"] if item["book_id"] == case.book["id"]]


def test_transport_payload_mutation_does_not_change_frozen_evidence(case):
    freeze(case)
    grant(case)
    queue(case)
    before = notification(case)["payload_json"]

    class MutatingAdapter(LocalSinkChannel):
        def deliver(self, payload):
            payload["recipient_user_id"] = "wrong-person"
            payload["manifest_sha256"] = "b" * 64
            return "local:mutated-copy"

    assert case.followups.dispatch(MutatingAdapter())["sent"] == 1
    assert notification(case)["payload_json"] == before


@pytest.mark.parametrize(
    "field,value",
    [
        ("package_id", "missing-package"),
        ("package_version", 999),
        ("manifest_sha256", "b" * 64),
        ("recipient_user_id", "company_admin"),
        ("grant_version", 999),
        ("restore_epoch", "fabricated-restore"),
    ],
)
def test_native_insert_requires_the_exact_source_binding(case, field, value):
    freeze(case)
    grant(case)
    queue(case)
    row = notification(case)
    row["id"] = "forged-" + row["id"]
    row["dedupe_key"] = "forged-" + row["dedupe_key"]
    row[field] = value
    payload = json.loads(row["payload_json"])
    payload["notification_id"] = row["id"]
    payload[field] = value
    row["payload_json"] = json.dumps(payload)
    with pytest.raises(sqlite3.IntegrityError):
        with case.db.transaction(write=True) as conn:
            conn.execute(
                f"INSERT INTO delivery_notifications({','.join(row)}) "
                f"VALUES({','.join('?' for _ in row)})",
                tuple(row.values()),
            )


def test_prequeued_other_kind_cannot_resend_unknown_delivery(case):
    freeze(case)
    grant(case)
    queue(case)
    queue(case, kind="signature_overdue")
    calls = []

    class LostResponse(LocalSinkChannel):
        def deliver(self, payload):
            calls.append(payload["notification_id"])
            raise TimeoutError("Synthetic uncertain result")

    case.followups.dispatch(LostResponse())
    assert len(calls) == 1
    assert case.followups.dispatch(LocalSinkChannel())["sent"] == 0


def test_forward_migration_preserves_legacy_payload_and_quarantines_unknown():
    migrations = Path(__file__).resolve().parents[1] / "migrations" / "sqlite"
    conn = sqlite3.connect(":memory:")
    try:
        # A minimal historical schema permits inspecting the actual forward DDL
        # independently of the fresh-schema template and current service guards.
        conn.executescript(
            "CREATE TABLE firms(id TEXT PRIMARY KEY);"
            "CREATE TABLE companies(id TEXT PRIMARY KEY);"
            "CREATE TABLE users(id TEXT PRIMARY KEY,active INTEGER);"
            "CREATE TABLE books(id TEXT PRIMARY KEY,firm_id TEXT,company_id TEXT);"
            "CREATE TABLE company_memberships(company_id TEXT,user_id TEXT);"
            "CREATE TABLE periods(book_id TEXT,period TEXT);"
            "CREATE TABLE monthly_delivery_packages(id TEXT PRIMARY KEY,firm_id TEXT,company_id TEXT,"
            "book_id TEXT,period TEXT,version INTEGER,manifest_sha256 TEXT,contact_user_id TEXT);"
            "CREATE TABLE monthly_delivery_acknowledgements(package_id TEXT);"
            "CREATE TABLE restore_state(instance_id TEXT PRIMARY KEY,hold INTEGER,restore_id TEXT);"
        )
        conn.executescript(
            (migrations / "105_monthly_delivery_followups.sql").read_text(encoding="utf-8")
        )
        original = '{ "legacy" : "preserve exact bytes" }'
        for name, state, attempts in (
            ("never_sent", "pending", 0),
            ("ambiguous_pending", "pending", 2),
            ("ambiguous_failed", "failed", 3),
            ("written", "sink_written", 1),
        ):
            conn.execute(
                "INSERT INTO delivery_notifications(id,firm_id,company_id,book_id,period,kind,"
                "dedupe_key,state,attempts,payload_json,created_by,created_at,updated_at) "
                "VALUES(?,'firm','company','book','2026-10','signature_reminder',?,?,?,?,"
                "'maker','2026-10-01','2026-10-01')",
                (name, name, state, attempts, original),
            )
        conn.commit()
        for version in (110, 111, 112, 113):
            conn.executescript(next(migrations.glob(f"{version:03d}_*.sql")).read_text("utf-8"))
        rows = {
            row[0]: row[1:]
            for row in conn.execute(
                "SELECT id,state,attempts,payload_json,legacy_payload_json FROM delivery_notifications"
            )
        }
        assert rows["never_sent"] == ("cancelled", 0, original, original)
        assert rows["ambiguous_pending"] == ("unknown", 2, original, original)
        assert rows["ambiguous_failed"] == ("unknown", 3, original, original)
        assert rows["written"] == ("sink_written", 1, original, original)
    finally:
        conn.close()


@pytest.mark.parametrize("already_claimed", [False, True])
def test_creator_review_permission_is_rechecked_before_send(case, already_claimed):
    freeze(case)
    grant(case)
    queue(case)
    claimed = case.followups.claim("pre-revocation") if already_claimed else None
    with case.db.transaction(write=True) as conn:
        conn.execute(
            "DELETE FROM memberships WHERE firm_id=? AND user_id=?",
            (case.reviewer.firm_id, case.reviewer.user_id),
        )
    if claimed:
        assert case.followups.begin(claimed["id"], claimed["lease_token"]) is None
    else:
        assert case.followups.dispatch(LocalSinkChannel())["sent"] == 0
    assert notification(case)["state"] == "cancelled"
    assert notification(case)["attempts"] == 0


def test_native_payload_must_keep_explicit_null_bindings_and_exact_keys(case):
    freeze(case)
    grant(case)
    queue(case, kind="missing_contact_followup")
    row = notification(case)
    row["id"] = "extra-key-" + row["id"]
    row["dedupe_key"] = "extra-key-" + row["dedupe_key"]
    payload = json.loads(row["payload_json"])
    payload["notification_id"] = row["id"]
    assert payload.pop("package_id") is None
    payload["untrusted_instructions"] = "Not a permitted notification field"
    row["payload_json"] = json.dumps(payload)
    with pytest.raises(sqlite3.IntegrityError):
        with case.db.transaction(write=True) as conn:
            conn.execute(
                f"INSERT INTO delivery_notifications({','.join(row)}) "
                f"VALUES({','.join('?' for _ in row)})",
                tuple(row.values()),
            )


@pytest.mark.parametrize("state", ["claimed", "sending", "unknown"])
def test_native_insert_cannot_reissue_uncertain_package(case, state):
    freeze(case)
    grant(case)
    queue(case)
    claimed = case.followups.claim("already-processing")
    if state in {"sending", "unknown"}:
        case.followups.begin(claimed["id"], claimed["lease_token"])
    if state == "unknown":
        case.followups.finish(claimed["id"], claimed["lease_token"], "unknown", error="lost")
    row = notification(case)
    row.update(
        id="duplicate-" + row["id"],
        dedupe_key="duplicate-" + row["dedupe_key"],
        kind="signature_overdue",
        state="pending",
        attempts=0,
        worker_id=None,
        lease_token=None,
        lease_until=None,
        send_started_at=None,
        sink_ref="",
        last_error="",
        available_at="",
        dispatched_at=None,
    )
    payload = json.loads(row["payload_json"])
    payload.update(notification_id=row["id"], kind=row["kind"])
    row["payload_json"] = json.dumps(payload)
    with pytest.raises(sqlite3.IntegrityError, match="Uncertain or in-flight"):
        with case.db.transaction(write=True) as conn:
            conn.execute(
                f"INSERT INTO delivery_notifications({','.join(row)}) "
                f"VALUES({','.join('?' for _ in row)})",
                tuple(row.values()),
            )


@pytest.mark.parametrize("attack", ["internal_recipient", "initial_available_at"])
def test_native_insert_matches_worker_contract_for_internal_and_initial_state(case, attack):
    freeze(case)
    grant(case)
    queue(case)
    row = notification(case)
    row["id"] = "contract-" + row["id"]
    row["dedupe_key"] = "contract-" + row["dedupe_key"]
    if attack == "internal_recipient":
        row["kind"] = "missing_contact_followup"
    else:
        row["available_at"] = "2099-01-01T00:00:00+00:00"
    payload = json.loads(row["payload_json"])
    payload.update(notification_id=row["id"], kind=row["kind"])
    row["payload_json"] = json.dumps(payload)
    with pytest.raises(sqlite3.IntegrityError):
        with case.db.transaction(write=True) as conn:
            conn.execute(
                f"INSERT INTO delivery_notifications({','.join(row)}) "
                f"VALUES({','.join('?' for _ in row)})",
                tuple(row.values()),
            )


def change_recipient_eligibility(case, change):
    from app.service_plans.models import PlanSave

    if change == "book_read":
        with case.db.transaction(write=True) as conn:
            conn.execute(
                "DELETE FROM book_access WHERE book_id=? AND user_id='finance'",
                (case.book["id"],),
            )
    elif change == "company_membership":
        with case.db.transaction(write=True) as conn:
            conn.execute(
                "DELETE FROM company_memberships WHERE company_id=? AND user_id='finance'",
                (case.book["company_id"],),
            )
    elif change == "plan_contact":
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
                reason="客户更换交付资料的联系人",
            ),
        )


@pytest.mark.parametrize("change", ["book_read", "plan_contact", "company_membership", "none"])
def test_native_insert_requires_current_recipient_signing_eligibility(case, change):
    freeze(case)
    grant(case)
    queue(case)
    row = notification(case)
    row["id"] = "current-recipient-" + row["id"]
    row["dedupe_key"] = "current-recipient-" + row["dedupe_key"]
    payload = json.loads(row["payload_json"])
    payload["notification_id"] = row["id"]
    row["payload_json"] = json.dumps(payload)
    change_recipient_eligibility(case, change)

    def insert():
        with case.db.transaction(write=True) as conn:
            conn.execute(
                f"INSERT INTO delivery_notifications({','.join(row)}) "
                f"VALUES({','.join('?' for _ in row)})",
                tuple(row.values()),
            )

    if change == "none":
        insert()
        with case.db.transaction() as conn:
            assert conn.execute("SELECT COUNT(*) FROM delivery_notifications").fetchone()[0] == 2
    else:
        with pytest.raises(sqlite3.IntegrityError):
            insert()


@pytest.mark.parametrize("change", ["book_read", "plan_contact", "company_membership", "none"])
@pytest.mark.parametrize("boundary", ["claim", "begin"])
def test_native_start_rechecks_current_recipient_signing_eligibility(case, change, boundary):
    freeze(case)
    grant(case)
    queue(case)
    if boundary == "begin":
        case.followups.claim("native-recheck")
    row = notification(case)
    change_recipient_eligibility(case, change)

    def start():
        with case.db.transaction(write=True) as conn:
            if boundary == "claim":
                conn.execute(
                    "UPDATE delivery_notifications SET state='claimed',worker_id='native-recheck',"
                    "lease_token='native-fence',lease_until='2099-01-01T00:00:00+00:00' WHERE id=?",
                    (row["id"],),
                )
            else:
                conn.execute(
                    "UPDATE delivery_notifications SET state='sending',attempts=attempts+1,"
                    "send_started_at='2030-01-01T00:00:00+00:00' WHERE id=?",
                    (row["id"],),
                )

    if change == "none":
        start()
        assert notification(case)["state"] == ("claimed" if boundary == "claim" else "sending")
    else:
        with pytest.raises(sqlite3.IntegrityError):
            start()
