"""Fenced N03 sends over real independent PostgreSQL sessions/processes.

No notification adapter has an external endpoint. Channels either record a
synthetic local result or raise; native SQL tests verify the database boundary.
"""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import json
import multiprocessing
import os
import threading
import time
from uuid import uuid4

import pytest

from app.accounting.database import PostgresConnection
from app.accounting.domain import AccountingError
from app.delivery.followup_models import (
    DeliveryGrantRequest,
    DeliveryRevokeRequest,
    DeliveryTaskRequest,
)
from app.delivery.followups import DeliveryFollowupService, LocalSinkChannel
from test_n03_notification_postgres import (
    p0_pg,
    p0_case,
    package_case,
    notification_case,
    recipient_notification_case,
    native,
    _row,
    _insert,
    recipient_ineligible,
)  # noqa: F401


@contextmanager
def worker(c):
    from psycopg.rows import dict_row

    with c.psycopg.connect(c.worker_url, autocommit=True, row_factory=dict_row) as raw:
        conn = PostgresConnection(raw)
        conn.begin(write=True, role="accounting_worker")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise


def call(c, name, *args):
    with worker(c) as conn:
        return conn.execute(
            "SELECT accounting." + name + "(" + ",".join("?" for _ in args) + ")", args
        ).fetchone()[name]


def claim(c, lease=30):
    result = call(c, "claim_delivery_notification", "n03-worker", lease)
    assert result and result["id"] == c.notification["notification_id"]
    return result


def begin(c, row, lease=30):
    return call(c, "begin_delivery_notification", row["id"], row["lease_token"], lease)


def finish(c, row, state="sink_written"):
    return call(
        c,
        "finish_delivery_notification",
        row["id"],
        row["lease_token"],
        state,
        "n03-local-reference" if state == "sink_written" else "",
        "synthetic",
    )


def _child_claim(dsn, start, result):
    import psycopg

    try:
        start.wait(15)
        with psycopg.connect(dsn) as conn:
            conn.execute("SET LOCAL ROLE accounting_worker")
            row = conn.execute(
                "SELECT accounting.claim_delivery_notification(%s,30)", ("process-" + uuid4().hex,)
            ).fetchone()[0]
        result.put({"id": row["id"] if row else None})
    except Exception as error:
        result.put({"error": type(error).__name__})


def test_two_process_claims_exactly_once(notification_case):
    c = notification_case
    ctx = multiprocessing.get_context("spawn")
    start, result = ctx.Event(), ctx.Queue()
    children = [
        ctx.Process(target=_child_claim, args=(c.worker_url, start, result)) for _ in range(2)
    ]
    try:
        for process in children:
            process.start()
        start.set()
        answers = [result.get(timeout=20) for _ in children]
        assert all("error" not in answer for answer in answers), answers
        assert sum(answer["id"] == c.notification["notification_id"] for answer in answers) == 1
        for process in children:
            process.join(10)
            assert process.exitcode == 0
        assert _row(c)["attempts"] == 0
    finally:
        for process in children:
            if process.is_alive():
                process.terminate()
                process.join(5)


def test_worker_cannot_directly_read_or_mutate_notifications(notification_case):
    c = notification_case
    for statement in (
        "SELECT * FROM delivery_notifications",
        "UPDATE delivery_notifications SET state='sink_written'",
        "DELETE FROM delivery_notifications",
    ):
        with pytest.raises(c.psycopg.errors.InsufficientPrivilege):
            with worker(c) as conn:
                conn.execute(statement)


def test_app_cannot_invoke_worker_function(notification_case):
    c = notification_case
    with pytest.raises(c.psycopg.errors.InsufficientPrivilege):
        with native(c, c.reviewer) as conn:
            conn.execute("SELECT accounting.claim_delivery_notification('forged-worker',30)")


def test_begin_finish_fences_and_attempt_count(notification_case):
    c = notification_case
    row = claim(c)
    assert row["state"] == "claimed" and row["attempts"] == 0
    assert call(c, "begin_delivery_notification", row["id"], "wrong-token", 30) is None
    sent = begin(c, row)
    assert sent["state"] == "sending" and sent["attempts"] == 1
    assert begin(c, row) is None
    assert (
        call(c, "finish_delivery_notification", row["id"], "wrong-token", "sink_written", "ref", "")
        is False
    )
    assert finish(c, sent) is True
    assert finish(c, sent) is False
    assert call(c, "claim_delivery_notification", "next", 30) is None
    stored = _row(c)
    assert stored["state"] == "sink_written" and stored["attempts"] == 1


