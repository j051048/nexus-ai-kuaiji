"""N03 recovery evidence: notification identity is frozen, never replayed by restore.

The database/original copy is real SQLite backup/restore. Channels are local test
objects only. PostgreSQL permissions/concurrency have a separate N03 suite.
"""

import copy
import hashlib
import json
import re
import sqlite3

import pytest

from app.accounting.database import Database
from app.accounting.domain import AccountingError
from app.delivery.followups import DeliveryFollowupService, LocalSinkChannel
from app.delivery.monthly_package import MonthlyPackageService
from scripts import backup_accounting as recovery
from scripts import release_recovery_hold as release
from test_delivery_followups import (  # noqa: F401 - fixture dependencies
    business,
    case,
    close_case,
    freeze,
    grant,
    queue,
    ready_case,
)


NOTIFICATION = {
    "id": "notification-synthetic",
    "firm_id": "firm-a",
    "company_id": "company-a",
    "book_id": "book-a",
    "period": "2026-10",
    "package_id": "package-a",
    "package_version": 1,
    "manifest_sha256": "a" * 64,
    "recipient_user_id": "customer-a",
    "grant_id": "grant-a",
    "grant_version": 1,
    "kind": "signature_reminder",
    "dedupe_key": "synthetic-dedupe-key",
    "state": "unknown",
    "attempts": 1,
    "worker_id": "local-worker",
    "lease_token": "synthetic-private-lease",
    "lease_until": "2026-10-29T01:02:00+00:00",
    "send_started_at": "2026-10-29T01:00:00+00:00",
    "available_at": "2026-10-29T01:00:00+00:00",
    "restore_epoch": "previous-restore",
    "payload_json": '{"note":"synthetic-private-body"}',
    "sink_ref": "synthetic-private-channel-receipt",
    "created_by": "accountant-a",
    "created_at": "2026-10-29T01:00:00+00:00",
    "updated_at": "2026-10-29T01:00:00+00:00",
    "dispatched_at": None,
    "last_error": "synthetic-private-error",
}


class InventoryConnection:
    """Exercise the exact SQL projection without requiring another PG server."""

    def __init__(self, notification=None):
        self.tables = {table: [] for table in release.INVENTORY_TABLES}
        self.tables["delivery_notifications"] = [
            copy.deepcopy(NOTIFICATION if notification is None else notification)
        ]
        self.rows = []

    def execute(self, statement, arguments=()):
        if isinstance(statement, str):
            assert "information_schema.columns" in statement
            table = arguments[0]
            # A post-N04 restore target carries the tax and delivery evidence
            # columns; the release proof must fail closed when they are absent.
            rows = self.tables[table]
            if table in release.REQUIRED_COLUMNS:
                columns = list(release.REQUIRED_COLUMNS[table])
                if rows and set(columns) - set(rows[0]):
                    columns = list(rows[0])  # Simulate a pre-N03/N04 schema.
            else:
                columns = list(rows[0]) if rows else ["id", "state"]
            self.rows = [(name,) for name in columns]
        else:
            query = statement.as_string()
            fields, table = query.split(" FROM accounting.")
            names = re.findall(r'"([a-z_0-9]+)"', fields)
            table = table.strip('"')
            self.rows = [tuple(row[name] for name in names) for row in self.tables[table]]
        return self

    def __iter__(self):
        return iter(self.rows)

    def fetchall(self):
        return self.rows


def test_notification_inventory_is_bound_without_exposing_payload_or_tokens():
    result = release.pending_inventory(InventoryConnection())
    assert "delivery_notifications" in result["tables"]
    row = result["tables"]["delivery_notifications"][0]
    for key in ("payload_json", "lease_token", "sink_ref", "last_error"):
        assert row[key] == hashlib.sha256(NOTIFICATION[key].encode()).hexdigest()
    serialized = json.dumps(result)
    assert "synthetic-private" not in serialized
    assert row["package_version"] == 1 and row["restore_epoch"] == "previous-restore"


