"""N03 restricted native SQL and worker contracts on disposable PG16/17.

Fixtures build a genuinely reviewed, locked and published monthly package.
Attack statements deliberately bypass Python AccessControl, never database roles.
"""

from contextlib import contextmanager
import json
import os
from types import SimpleNamespace
from urllib.parse import urlsplit
from uuid import uuid4

import pytest

from app.accounting.database import Database, PostgresConnection
from app.accounting.domain import Actor
from app.delivery.followup_models import DeliveryGrantRequest, DeliveryTaskRequest
from app.delivery.followups import DeliveryFollowupService
from test_monthly_delivery_postgres import package_case  # noqa: F401
from test_p0_close_upload_postgres import p0_case  # noqa: F401


@pytest.fixture(scope="module")
def p0_pg():
    admin = os.getenv("ACCOUNTING_TEST_POSTGRES_URL")
    runtime = os.getenv("ACCOUNTING_TEST_DATABASE_URL")
    worker = os.getenv("ACCOUNTING_TEST_WORKER_DATABASE_URL")
    if not all((admin, runtime, worker)):
        pytest.skip("N03 requires an explicit isolated PostgreSQL runner")
    for value in (admin, runtime, worker):
        target = urlsplit(value)
        assert target.hostname == "127.0.0.1" and target.port in (25531, 25532)
        assert target.path.startswith("/n03_local_") and not target.query
    import psycopg
    from scripts.migrate_accounting import migrate

    migrate(admin)
    for value in (runtime, worker):
        with psycopg.connect(value) as conn:
            assert conn.execute(
                "SELECT rolsuper,rolbypassrls,rolcreatedb,rolcreaterole "
                "FROM pg_roles WHERE rolname=current_user"
            ).fetchone() == (False, False, False, False)
    db = Database(runtime)
    worker_db = Database(worker)
    yield SimpleNamespace(
        admin=admin, runtime=runtime, worker_url=worker, db=db, worker_db=worker_db, psycopg=psycopg
    )
    db.close()
    worker_db.close()


@contextmanager
def native(c, actor):
    from psycopg.rows import dict_row

    with c.psycopg.connect(c.runtime, autocommit=True, row_factory=dict_row) as raw:
        conn = PostgresConnection(raw)
        conn.begin(write=True, user_id=actor.user_id, firm_id=actor.firm_id)
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise


@pytest.fixture
def notification_case(package_case):
    yield from _notification_case(package_case)


@pytest.fixture
def recipient_notification_case(package_case):
    from app.service_plans.models import PlanSave
    from app.service_plans.service import ServicePlansService

    c = package_case
    person = "n03-contact-" + uuid4().hex
    with c.psycopg.connect(c.admin) as conn:
        conn.execute(
            "INSERT INTO accounting.users(id,name,email) VALUES(%s,'N03客户财务',%s)",
            (person, person + "@example.test"),
        )
        conn.execute(
            "INSERT INTO accounting.memberships(firm_id,user_id,role) VALUES(%s,%s,'operator')",
            (c.manager.firm_id, person),
        )
        conn.execute(
            "INSERT INTO accounting.company_memberships(company_id,user_id,role) VALUES(%s,%s,'finance')",
            (c.book["company_id"], person),
        )
        conn.execute(
            "INSERT INTO accounting.book_access(book_id,firm_id,user_id,role) VALUES(%s,%s,%s,'viewer')",
            (c.book["id"], c.manager.firm_id, person),
        )
    ServicePlansService(c.db, c.accounting, c.business).save_plan(
        c.reviewer,
        c.book["id"],
        PlanSave(
            expected_version=1,
            contact_user_id=person,
            due_on="2026-11-10",
            reason="N03有账套读取权的客户财务",
        ),
    )
    c.manager = Actor(person, c.manager.firm_id)
    yield from _notification_case(c)