def test_expired_claim_reclaims_without_send_attempt(notification_case):
    c = notification_case
    old = claim(c, 1)
    time.sleep(1.1)
    assert call(c, "recover_delivery_notifications") == 1
    new = claim(c)
    assert new["lease_token"] != old["lease_token"] and new["attempts"] == 0
    assert begin(c, old) is None


def test_sending_crash_becomes_unknown_without_reclaim(notification_case):
    c = notification_case
    sent = begin(c, claim(c), 1)
    time.sleep(1.1)
    assert call(c, "recover_delivery_notifications") == 1
    assert _row(c)["state"] == "unknown" and _row(c)["attempts"] == 1
    assert call(c, "claim_delivery_notification", "restart", 30) is None
    assert finish(c, sent) is False


def test_revoke_after_claim_prevents_begin(notification_case):
    c = notification_case
    row = claim(c)
    c.followups.revoke(
        c.reviewer,
        c.book["id"],
        "2026-10",
        DeliveryRevokeRequest(period="2026-10", reason="虚构发送前撤销授权"),
    )
    assert begin(c, row) is None
    assert _row(c)["state"] == "cancelled" and _row(c)["attempts"] == 0


@pytest.mark.parametrize("boundary", ["claim", "begin"])
@pytest.mark.parametrize("revocation", ["book_read", "plan_contact"])
def test_worker_rechecks_current_recipient_eligibility(
    recipient_notification_case, boundary, revocation
):
    c = recipient_notification_case
    row = claim(c) if boundary == "begin" else None
    with recipient_ineligible(c, revocation):
        if boundary == "claim":
            result = claim(c)
            assert result["state"] == "cancelled"
        else:
            assert begin(c, row) is None
    with c.db.transaction(actor=c.reviewer) as conn:
        result = conn.execute(
            "SELECT state,attempts FROM delivery_notifications WHERE id=?",
            (c.notification["notification_id"],),
        ).fetchone()
    assert result["state"] == "cancelled" and result["attempts"] == 0


def test_creator_loses_review_before_send(notification_case):
    c = notification_case
    row = claim(c)
    with c.psycopg.connect(c.admin) as conn:
        conn.execute(
            "UPDATE accounting.memberships SET role='operator' WHERE firm_id=%s AND user_id=%s",
            (c.reviewer.firm_id, c.reviewer.user_id),
        )
    try:
        assert begin(c, row) is None
        assert _row(c)["state"] == "cancelled"
    finally:
        with c.psycopg.connect(c.admin) as conn:
            conn.execute(
                "UPDATE accounting.memberships SET role='admin' WHERE firm_id=%s AND user_id=%s",
                (c.reviewer.firm_id, c.reviewer.user_id),
            )


def test_unknown_cannot_be_requeued_with_new_kind_or_key(notification_case):
    c = notification_case
    sent = begin(c, claim(c))
    assert finish(c, sent, "unknown")
    with pytest.raises(c.psycopg.errors.CheckViolation):
        _insert(c, c.reviewer)
    with pytest.raises(AccountingError):
        c.followups.queue(
            c.reviewer,
            c.book["id"],
            DeliveryTaskRequest(
                period="2026-10", kind="signature_overdue", reason="不得绕过未知结果"
            ),
        )


def test_prequeued_other_kind_does_not_bypass_unknown(notification_case):
    c = notification_case
    second = c.followups.queue(
        c.reviewer,
        c.book["id"],
        DeliveryTaskRequest(period="2026-10", kind="signature_overdue", reason="预先排队另一提醒"),
    )
    sent = begin(c, claim(c))
    assert finish(c, sent, "unknown")
    other = call(c, "claim_delivery_notification", "restart", 30)
    assert other["id"] == second["notification_id"] and other["state"] == "cancelled"
    assert call(c, "claim_delivery_notification", "restart", 30) is None


