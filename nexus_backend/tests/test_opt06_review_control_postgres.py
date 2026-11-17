"""OPT06-A: direct PostgreSQL writes cannot bypass a firm ban."""

from __future__ import annotations

import os
from uuid import uuid4

import pytest

from scripts.pg_suite_target import load_suite_target, verify_suite_target

ADMIN_URL = os.getenv("ACCOUNTING_TEST_POSTGRES_URL")
APP_URL = os.getenv("ACCOUNTING_TEST_DATABASE_URL")


@pytest.fixture(scope="module")
def pg():
    import psycopg
    from scripts.migrate_accounting import migrate

    target = load_suite_target()
    if target is None:
        pytest.skip("OPT06-A PG requires an explicit isolated PostgreSQL runner")
    target = verify_suite_target(target)
    assert (ADMIN_URL, APP_URL) == (target["admin"], target["app"])
    migrate(target["admin"])
    yield psycopg


def test_direct_policy_insert_is_rejected(pg):
    psycopg = pg
    from app.accounting.database import Database
    from app.accounting.domain import Actor
    from app.accounting.models import BookCreate
    from app.accounting.service import AccountingService

    suffix = uuid4().hex[:10]
    owner = Actor(f"opt06-owner-{suffix}", f"opt06-firm-{suffix}")
    with psycopg.connect(ADMIN_URL) as conn:
        conn.execute(
            "INSERT INTO accounting.users(id,name,email) VALUES(%s,%s,%s)",
            (owner.user_id, owner.user_id, f"{owner.user_id}@example.test"),
        )
        conn.execute(
            "INSERT INTO accounting.firms(id,name,created_by) VALUES(%s,%s,%s)",
            (owner.firm_id, owner.firm_id, owner.user_id),
        )
        conn.execute(
            "INSERT INTO accounting.memberships(firm_id,user_id,role) VALUES(%s,%s,'owner')",
            (owner.firm_id, owner.user_id),
        )
    db = Database(APP_URL)
    try:
        book = AccountingService(db).create_book(
            owner, BookCreate(name="OPT06 book", period="2026-10")
        )
        with psycopg.connect(ADMIN_URL) as conn:
            conn.execute(
                "INSERT INTO accounting.firm_review_controls"
                "(firm_id,single_operator_disabled,version,reason,updated_by,updated_at) "
                "VALUES(%s,TRUE,1,'集团要求所有账套保持独立复核，不得单人办理',%s,now()::text)",
                (owner.firm_id, owner.user_id),
            )
        with psycopg.connect(APP_URL) as conn:
            conn.execute("SET ROLE accounting_app")
            conn.execute("SET search_path TO accounting,pg_catalog")
            conn.execute(
                "SELECT set_config('app.user_id',%s,false),set_config('app.firm_id',%s,false)",
                (owner.user_id, owner.firm_id),
            )
            ledger_id = conn.execute(
                "SELECT ledger_id FROM accounting.ledger_periods WHERE book_id=%s", (book["id"],)
            ).fetchone()[0]
            with pytest.raises(psycopg.errors.CheckViolation, match="single_operator disabled"):
                conn.execute(
                    "INSERT INTO accounting.ledger_review_policies"
                    "(ledger_id,version,firm_id,company_id,mode,reason,confirmed_by,created_at) "
                    "VALUES(%s,1,%s,%s,'single_operator','直接写库绕过失败',%s,now()::text)",
                    (ledger_id, owner.firm_id, book["company_id"], owner.user_id),
                )
    finally:
        db.close()


def test_separation_removes_manager_review_but_keeps_non_manager_reviewer(pg):
    psycopg = pg
    from app.accounting.database import Database
    from app.accounting.domain import Actor
    from app.accounting.models import BookCreate
    from app.accounting.service import AccountingService

    suffix = uuid4().hex[:10]
    owner = Actor(f"opt06b-owner-{suffix}", f"opt06b-firm-{suffix}")
    reviewer = Actor(f"opt06b-reviewer-{suffix}", owner.firm_id)
    manager2 = Actor(f"opt06b-manager2-{suffix}", owner.firm_id)
    with psycopg.connect(ADMIN_URL) as conn:
        conn.execute(
            "INSERT INTO accounting.users(id,name,email) VALUES(%s,%s,%s),(%s,%s,%s),(%s,%s,%s)",
            (
                owner.user_id,
                owner.user_id,
                f"{owner.user_id}@example.test",
                reviewer.user_id,
                reviewer.user_id,
                f"{reviewer.user_id}@example.test",
                manager2.user_id,
                manager2.user_id,
                f"{manager2.user_id}@example.test",
            ),
        )
        conn.execute(
            "INSERT INTO accounting.firms(id,name,created_by) VALUES(%s,%s,%s)",
            (owner.firm_id, owner.firm_id, owner.user_id),
        )
        conn.execute(
            "INSERT INTO accounting.memberships(firm_id,user_id,role) VALUES(%s,%s,'owner'),(%s,%s,'reviewer'),(%s,%s,'admin')",
            (
                owner.firm_id,
                owner.user_id,
                owner.firm_id,
                reviewer.user_id,
                owner.firm_id,
                manager2.user_id,
            ),
        )
    db = Database(APP_URL)
    try:
        book = AccountingService(db).create_book(
            owner, BookCreate(name="OPT06-B book", period="2026-10")
        )
        with psycopg.connect(ADMIN_URL) as conn:
            conn.execute(
                "INSERT INTO accounting.book_access(book_id,firm_id,user_id,role) VALUES(%s,%s,%s,'reviewer')",
                (book["id"], owner.firm_id, reviewer.user_id),
            )
            conn.execute(
                "INSERT INTO accounting.firm_review_controls"
                "(firm_id,single_operator_disabled,management_review_separation,version,reason,updated_by,updated_at) "
                "VALUES(%s,FALSE,TRUE,1,'集团要求管理与复核分离',%s,now()::text)",
                (owner.firm_id, owner.user_id),
            )
        with psycopg.connect(ADMIN_URL) as conn:
            with pytest.raises(
                psycopg.errors.InsufficientPrivilege, match="manager reviewer grants"
            ):
                conn.execute(
                    "INSERT INTO accounting.book_access(book_id,firm_id,user_id,role) VALUES(%s,%s,%s,'reviewer')",
                    (book["id"], owner.firm_id, manager2.user_id),
                )
        with psycopg.connect(APP_URL) as conn:
            conn.execute("SET ROLE accounting_app")
            conn.execute("SET search_path TO accounting,pg_catalog")
            for actor, expected in ((owner, False), (reviewer, True)):
                conn.execute(
                    "SELECT set_config('app.user_id',%s,false),set_config('app.firm_id',%s,false)",
                    (actor.user_id, actor.firm_id),
                )
                row = conn.execute(
                    "SELECT can_review FROM accounting.rr_book_permissions WHERE book_id=%s AND user_id=%s",
                    (book["id"], actor.user_id),
                ).fetchone()
                assert row == (expected,), (actor.user_id, row)
    finally:
        db.close()
