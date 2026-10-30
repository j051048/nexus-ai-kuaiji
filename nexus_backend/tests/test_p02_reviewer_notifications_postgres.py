"""P02 ordinary firm reviewers use bounded recipient checks, never broad RLS reads.

Synthetic identities and a genuinely locked/reviewed package use existing N03
fixtures. The caller tested here is a firm reviewer (not an admin alias).
"""

from uuid import uuid4

import pytest

from app.accounting.domain import AccountingError, Actor
from app.delivery.followup_models import DeliveryGrantRequest, DeliveryTaskRequest
from app.delivery.followups import DeliveryFollowupService
from app.delivery.monthly_models import MonthlyPackageAcknowledge
from test_n03_notification_postgres import (  # noqa: F401
    p0_pg,
    p0_case,
    package_case,
    recipient_notification_case,
)


@pytest.fixture
def plain_case(recipient_notification_case):
    c = recipient_notification_case
    _add_plain_reviewers(c)
    return c


def _add_plain_reviewers(c):
    c.plain = Actor("p02-plain-reviewer-" + uuid4().hex, c.reviewer.firm_id)
    c.viewer = Actor("p02-viewer-" + uuid4().hex, c.reviewer.firm_id)
    with c.psycopg.connect(c.admin) as conn:
        for actor, firm_role, book_role in (
            (c.plain, "reviewer", "reviewer"),
            (c.viewer, "operator", "viewer"),
        ):
            conn.execute(
                "INSERT INTO accounting.users(id,name,email) VALUES(%s,'P02角色测试',%s)",
                (actor.user_id, actor.user_id + "@example.test"),
            )
            conn.execute(
                "INSERT INTO accounting.memberships(firm_id,user_id,role) VALUES(%s,%s,%s)",
                (actor.firm_id, actor.user_id, firm_role),
            )
            conn.execute(
                "INSERT INTO accounting.book_access(book_id,firm_id,user_id,role) VALUES(%s,%s,%s,%s)",
                (c.book["id"], actor.firm_id, actor.user_id, book_role),
            )


def _configure(c, actor=None, recipient=None, book=None):
    return c.followups.configure(
        actor or c.plain,
        book or c.book["id"],
        DeliveryGrantRequest(
            period="2026-10",
            recipient_user_id=recipient or c.manager.user_id,
            reason="普通复核员确认已授权客户联系人",
        ),
    )


def _queue(c, actor=None):
    return c.followups.queue(
        actor or c.plain,
        c.book["id"],
        DeliveryTaskRequest(
            period="2026-10",
            kind="signature_reminder",
            reason="本地测试排队，不实际外发",
        ),
    )


def _member(c, actor, book=None, recipient=None):
    with c.db.transaction(actor=actor) as conn:
        return conn.execute(
            "SELECT accounting.delivery_recipient_member(?,?)",
            (book or c.book["id"], recipient or c.manager.user_id),
        ).fetchone()[0]


def test_plain_reviewer_configures_queues_and_reads_without_directory_disclosure(plain_case):
    c = plain_case
    with c.db.transaction(actor=c.plain) as conn:
        assert (
            conn.execute(
                "SELECT role FROM memberships WHERE firm_id=? AND user_id=?",
                (c.plain.firm_id, c.plain.user_id),
            ).fetchone()[0]
            == "reviewer"
        )
        assert (
            conn.execute(
                "SELECT user_id FROM company_memberships WHERE company_id=?",
                (c.book["company_id"],),
            ).fetchall()
            == []
        )
    granted = _configure(c)
    assert granted["recipient_user_id"] == c.manager.user_id
    queued = _queue(c)
    assert queued["state"] == "pending"
    state = c.followups.status(c.plain, c.book["id"], "2026-10")
    assert state["recipient"]["state"] == "valid"
    assert "recipient_invalid" not in state["followup"]["reasons"]
    assert state["notification"]["pending"] == 1
    with c.db.transaction(actor=c.plain) as conn:
        assert (
            conn.execute(
                "SELECT user_id FROM company_memberships WHERE company_id=?",
                (c.book["company_id"],),
            ).fetchall()
            == []
        )


def test_plain_reviewer_existing_grant_status_is_not_falsely_invalid(plain_case):
    c = plain_case
    assert c.followups.status(c.plain, c.book["id"], "2026-10")["recipient"]["state"] == "valid"
    item = next(
        row for row in c.followups.followups(c.plain)["items"] if row["book_id"] == c.book["id"]
    )
    assert "recipient_invalid" not in item["reasons"]