def test_restore_generation_blocks_old_lease_settlement(notification_case):
    c = notification_case
    sent = begin(c, claim(c))
    with c.psycopg.connect(c.admin) as conn:
        conn.execute(
            "INSERT INTO accounting.restore_state(instance_id,hold,restore_id,updated_at) "
            "VALUES('restored',false,%s,'now')",
            (uuid4().hex,),
        )
    try:
        assert finish(c, sent) is False
        assert call(c, "recover_delivery_notifications") >= 1
        assert _row(c)["state"] == "unknown"
        assert call(c, "claim_delivery_notification", "restored-worker", 30) is None
    finally:
        with c.psycopg.connect(c.admin) as conn:
            conn.execute("DELETE FROM accounting.restore_state WHERE instance_id='restored'")


def test_restore_database_hold_blocks_claim_and_begin(notification_case):
    c = notification_case
    row = claim(c)
    with c.psycopg.connect(c.admin) as conn:
        conn.execute(
            "INSERT INTO accounting.restore_state(instance_id,hold,updated_at) VALUES('n03-test',true,'now')"
        )
    try:
        assert begin(c, row) is None
        assert call(c, "claim_delivery_notification", "held-worker", 30) is None
        assert _row(c)["attempts"] == 0
    finally:
        with c.psycopg.connect(c.admin) as conn:
            conn.execute("DELETE FROM accounting.restore_state WHERE instance_id='n03-test'")


def test_recovery_inventory_binds_notification_state(notification_case):
    c = notification_case
    from scripts.release_recovery_hold import pending_inventory
    from scripts.recovery_security import digest

    with c.psycopg.connect(c.admin) as conn:
        before = pending_inventory(conn)
    assert before["tables"]["delivery_notifications"]
    claim(c)
    with c.psycopg.connect(c.admin) as conn:
        after = pending_inventory(conn)
    assert digest(before) != digest(after)


def test_slow_local_channel_does_not_hold_company_write_lock(notification_case):
    c = notification_case
    entered, release = threading.Event(), threading.Event()

    class Slow(LocalSinkChannel):
        def deliver(self, payload):
            entered.set()
            assert release.wait(15), "test send was not released"
            return super().deliver(payload)

    service = DeliveryFollowupService(c.worker_db)
    with ThreadPoolExecutor(max_workers=2) as pool:
        dispatched = pool.submit(service.dispatch, Slow(), 1)
        try:
            assert entered.wait(10)
            # This real authorized write would block if the sending transaction
            # still held either the global compatibility or company lock.
            changed = pool.submit(
                c.followups.configure,
                c.reviewer,
                c.book["id"],
                DeliveryGrantRequest(
                    period="2026-10",
                    recipient_user_id=c.manager.user_id,
                    reason="慢发送期间独立配置不应阻塞",
                ),
            )
            assert changed.result(timeout=5)["version"] == 2
        finally:
            release.set()
        assert dispatched.result(timeout=10)["sent"] == 1
    assert _row(c)["state"] == "sink_written"


@pytest.mark.parametrize("worker_id,lease", [("   ", 30), ("worker", None), ("worker", 601)])
def test_claim_boundary_rejects_blank_null_and_oversized_lease(notification_case, worker_id, lease):
    c = notification_case
    with pytest.raises(c.psycopg.errors.InvalidParameterValue):
        call(c, "claim_delivery_notification", worker_id, lease)


@pytest.mark.parametrize("lease", [None, 601])
def test_begin_boundary_rejects_null_or_oversized_lease(notification_case, lease):
    c = notification_case
    row = claim(c)
    with pytest.raises(c.psycopg.errors.InvalidParameterValue):
        begin(c, row, lease)


@pytest.mark.parametrize(
    "outcome,reference,error",
    [
        (None, "", ""),
        ("unknown", None, ""),
        ("unknown", "", None),
        ("sink_written", "x" * 501, ""),
        ("unknown", "", "x" * 201),
        ("sink_written", "   ", ""),
    ],
)
def test_finish_boundary_rejects_nulls_and_oversized_data(
    notification_case, outcome, reference, error
):
    c = notification_case
    with pytest.raises(c.psycopg.errors.InvalidParameterValue):
        call(c, "finish_delivery_notification", "missing", "token", outcome, reference, error)


