"""Restricted roles and real concurrent sessions; synthetic identity and transport."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import pytest
from app.accounting.domain import AccountingError
from app.accounting.database import Database
from app.accounting.models import ApprovalRequest
from app.accounting.review_policy import ReviewPolicyService
from app.accounting.review_policy_models import ReviewPolicyChange
from app.delivery.receipts import ProviderReceiptService, sign
from test_admin_identity_postgres import accounts, case, postgres, peer, ADMIN_URL  # noqa: F401
from test_admin_identity import request
from test_postgres_integration import create_draft, APP_URL
from test_tasks0406_invites import (  # noqa: F401
    Mail,
    send,
    test_invite_send_is_bound_private_and_never_grants_membership,
    test_invite_tampering_or_stale_link_never_sends,
    test_unknown_invite_is_not_resent_even_with_new_key,
    test_proven_unsent_invite_allows_only_explicit_bounded_new_attempt,
    test_crash_after_send_marker_is_not_retried_after_restart,
    test_restore_hold_blocks_new_invites_and_restart_keeps_unknown,
    test_expired_and_budgeted_invites_never_send_again,
    test_invitation_disabled_by_default_creates_no_attempt,
)
from test_next_p0_receipts_postgres import callback, p0_pg, p0_case, package_case, notification_case  # noqa: F401

pytestmark = pytest.mark.skipif(
    not ADMIN_URL or not APP_URL, reason="需要显式本地隔离 PostgreSQL 配置"
)


def test_concurrent_invitation_clicks_send_once(accounts):
    a, mail, barrier = accounts, Mail(), Barrier(2)
    result = a.service.create(a.case.actor, request(a))

    def race(_):
        barrier.wait(timeout=10)
        return send(a, result, mail)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(race, range(2)))
    assert len(mail.calls) == 1
    assert results[0]["id"] == results[1]["id"]


def test_invitation_transport_holds_no_write_lock(accounts):
    a = accounts
    result = a.service.create(a.case.actor, request(a))

    def cancel_from_other_session():
        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(a.service.cancel, a.case.actor, result["request"]["id"]).result(timeout=5)

    assert send(a, result, Mail(cancel_from_other_session))["state"] == "accepted"
    assert a.service.list_requests(a.case.actor)["requests"][0]["status"] == "cancelled"


def test_native_invitation_evidence_is_scoped_and_immutable(accounts, case):
    a = accounts
    r = a.service.create(a.case.actor, request(a))
    delivery = send(a, r, Mail())
    foreign = peer(case, firm_role="owner", other_firm=True)
    with a.case.db.transaction(actor=foreign) as conn:
        assert not conn.execute(
            "SELECT * FROM account_invitation_deliveries WHERE id=?", (delivery["id"],)
        ).fetchall()
    for sql in (
        "UPDATE account_invitation_deliveries SET state='not_sent' WHERE id=?",
        "UPDATE account_invitation_deliveries SET recipient_email='wrong@example.test' WHERE id=?",
        "DELETE FROM account_invitation_deliveries WHERE id=?",
    ):
        with pytest.raises(Exception):
            with a.case.db.transaction(write=True, actor=a.case.actor) as conn:
                conn.execute(sql, (delivery["id"],))


def test_pg_review_stats_remain_separate_after_policy_change(case):
    c = case
    first = create_draft(c, external_id="independent")
    c.service.approve(
        c.reviewer, c.book, first["id"], ApprovalRequest(expected_version=first["version"])
    )
    policy = ReviewPolicyService(c.db)
    policy.configure(
        c.actor,
        c.book,
        ReviewPolicyChange(expected_version=0, mode="single_operator", reason="虚构单人场景"),
    )
    second = create_draft(c, external_id="single")
    if second["id"] == first["id"]:
        second = next(
            d for d in c.service.get_book(c.actor, c.book)["drafts"] if d["status"] == "draft"
        )
    c.service.approve(
        c.actor,
        c.book,
        second["id"],
        ApprovalRequest(
            expected_version=second["version"],
            expected_policy_version=1,
            review_reason="单人核对原件",
        ),
    )
    s = policy.get(c.actor, c.book)["review_summary"]
    assert s == {
        "independent": 1,
        "single_operator": 1,
        "unverified": 0,
        "total_reviewed": 2,
        "single_operator_ratio": "0.5000",
    }
    policy.configure(
        c.actor,
        c.book,
        ReviewPolicyChange(expected_version=1, mode="independent", reason="恢复双人办理"),
    )
    assert policy.get(c.actor, c.book)["review_summary"] == s


def test_pg_receipts_bound_and_revoked_viewer_denied(callback):
    c, raw, stamp, event_id = callback
    worker_db = Database(c.worker_url)
    try:
        ProviderReceiptService(worker_db).receive(raw, stamp, sign(raw, stamp), event_id)
    finally:
        worker_db.close()
    view = ProviderReceiptService(c.db).timeline(
        c.reviewer, c.book["id"], c.notification["notification_id"]
    )
    assert view["package_id"] == c.package["id"] and view["accepted"]
    assert not view["customer_opened"] and not view["signed"] and not view["can_retry"]
    with pytest.raises(AccountingError):
        ProviderReceiptService(c.db).timeline(
            c.foreign, c.book["id"], c.notification["notification_id"]
        )
    with c.psycopg.connect(c.admin) as conn:
        conn.execute(
            "DELETE FROM accounting.book_access WHERE book_id=%s AND user_id=%s",
            (c.book["id"], c.reviewer.user_id),
        )
        conn.execute(
            "UPDATE accounting.memberships SET role='viewer' WHERE firm_id=%s AND user_id=%s",
            (c.reviewer.firm_id, c.reviewer.user_id),
        )
    with pytest.raises(AccountingError):
        ProviderReceiptService(c.db).timeline(
            c.reviewer, c.book["id"], c.notification["notification_id"]
        )


def test_invitation_revocation_during_send_cannot_publish_or_resend(accounts, case):
    a = accounts
    peer(case, firm_role="owner")
    result = a.service.create(a.case.actor, request(a))

    def revoke():
        with a.case.psycopg.connect(ADMIN_URL) as conn:
            conn.execute(
                "UPDATE accounting.users SET active=0 WHERE id=%s", (a.case.actor.user_id,)
            )

    mail = Mail(revoke)
    with pytest.raises(AccountingError):
        send(a, result, mail)
    with a.case.psycopg.connect(ADMIN_URL) as conn:
        assert (
            conn.execute(
                "SELECT state FROM accounting.account_invitation_deliveries WHERE request_id=%s",
                (result["request"]["id"],),
            ).fetchone()[0]
            == "sending"
        )
    with pytest.raises(AccountingError):
        send(a, result, Mail(), key="after-revocation")
    assert len(mail.calls) == 1