def _notification_case(c):
    c.package = c.monthly.create(c.reviewer, c.book["id"], c.request)
    c.followups = DeliveryFollowupService(c.db)
    c.grant = c.followups.configure(
        c.reviewer,
        c.book["id"],
        DeliveryGrantRequest(
            period="2026-10", recipient_user_id=c.manager.user_id, reason="N03虚构交付联系人"
        ),
    )
    # Native seed is intentional: SQL guards must work independently of the
    # service under parallel implementation. Optional new columns let this same
    # attack fixture reproduce the original schema's bypass before migration.
    identifier = uuid4().hex
    payload = {
        "notification_id": identifier,
        "firm_id": c.reviewer.firm_id,
        "company_id": c.book["company_id"],
        "book_id": c.book["id"],
        "period": "2026-10",
        "package_id": c.package["id"],
        "package_version": c.package["version"],
        "manifest_sha256": c.package["manifest_sha256"],
        "recipient_user_id": c.manager.user_id,
        "grant_id": c.grant["grant_id"],
        "grant_version": c.grant["version"],
        "restore_epoch": "",
        "kind": "signature_reminder",
        "reason": "N03虚构签收提醒",
    }
    values = {
        key: value for key, value in payload.items() if key not in {"notification_id", "reason"}
    }
    values.update(
        id=identifier,
        dedupe_key="n03-seed-" + identifier,
        state="pending",
        payload_json=json.dumps(payload),
        created_by=c.reviewer.user_id,
        created_at="2026-10-29T00:00:00+00:00",
        updated_at="2026-10-29T00:00:00+00:00",
    )
    with native(c, c.reviewer) as conn:
        columns = {
            row["column_name"]
            for row in conn.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_schema='accounting' AND table_name='delivery_notifications'"
            )
        }
        values = {key: value for key, value in values.items() if key in columns}
        conn.execute(
            "INSERT INTO delivery_notifications("
            + ",".join(values)
            + ") VALUES("
            + ",".join("?" for _ in values)
            + ")",
            tuple(values.values()),
        )
    c.notification = {"notification_id": identifier}
    person = "n03-viewer-" + uuid4().hex
    c.viewer = Actor(person, c.manager.firm_id)
    with c.psycopg.connect(c.admin) as conn:
        conn.execute(
            "INSERT INTO accounting.users(id,name,email) VALUES(%s,'N03只读用户',%s)",
            (person, person + "@example.test"),
        )
        conn.execute(
            "INSERT INTO accounting.memberships(firm_id,user_id,role) VALUES(%s,%s,'operator')",
            (c.manager.firm_id, person),
        )
        conn.execute(
            "INSERT INTO accounting.book_access(book_id,firm_id,user_id,role) VALUES(%s,%s,%s,'viewer')",
            (c.book["id"], c.manager.firm_id, person),
        )
    yield c
    with native(c, c.reviewer) as conn:
        conn.execute(
            "UPDATE delivery_notifications SET state='cancelled',last_error='synthetic_fixture_cleanup' "
            "WHERE book_id=? AND state IN('pending','failed','claimed')",
            (c.book["id"],),
        )


def _row(c):
    with c.db.transaction(actor=c.manager) as conn:
        return dict(
            conn.execute(
                "SELECT * FROM delivery_notifications WHERE id=?",
                (c.notification["notification_id"],),
            ).fetchone()
        )


def _insert(c, actor, _values=None, **changes):
    values = dict(_values) if _values is not None else _row(c)
    values.update(id=uuid4().hex, dedupe_key="n03-copy-" + uuid4().hex, created_by=actor.user_id)
    payload = json.loads(values["payload_json"])
    payload["notification_id"] = values["id"]
    for key, value in changes.items():
        if key in payload:
            payload[key] = value
    values["payload_json"] = json.dumps(payload)
    values.update(changes)
    with native(c, actor) as conn:
        conn.execute(
            "INSERT INTO delivery_notifications("
            + ",".join(values)
            + ") VALUES("
            + ",".join("?" for _ in values)
            + ")",
            tuple(values.values()),
        )


@contextmanager
def recipient_ineligible(c, kind):
    """Change current authority, retaining a valid immutable frozen package."""
    if kind == "book_read":
        with c.psycopg.connect(c.admin) as conn:
            conn.execute(
                "DELETE FROM accounting.book_access WHERE book_id=%s AND user_id=%s",
                (c.book["id"], c.manager.user_id),
            )
    else:
        from app.service_plans.models import PlanSave
        from app.service_plans.service import ServicePlansService

        # Changing to another contact is a valid operation; the old frozen
        # package still names manager and must not be sent as signable.
        with c.psycopg.connect(c.admin) as conn:
            conn.execute(
                "INSERT INTO accounting.company_memberships(company_id,user_id,role) VALUES(%s,%s,'admin')",
                (c.book["company_id"], c.reviewer.user_id),
            )
        ServicePlansService(c.db, c.accounting, c.business).save_plan(
            c.reviewer,
            c.book["id"],
            PlanSave(
                expected_version=2,
                contact_user_id=c.reviewer.user_id,
                due_on="2026-11-10",
                reason="N03虚构客户联系人变更",
            ),
        )
    try:
        yield
    finally:
        if kind == "book_read":
            with c.psycopg.connect(c.admin) as conn:
                conn.execute(
                    "INSERT INTO accounting.book_access(book_id,firm_id,user_id,role) VALUES(%s,%s,%s,'viewer')",
                    (c.book["id"], c.manager.firm_id, c.manager.user_id),
                )