def test_settlement_audit_preserves_executor_and_attempt_identity(notification_case):
    c = notification_case
    sent = begin(c, claim(c))
    assert finish(c, sent, "unknown")
    row = _row(c)
    assert row["worker_id"] == sent["worker_id"]
    assert (
        row["lease_token"] == sent["lease_token"]
        and row["send_started_at"] == sent["send_started_at"]
    )
    with c.db.transaction(actor=c.reviewer) as conn:
        audit = conn.execute(
            "SELECT details FROM audit_events WHERE object_id=? AND action='delivery.notification.settled'",
            (sent["id"],),
        ).fetchone()
    data = json.loads(audit["details"])
    assert data["executor_kind"] == "worker"
    assert data["initiated_by"] == c.reviewer.user_id
    assert data["worker_id"] == sent["worker_id"] and data["lease_sha256"]


def _child_begin_then_crash(dsn, identifier, token, committed):
    import psycopg

    with psycopg.connect(dsn) as conn:
        conn.execute("SET LOCAL ROLE accounting_worker")
        result = conn.execute(
            "SELECT accounting.begin_delivery_notification(%s,%s,1)", (identifier, token)
        ).fetchone()[0]
        assert result["state"] == "sending"
    committed.set()
    os._exit(23)


def test_process_crash_after_send_marker_never_replays(notification_case):
    c = notification_case
    row = claim(c)
    ctx = multiprocessing.get_context("spawn")
    committed = ctx.Event()
    process = ctx.Process(
        target=_child_begin_then_crash,
        args=(c.worker_url, row["id"], row["lease_token"], committed),
    )
    process.start()
    try:
        assert committed.wait(15)
        process.join(10)
        assert process.exitcode == 23
        time.sleep(1.1)
        assert call(c, "recover_delivery_notifications") >= 1
        assert _row(c)["state"] == "unknown" and _row(c)["attempts"] == 1
        assert call(c, "claim_delivery_notification", "replacement", 30) is None
    finally:
        if process.is_alive():
            process.terminate()
            process.join(5)


def test_actual_service_queue_and_worker_dispatch_local_sink(package_case):
    c = package_case
    package = c.monthly.create(c.reviewer, c.book["id"], c.request)
    followups = DeliveryFollowupService(c.db)
    followups.configure(
        c.reviewer,
        c.book["id"],
        DeliveryGrantRequest(
            period="2026-10", recipient_user_id=c.manager.user_id, reason="真实服务虚构联系人"
        ),
    )
    queued = followups.queue(
        c.reviewer,
        c.book["id"],
        DeliveryTaskRequest(
            period="2026-10", kind="signature_reminder", reason="真实服务虚构签收提醒"
        ),
    )
    service = DeliveryFollowupService(c.worker_db)
    assert service.dispatch(LocalSinkChannel())["sent"] == 1
    assert service.dispatch(LocalSinkChannel())["sent"] == 0
    with c.db.transaction(actor=c.manager) as conn:
        row = conn.execute(
            "SELECT * FROM delivery_notifications WHERE id=?", (queued["notification_id"],)
        ).fetchone()
        assert row["state"] == "sink_written" and row["attempts"] == 1
        assert (
            row["package_version"] == package["version"]
            and row["manifest_sha256"] == package["manifest_sha256"]
        )
        assert (
            conn.execute(
                "SELECT count(*) FROM monthly_delivery_acknowledgements WHERE package_id=?",
                (package["id"],),
            ).fetchone()[0]
            == 0
        )


def test_g0_queue_is_cancelled_after_explicit_grant_change(package_case):
    c = package_case
    c.monthly.create(c.reviewer, c.book["id"], c.request)
    followups = DeliveryFollowupService(c.db)
    queued = followups.queue(
        c.reviewer,
        c.book["id"],
        DeliveryTaskRequest(
            period="2026-10", kind="signature_reminder", reason="旧冻结联系人无显式授权"
        ),
    )
    followups.configure(
        c.reviewer,
        c.book["id"],
        DeliveryGrantRequest(
            period="2026-10", recipient_user_id=c.manager.user_id, reason="新增显式授权使旧任务失效"
        ),
    )
    service = DeliveryFollowupService(c.worker_db)
    assert service.dispatch(LocalSinkChannel())["sent"] == 0
    with c.db.transaction(actor=c.manager) as conn:
        row = conn.execute(
            "SELECT state,attempts FROM delivery_notifications WHERE id=?",
            (queued["notification_id"],),
        ).fetchone()
        assert row["state"] == "cancelled" and row["attempts"] == 0
