"""Real restricted PG roles: opening never grants authority or completes a task."""

import pytest
from app.accounting.domain import AccountingError
from app.accounting.models import BookCreate
from app.delivery.followup_models import DeliveryGrantRequest, DeliveryRevokeRequest
from app.service_plans.service import ServicePlansService
from test_p02_reviewer_notifications_postgres import (  # noqa: F401
    p0_pg,
    p0_case,
    package_case,
    recipient_notification_case,
    plain_case,
)


def read(c, actor=None):
    return c.followups.status(actor or c.plain, c.book["id"], "2026-10")


def test_paged_delivery_feed_reaches_later_books_and_keeps_reviewer_scope(plain_case):
    c = plain_case
    added = {
        c.accounting.create_book(
            c.reviewer,
            BookCreate(
                name=f"000-分页合成-{index // 2}", period="2026-10", company_id=c.book["company_id"]
            ),
        )["id"]
        for index in range(9)
    }

    def collect(actor):
        offset, rows = 0, []
        for _ in range(30):
            value = c.followups.followups(actor, limit=2, offset=offset)
            assert value["scope"] == {"firm_id": actor.firm_id, "user_id": actor.user_id}
            assert value["pagination"]["offset"] == offset
            assert len(value["items"]) <= 2
            rows.extend(value["items"])
            following = value["pagination"]["next_offset"]
            if following is None:
                return rows
            assert offset < following <= offset + 8
            offset = following
        pytest.fail("分页未终止")

    rows = collect(c.reviewer)
    ids = [row["book_id"] for row in rows]
    assert added | {c.book["id"], c.other_book["id"]} == set(ids)
    assert len(ids) == len(set(ids))
    assert [row["book_id"] for row in collect(c.plain)] == [c.book["id"]]
    assert all(row["book_id"] == c.foreign_book["id"] for row in collect(c.foreign))
    with c.psycopg.connect(c.admin) as conn:
        conn.execute(
            "DELETE FROM accounting.book_access WHERE book_id=%s AND user_id=%s",
            (c.book["id"], c.plain.user_id),
        )
    assert collect(c.plain) == []


def test_task_target_matches_frozen_package_and_current_reviewer(plain_case):
    c = plain_case
    c.followups.plans = ServicePlansService(c.db, c.accounting, c.business)
    state = read(c)
    feed = c.followups.followups(c.plain)
    row = next(r for r in feed["items"] if r["book_id"] == c.book["id"])
    assert row["source_fingerprint"] == state["source_fingerprint"]
    assert state["scope"]["user_id"] == c.plain.user_id
    assert state["company_id"] == c.book["company_id"]
    assert state["event"]["package_id"] == c.package["id"]
    assert state["event"]["manifest_sha256"] == c.package["manifest_sha256"]
    assert state["action"] == {
        "kind": "await_customer_signature",
        "owner": "customer",
        "label": "查看签收进度",
        "can_act": False,
        "reason": "由客户本人确认签收",
    }
    assert state["signature"]["state"] == "awaiting"


def test_foreign_viewer_and_other_company_cannot_resolve_tasks(plain_case):
    c = plain_case
    for actor, book in ((c.foreign, c.book), (c.viewer, c.book), (c.plain, c.other_book)):
        with pytest.raises(AccountingError) as error:
            c.followups.status(actor, book["id"], "2026-10")
        assert error.value.status in {403, 404}


def test_open_rechecks_revocation_committed_by_another_session(plain_case):
    c = plain_case
    assert read(c)["action"]["kind"] == "await_customer_signature"
    with c.psycopg.connect(c.admin) as conn:
        conn.execute(
            "DELETE FROM accounting.book_access WHERE book_id=%s AND user_id=%s",
            (c.book["id"], c.plain.user_id),
        )
    with pytest.raises(AccountingError) as error:
        read(c)
    assert error.value.status in {403, 404}
    assert not any(r["book_id"] == c.book["id"] for r in c.followups.followups(c.plain)["items"])


def test_source_change_invalidates_target_without_signing_or_resending(plain_case):
    c = plain_case
    before = read(c)
    c.followups.configure(
        c.reviewer,
        c.book["id"],
        DeliveryGrantRequest(
            period="2026-10", recipient_user_id=c.manager.user_id, reason="另一会话重新确认收件人"
        ),
    )
    after = read(c)
    assert after["source_fingerprint"] != before["source_fingerprint"]
    with pytest.raises(AccountingError) as conflict:
        c.followups.configure(
            c.plain,
            c.book["id"],
            DeliveryGrantRequest(
                period="2026-10",
                recipient_user_id=c.manager.user_id,
                reason="拒绝旧页面覆盖",
                expected_source_fingerprint=before["source_fingerprint"],
            ),
        )
    assert conflict.value.status == 409
    assert after["signature"]["state"] == "awaiting"
    with c.db.transaction(actor=c.plain) as conn:
        task = conn.execute(
            "SELECT state,attempts FROM delivery_notifications WHERE id=?",
            (c.notification["notification_id"],),
        ).fetchone()
    assert task["state"] in {"pending", "cancelled"} and task["attempts"] == 0


def test_reviewer_recipient_form_lists_only_current_company_and_saves_bound_source(plain_case):
    c = plain_case
    c.followups.plans = ServicePlansService(c.db, c.accounting, c.business)
    c.followups.revoke(
        c.reviewer,
        c.book["id"],
        "2026-10",
        DeliveryRevokeRequest(period="2026-10", reason="本地复验撤回后重新确认联系人"),
    )
    state = read(c)
    assert state["action"]["kind"] == "fix_recipient"
    candidates = {r["user_id"] for r in state["recipient_candidates"]}
    assert c.manager.user_id in candidates
    assert c.foreign.user_id not in candidates and c.plain.user_id not in candidates
    saved = c.followups.configure(
        c.plain,
        c.book["id"],
        DeliveryGrantRequest(
            period="2026-10",
            recipient_user_id=c.manager.user_id,
            reason="按新读到的授权办理",
            expected_source_fingerprint=state["source_fingerprint"],
        ),
    )
    assert saved["recipient_user_id"] == c.manager.user_id
    assert read(c)["action"]["kind"] == "await_customer_signature"
