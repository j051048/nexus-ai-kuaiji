"""付款指令表：三权分立与 append-only 由数据库强制。"""

import sqlite3
from datetime import datetime, timezone

import pytest

from review_policy_fixtures import ensure_independent_reviewer
from test_business import business  # noqa: F401  (pytest fixture re-export)


def _insert(
    conn,
    business,
    *,
    order_id="po1",
    maker=None,
    approver=None,
    reviewer=None,
    status="draft",
    reason="",
    key="pay-1",
):
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO payment_orders(id,firm_id,company_id,book_id,period,payee,payee_account,"
        "amount_cents,idempotency_key,status,maker_user_id,approver_user_id,reviewer_user_id,"
        "escalated,reason,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            order_id,
            business.manager.firm_id,
            business.book["company_id"],
            business.book["id"],
            "2026-10",
            "供应商甲",
            "6222 0000 0000 0001",
            120_000,
            key,
            status,
            maker or business.actors["accountant"].user_id,
            approver,
            reviewer,
            0,
            reason,
            now,
            now,
        ),
    )


def test_payment_order_accepts_three_distinct_people(business):
    with business.db.transaction(write=True) as conn:
        _insert(
            conn,
            business,
            approver=ensure_independent_reviewer(
                business.db, business.actors["accountant"], business.book["id"]
            ).user_id,
        )
        row = conn.execute("SELECT status FROM payment_orders WHERE id='po1'").fetchone()
    assert row["status"] == "draft"


def test_database_refuses_maker_as_approver(business):
    with pytest.raises(sqlite3.IntegrityError):
        with business.db.transaction(write=True) as conn:
            _insert(conn, business, approver=business.actors["accountant"].user_id)


def test_database_refuses_approver_as_voucher_reviewer(business):
    with pytest.raises(sqlite3.IntegrityError):
        with business.db.transaction(write=True) as conn:
            _insert(
                conn, business, approver=business.manager.user_id, reviewer=business.manager.user_id
            )


def test_failure_needs_a_reason(business):
    with pytest.raises(sqlite3.IntegrityError):
        with business.db.transaction(write=True) as conn:
            _insert(conn, business, status="failed")


def test_idempotency_key_is_unique_per_book(business):
    with business.db.transaction(write=True) as conn:
        _insert(conn, business, order_id="po1")
    with pytest.raises(sqlite3.IntegrityError):
        with business.db.transaction(write=True) as conn:
            _insert(conn, business, order_id="po2")


def test_payment_events_are_append_only(business):
    maker = business.actors["accountant"].user_id
    now = datetime.now(timezone.utc).isoformat()
    with business.db.transaction(write=True) as conn:
        _insert(conn, business)
        conn.execute(
            "INSERT INTO payment_events(id,order_id,book_id,from_status,to_status,reason,"
            "actor_user_id,created_at) VALUES('e1','po1',?,'draft','pending_approval','',?,?)",
            (business.book["id"], maker, now),
        )
    with pytest.raises(sqlite3.IntegrityError):
        with business.db.transaction(write=True) as conn:
            conn.execute("UPDATE payment_events SET to_status='approved' WHERE id='e1'")
