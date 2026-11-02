"""Restricted PG16/17 sessions, synthetic notifications; no outbound adapters."""

from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4
import pytest
from app.accounting.domain import AccountingError
from app.delivery.followups import DeliveryFollowupService, LocalSinkChannel
from app.delivery.notification_investigations import NotificationInvestigations
from test_dcp_notification_investigation import command
from test_n03_notification_postgres import (  # noqa: F401
    p0_pg,
    p0_case,
    package_case,
    notification_case,
    native,
    _row,
)


@pytest.fixture
def unknown_case(notification_case):
    c = notification_case
    dispatcher = DeliveryFollowupService(c.worker_db)
    claimed = dispatcher.claim("dcp-r567")
    assert claimed["id"] == c.notification["notification_id"]
    assert dispatcher.begin(claimed["id"], claimed["lease_token"])
    assert dispatcher.finish(claimed["id"], claimed["lease_token"], "unknown", "", "synthetic_lost")
    c.unknown = _row(c)
    c.investigations = NotificationInvestigations(c.db)
    return c


def create(c, **changes):
    return c.investigations.create(
        c.manager, c.book["id"], c.unknown["id"], command(c.unknown, **changes)
    )


def test_pg_manual_evidence_survives_idempotency_without_resend(unknown_case):
    c = unknown_case
    first = create(c)
    assert first == create(c)
    assert c.investigations.history(c.reviewer, c.book["id"], c.unknown["id"])["items"] == [first]
    assert DeliveryFollowupService(c.worker_db).dispatch(LocalSinkChannel())["sent"] == 0
    assert _row(c)["state"] == "unknown" and _row(c)["attempts"] == 1


@pytest.mark.parametrize(
    "field", ["company_id", "book_id", "created_by", "manifest_sha256", "revision"]
)
def test_pg_native_source_and_actor_binding(unknown_case, field):
    c = unknown_case
    row = create(c)
    values = {**row, "id": uuid4().hex, "revision": 2, "idempotency_key": uuid4().hex}
    values[field] = {
        "company_id": c.other_book["company_id"],
        "book_id": c.other_book["id"],
        "created_by": c.reviewer.user_id,
        "manifest_sha256": "f" * 64,
        "revision": 9,
    }[field]
    with pytest.raises(c.psycopg.Error) as error:
        with native(c, c.manager) as conn:
            conn.execute(
                "INSERT INTO notification_investigations("
                + ",".join(values)
                + ") VALUES("
                + ",".join("?" for _ in values)
                + ")",
                tuple(values.values()),
            )
    assert error.value.sqlstate in {"42501", "23514"}


def test_pg_append_only_rls_and_worker_denial(unknown_case):
    c = unknown_case
    row = create(c)
    with native(c, c.foreign) as conn:
        assert conn.execute("SELECT id FROM notification_investigations").fetchall() == []
    for sql in (
        "UPDATE notification_investigations SET reason=reason WHERE id=?",
        "DELETE FROM notification_investigations WHERE id=?",
    ):
        with pytest.raises(c.psycopg.Error) as error:
            with native(c, c.manager) as conn:
                conn.execute(sql, (row["id"],))
        assert error.value.sqlstate == "42501"
    with native(c, c.manager) as conn:
        for role in ("accounting_worker", "accounting_projection"):
            assert not conn.execute(
                "SELECT has_table_privilege(?, 'accounting.notification_investigations', 'INSERT') AS p",
                (role,),
            ).fetchone()["p"]


def test_pg_two_sessions_compare_and_append(unknown_case):
    c = unknown_case

    def attempt(key):
        try:
            return create(c, idempotency_key=key)["revision"]
        except AccountingError as error:
            return error.status

    with ThreadPoolExecutor(max_workers=2) as pool:
        values = list(pool.map(attempt, ["parallel-key-one", "parallel-key-two"]))
    assert sorted(values) == [1, 409]
    assert len(c.investigations.history(c.manager, c.book["id"], c.unknown["id"])["items"]) == 1


def test_pg_revoked_manager_cannot_append(unknown_case):
    c = unknown_case
    c.manager, c.reviewer = c.reviewer, c.manager
    first = create(c)
    with c.psycopg.connect(c.admin) as conn:
        conn.execute(
            "UPDATE accounting.memberships SET role='reviewer' WHERE user_id=%s AND firm_id=%s",
            (c.manager.user_id, c.manager.firm_id),
        )
    with pytest.raises(AccountingError):
        create(c, expected_revision=1, idempotency_key="after-revoke-key")
    with native(c, c.reviewer) as conn:
        assert (
            conn.execute(
                "SELECT COUNT(*) AS n FROM notification_investigations WHERE notification_id=?",
                (first["notification_id"],),
            ).fetchone()["n"]
            == 1
        )