@pytest.mark.parametrize("revocation", ["book_read", "plan_contact"])
def test_native_insert_rechecks_current_recipient_eligibility(
    recipient_notification_case, revocation
):
    c = recipient_notification_case
    values = _row(c)
    with recipient_ineligible(c, revocation):
        with pytest.raises(c.psycopg.errors.CheckViolation):
            _insert(c, c.reviewer, _values=values)


def test_viewer_cannot_revoke_delivery_grant(notification_case):
    c = notification_case
    with native(c, c.viewer) as conn:
        count = conn.execute(
            "UPDATE monthly_delivery_grants SET state='revoked',revoked_by=?,revoked_at='now' WHERE id=?",
            (c.viewer.user_id, c.grant["grant_id"]),
        ).rowcount
    assert count == 0


def test_viewer_cannot_insert_notification(notification_case):
    c = notification_case
    with pytest.raises(c.psycopg.errors.InsufficientPrivilege):
        _insert(c, c.viewer)


def test_viewer_cannot_mutate_pending_notification(notification_case):
    c = notification_case
    with native(c, c.viewer) as conn:
        count = conn.execute(
            "UPDATE delivery_notifications SET state='cancelled' WHERE id=?",
            (c.notification["notification_id"],),
        ).rowcount
    assert count == 0


@pytest.mark.parametrize("field", ["company_id", "package_id", "recipient_user_id", "payload_json"])
def test_native_notification_scope_binding(notification_case, field):
    c = notification_case
    changed = {
        "company_id": c.other_book["company_id"],
        "package_id": "missing-package",
        "recipient_user_id": c.reviewer.user_id,
        "payload_json": "{}",
    }[field]
    with pytest.raises((c.psycopg.errors.CheckViolation, c.psycopg.errors.InsufficientPrivilege)):
        _insert(c, c.reviewer, **{field: changed})


@pytest.mark.parametrize("field", ["recipient_user_id", "payload_json", "created_by"])
def test_native_notification_identity_is_immutable(notification_case, field):
    c = notification_case
    changed = {
        "recipient_user_id": c.reviewer.user_id,
        "payload_json": "{}",
        "created_by": c.manager.user_id,
    }[field]
    with pytest.raises((c.psycopg.errors.CheckViolation, c.psycopg.errors.InsufficientPrivilege)):
        with native(c, c.reviewer) as conn:
            conn.execute(
                f"UPDATE delivery_notifications SET {field}=? WHERE id=?",
                (changed, c.notification["notification_id"]),
            )


def test_native_actor_cannot_forge_grant_revoker(notification_case):
    c = notification_case
    with pytest.raises(c.psycopg.errors.InsufficientPrivilege):
        with native(c, c.reviewer) as conn:
            conn.execute(
                "UPDATE monthly_delivery_grants SET state='revoked',revoked_by=?,revoked_at='now' WHERE id=?",
                (c.manager.user_id, c.grant["grant_id"]),
            )


def test_foreign_firm_cannot_read_notifications(notification_case):
    c = notification_case
    with native(c, c.foreign) as conn:
        assert (
            conn.execute(
                "SELECT id FROM delivery_notifications WHERE id=?",
                (c.notification["notification_id"],),
            ).fetchall()
            == []
        )


@pytest.mark.parametrize(
    "field", ["package_version", "manifest_sha256", "grant_version", "grant_id"]
)
def test_native_binding_matches_server_version_not_self_consistent_payload(
    notification_case, field
):
    c = notification_case
    changed = {
        "package_version": c.package["version"] + 1,
        "manifest_sha256": "0" * 64,
        "grant_version": c.grant["version"] + 1,
        "grant_id": None,
    }[field]
    with pytest.raises(c.psycopg.errors.CheckViolation):
        _insert(c, c.reviewer, **{field: changed})


def test_native_cannot_insert_already_sent_notification(notification_case):
    c = notification_case
    with pytest.raises(c.psycopg.errors.CheckViolation):
        _insert(c, c.reviewer, state="sink_written", attempts=1, sink_ref="forged")


@pytest.mark.parametrize("field", ["worker_id", "lease_token", "lease_until"])
def test_app_cancellation_cannot_forge_worker_evidence(notification_case, field):
    c = notification_case
    with pytest.raises(c.psycopg.errors.CheckViolation):
        with native(c, c.reviewer) as conn:
            conn.execute(
                f"UPDATE delivery_notifications SET state='cancelled',{field}='forged' WHERE id=?",
                (c.notification["notification_id"],),
            )