@pytest.mark.parametrize(
    "field,value",
    [
        ("state", "sink_written"),
        ("company_id", "company-b"),
        ("book_id", "book-b"),
        ("period", "2026-11"),
        ("package_id", "package-b"),
        ("package_version", 2),
        ("manifest_sha256", "b" * 64),
        ("recipient_user_id", "customer-b"),
        ("grant_id", "grant-b"),
        ("grant_version", 2),
        ("kind", "signature_overdue"),
        ("attempts", 2),
        ("send_started_at", "2026-10-29T01:01:00+00:00"),
        ("lease_token", "new-private-lease"),
        ("lease_until", "2026-10-29T01:04:00+00:00"),
        ("restore_epoch", "current-restore"),
        ("payload_json", '{"note":"changed-private-body"}'),
        ("sink_ref", "changed-private-receipt"),
    ],
)
def test_notification_change_invalidates_release_inventory(field, value):
    before = release.pending_inventory(InventoryConnection())
    changed = dict(NOTIFICATION, **{field: value})
    after = release.pending_inventory(InventoryConnection(changed))
    assert before["sha256"] != after["sha256"]


def test_notification_inventory_rejects_incomplete_restore_schema():
    with pytest.raises(ValueError, match="通知"):
        release.pending_inventory(InventoryConnection({"id": "legacy", "state": "pending"}))


def _restored(case, tmp_path):
    archive, target = tmp_path / "archive", tmp_path / "restored"
    manifest = recovery.backup(case.db.path, case.sub.storage.root, archive)
    receipt = recovery.restore(archive, target)
    assert receipt["verified_files"] >= 2  # Database and actual synthetic bank original.
    assert receipt["serving_enabled"] is False
    assert receipt["outbound_disabled"] is True
    assert receipt["worker_autostart"] is False
    assert "monthly_delivery_grants" not in manifest["original_tables"]
    copied = Database(target / "database.sqlite")
    return copied, target, receipt


def _release_sqlite_fixture(copied, target):
    """Explicit test-only release, not a production release tool or bypass."""
    with sqlite3.connect(copied.path) as conn:
        conn.execute(
            "UPDATE restore_state SET hold=0,reason='synthetic recovery checks complete',"
            "updated_by='n03-test' WHERE instance_id='restored'"
        )
    (target / "restore-verification.json").rename(target / "restore-released.json")


def test_real_restore_database_hold_alone_blocks_notifications(case, tmp_path):
    freeze(case)
    grant(case)
    queue(case)
    copied, target, _ = _restored(case, tmp_path)
    # Verify the DB hold independently: a misplaced marker must not re-enable sending.
    (target / "restore-verification.json").rename(target / "saved-verification.json")
    with pytest.raises(AccountingError) as blocked:
        DeliveryFollowupService(copied).dispatch(LocalSinkChannel())
    assert blocked.value.status == 409
    with copied.transaction() as conn:
        row = conn.execute("SELECT state,attempts FROM delivery_notifications").fetchone()
    assert row["state"] == "pending" and row["attempts"] == 0


def test_restore_released_old_pending_cannot_auto_send_and_scope_survives(case, tmp_path):
    package = freeze(case)
    authorization = grant(case)
    queued = queue(case)
    copied, target, receipt = _restored(case, tmp_path)
    _release_sqlite_fixture(copied, target)
    followups = DeliveryFollowupService(copied)
    result = followups.dispatch(LocalSinkChannel())
    assert result["sent"] == 0
    with copied.transaction() as conn:
        restored_row = dict(
            conn.execute(
                "SELECT * FROM delivery_notifications WHERE id=?", (queued["notification_id"],)
            ).fetchone()
        )
        restored_grant = dict(
            conn.execute(
                "SELECT * FROM monthly_delivery_grants WHERE id=?", (authorization["grant_id"],)
            ).fetchone()
        )
    assert restored_row["state"] == "cancelled" and restored_row["attempts"] == 0
    assert restored_row["restore_epoch"] != receipt["restore_id"]
    assert restored_row["package_id"] == package["id"]
    assert restored_row["manifest_sha256"] == package["manifest_sha256"]
    assert restored_grant["recipient_user_id"] == "finance"
    assert restored_grant["state"] == "active"
    packages = MonthlyPackageService(copied)
    assert packages.download(case.actors["finance"], case.book["id"], package["id"])
    with pytest.raises(AccountingError) as denied:
        packages.download(case.manager, case.other_book["id"], package["id"])
    assert denied.value.status == 404