def test_reviewer_can_configure_before_service_plan_or_package_exists(p0_case):
    c = p0_case
    _add_plain_reviewers(c)
    c.followups = DeliveryFollowupService(c.db)
    configured = _configure(c)
    assert configured["recipient_user_id"] == c.manager.user_id
    status = c.followups.status(c.plain, c.book["id"], "2026-10")
    assert status["recipient"]["state"] == "valid"
    assert status["event"]["state"] == "missing"
    with pytest.raises(AccountingError) as error:
        _queue(c)
    assert error.value.status == 409


def test_native_boolean_is_scoped_and_never_enumerates_other_books(plain_case):
    c = plain_case
    assert _member(c, c.plain) is True
    assert _member(c, c.viewer) is False
    assert _member(c, c.foreign) is False
    assert _member(c, c.plain, book=c.other_book["id"]) is False
    assert _member(c, c.plain, book=c.foreign_book["id"]) is False
    assert _member(c, c.plain, recipient=c.foreign.user_id) is False
    assert _member(c, c.plain, recipient="nonexistent") is False


@pytest.mark.parametrize("operation", ["configure", "queue", "status"])
def test_viewer_cannot_use_notification_management(plain_case, operation):
    c = plain_case
    with pytest.raises(AccountingError) as error:
        if operation == "configure":
            _configure(c, c.viewer)
        elif operation == "queue":
            _queue(c, c.viewer)
        else:
            c.followups.status(c.viewer, c.book["id"], "2026-10")
    assert error.value.status == 403


def test_foreign_recipient_and_inaccessible_book_are_denied(plain_case):
    c = plain_case
    with pytest.raises(AccountingError) as error:
        _configure(c, recipient=c.foreign.user_id)
    assert error.value.status == 422
    for book in (c.other_book["id"], c.foreign_book["id"]):
        with pytest.raises(AccountingError) as error:
            _configure(c, book=book)
        assert error.value.status == 404


def test_notification_configuration_never_confers_customer_signature(plain_case):
    c = plain_case
    _configure(c)
    for actor in (c.plain, c.reviewer):
        with pytest.raises(AccountingError) as error:
            c.monthly.acknowledge(
                actor,
                c.book["id"],
                c.package["id"],
                MonthlyPackageAcknowledge(
                    manifest_sha256=c.package["manifest_sha256"],
                    reason="复核员和制包人不得代客户签收",
                ),
            )
        assert error.value.status == 403
    assert c.followups.status(c.plain, c.book["id"], "2026-10")["signature"]["state"] == "awaiting"


@pytest.mark.parametrize(
    "mutation",
    ["inactive_recipient", "removed_membership", "recipient_book_revoked", "reviewer_revoked"],
)
def test_current_authorization_is_rechecked_without_cached_recipient_results(plain_case, mutation):
    c = plain_case
    assert c.followups.status(c.plain, c.book["id"], "2026-10")["recipient"]["state"] == "valid"
    with c.psycopg.connect(c.admin) as conn:
        if mutation == "inactive_recipient":
            conn.execute("UPDATE accounting.users SET active=0 WHERE id=%s", (c.manager.user_id,))
        elif mutation == "removed_membership":
            conn.execute(
                "DELETE FROM accounting.company_memberships WHERE company_id=%s AND user_id=%s",
                (c.book["company_id"], c.manager.user_id),
            )
        else:
            actor = c.plain if mutation == "reviewer_revoked" else c.manager
            conn.execute(
                "DELETE FROM accounting.book_access WHERE book_id=%s AND user_id=%s",
                (c.book["id"], actor.user_id),
            )
    if mutation == "reviewer_revoked":
        assert _member(c, c.plain) is False
        with pytest.raises(AccountingError) as error:
            _queue(c)
        assert error.value.status == 404
    else:
        assert (
            c.followups.status(c.plain, c.book["id"], "2026-10")["recipient"]["state"] == "invalid"
        )
        with pytest.raises(AccountingError) as error:
            _queue(c)
        assert error.value.status == 409


def test_new_boolean_function_has_only_app_execute_permission(plain_case):
    c = plain_case
    with c.psycopg.connect(c.admin) as conn:
        assert conn.execute(
            "SELECT has_function_privilege('accounting_app','accounting.delivery_recipient_member(text,text)','EXECUTE')"
        ).fetchone()[0]
        for role in ("accounting_system", "accounting_worker", "accounting_projection"):
            assert not conn.execute(
                "SELECT has_function_privilege(%s,'accounting.delivery_recipient_member(text,text)','EXECUTE')",
                (role,),
            ).fetchone()[0]
        assert conn.execute(
            "SELECT proconfig FROM pg_proc WHERE oid='accounting.delivery_recipient_member(text,text)'::regprocedure"
        ).fetchone()[0] == ["search_path=pg_catalog, accounting, pg_temp"]