def test_restored_unknown_stays_unknown_after_explicit_release(case, tmp_path):
    from app.delivery.followup_models import DeliveryTaskRequest

    class LostReceipt(LocalSinkChannel):
        def deliver(self, payload):
            raise TimeoutError("synthetic response lost after send")

    freeze(case)
    grant(case)
    queue(case)
    case.followups.dispatch(LostReceipt())
    copied, target, _ = _restored(case, tmp_path)
    _release_sqlite_fixture(copied, target)
    followups = DeliveryFollowupService(copied)
    result = followups.dispatch(LocalSinkChannel())
    assert result["sent"] == 0
    with copied.transaction() as conn:
        row = conn.execute("SELECT state,attempts FROM delivery_notifications").fetchone()
    assert row["state"] == "unknown" and row["attempts"] == 1
    # A fresh restore epoch must not become an alternate idempotency key for
    # retrying a send whose original result still has not been reconciled.
    with pytest.raises(AccountingError) as unresolved:
        followups.queue(
            case.reviewer,
            case.book["id"],
            DeliveryTaskRequest(
                period="2026-10", kind="signature_reminder", reason="恢复后尝试重排未知通知"
            ),
        )
    assert unresolved.value.status == 409 and "未知" in unresolved.value.detail


def test_new_explicit_queue_binds_new_restore_epoch_only_after_release(case, tmp_path):
    from app.delivery.followup_models import DeliveryTaskRequest

    freeze(case)
    grant(case)
    queued = queue(case)
    copied, target, receipt = _restored(case, tmp_path)
    followups = DeliveryFollowupService(copied)
    request = DeliveryTaskRequest(
        period="2026-10", kind="signature_reminder", reason="恢复核对后重新提醒"
    )
    with pytest.raises(AccountingError) as held:
        followups.queue(case.reviewer, case.book["id"], request)
    assert held.value.status == 409
    _release_sqlite_fixture(copied, target)
    followups.dispatch(LocalSinkChannel())  # Quarantines old epoch without transmitting.
    new = followups.queue(case.reviewer, case.book["id"], request)
    assert new["notification_id"] != queued["notification_id"]
    with copied.transaction() as conn:
        row = conn.execute(
            "SELECT restore_epoch,attempts FROM delivery_notifications WHERE id=?",
            (new["notification_id"],),
        ).fetchone()
    assert row["restore_epoch"] == receipt["restore_id"] and row["attempts"] == 0
    assert followups.dispatch(LocalSinkChannel())["sent"] == 1


@pytest.mark.parametrize(
    "phase,expected,attempts", [("claimed", "cancelled", 0), ("sending", "unknown", 1)]
)
def test_restored_inflight_generation_is_quarantined(case, tmp_path, phase, expected, attempts):
    freeze(case)
    grant(case)
    queued = queue(case)
    claimed = case.followups.claim("n03-source-process")
    assert claimed["id"] == queued["notification_id"]
    if phase == "sending":
        assert case.followups.begin(claimed["id"], claimed["lease_token"])
    copied, target, _ = _restored(case, tmp_path)
    _release_sqlite_fixture(copied, target)
    result = DeliveryFollowupService(copied).dispatch(LocalSinkChannel())
    assert result["sent"] == 0
    with copied.transaction() as conn:
        row = conn.execute("SELECT state,attempts FROM delivery_notifications").fetchone()
    assert row["state"] == expected and row["attempts"] == attempts


def test_old_sender_token_cannot_settle_restored_generation(case, tmp_path):
    freeze(case)
    grant(case)
    queue(case)
    claimed = case.followups.claim("n03-source-process")
    assert case.followups.begin(claimed["id"], claimed["lease_token"])
    copied, target, _ = _restored(case, tmp_path)
    _release_sqlite_fixture(copied, target)
    assert (
        DeliveryFollowupService(copied).finish(
            claimed["id"], claimed["lease_token"], "sink_written", "synthetic-late-receipt"
        )
        is False
    )
    with copied.transaction() as conn:
        row = conn.execute("SELECT state,sink_ref FROM delivery_notifications").fetchone()
    assert row["state"] != "sink_written" and row["sink_ref"] == ""


@pytest.mark.parametrize("operation", ["dispatch", "queue"])
def test_any_database_hold_blocks_new_notification_actions(case, operation):
    freeze(case)
    grant(case)
    queue(case)
    with case.db.transaction(write=True) as conn:
        conn.execute(
            "INSERT INTO restore_state(instance_id,hold,restore_id,reason,updated_by,updated_at) "
            "VALUES('secondary-recovery',1,'local-id','synthetic hold','tester','2026-10-29')"
        )
    with pytest.raises(AccountingError) as held:
        if operation == "dispatch":
            case.followups.dispatch(LocalSinkChannel())
        else:
            queue(case)
    assert held.value.status == 409
