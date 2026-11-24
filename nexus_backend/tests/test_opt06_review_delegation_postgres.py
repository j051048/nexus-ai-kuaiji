"""OPT06-C: PostgreSQL accepts only a currently valid delegated reviewer."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from scripts.pg_suite_target import load_suite_target, verify_suite_target

pytestmark = pytest.mark.skipif(
    os.getenv("ACCOUNTING_AK05_ISOLATED_TARGET") != "1",
    reason="需要显式 AK05 四角色隔离 PostgreSQL 运行器",
)

ADMIN_URL = os.getenv("ACCOUNTING_TEST_POSTGRES_URL")
APP_URL = os.getenv("ACCOUNTING_TEST_DATABASE_URL")


@pytest.fixture(scope="module")
def pg():
    import psycopg
    from scripts.migrate_accounting import migrate

    target = load_suite_target()
    if target is None:
        pytest.skip("OPT06-C PG requires an explicit isolated PostgreSQL runner")
    target = verify_suite_target(target)
    assert (ADMIN_URL, APP_URL) == (target["admin"], target["app"])
    migrate(target["admin"])
    yield psycopg


def test_active_delegation_allows_review_and_expiry_revokes_it(pg):
    psycopg = pg
    from app.accounting.database import Database
    from app.accounting.domain import Actor
    from app.accounting.models import BookCreate
    from app.accounting.service import AccountingService

    suffix = uuid4().hex[:10]
    owner = Actor(f"opt06c-owner-{suffix}", f"opt06c-firm-{suffix}")
    delegate = Actor(f"opt06c-delegate-{suffix}", owner.firm_id)
    with psycopg.connect(ADMIN_URL) as conn:
        conn.execute(
            "INSERT INTO accounting.users(id,name,email) VALUES(%s,%s,%s),(%s,%s,%s)",
            (
                owner.user_id,
                owner.user_id,
                f"{owner.user_id}@example.test",
                delegate.user_id,
                delegate.user_id,
                f"{delegate.user_id}@example.test",
            ),
        )
        conn.execute(
            "INSERT INTO accounting.firms(id,name,created_by) VALUES(%s,%s,%s)",
            (owner.firm_id, owner.firm_id, owner.user_id),
        )
        conn.execute(
            "INSERT INTO accounting.memberships(firm_id,user_id,role) VALUES(%s,%s,'owner'),(%s,%s,'operator')",
            (owner.firm_id, owner.user_id, owner.firm_id, delegate.user_id),
        )
    db = Database(APP_URL)
    try:
        book = AccountingService(db).create_book(
            owner, BookCreate(name="OPT06-C book", period="2026-10")
        )
        now = datetime.now(timezone.utc)
        with psycopg.connect(ADMIN_URL) as conn:
            conn.execute(
                "INSERT INTO accounting.book_access(book_id,firm_id,user_id,role) VALUES(%s,%s,%s,'editor')",
                (book["id"], owner.firm_id, delegate.user_id),
            )
            conn.execute(
                "INSERT INTO accounting.book_review_delegations("
                "id,firm_id,company_id,book_id,duty,from_user_id,to_user_id,starts_at,ends_at,"
                "status,reason,requested_by,approved_by,approved_at,created_at) "
                "VALUES(%s,%s,%s,%s,'reviewer',%s,%s,%s,%s,'active',%s,%s,%s,%s,%s)",
                (
                    "delegation-" + suffix,
                    owner.firm_id,
                    book["company_id"],
                    book["id"],
                    owner.user_id,
                    delegate.user_id,
                    (now - timedelta(hours=1)).isoformat(),
                    (now + timedelta(hours=2)).isoformat(),
                    "复核人休假期间的受控替补安排",
                    owner.user_id,
                    owner.user_id,
                    now.isoformat(),
                    now.isoformat(),
                ),
            )
        with psycopg.connect(APP_URL) as conn:
            conn.execute("SET ROLE accounting_app")
            conn.execute("SET search_path TO accounting,pg_catalog")
            conn.execute(
                "SELECT set_config('app.user_id',%s,false),set_config('app.firm_id',%s,false)",
                (delegate.user_id, owner.firm_id),
            )
            assert (
                conn.execute("SELECT accounting.book_review_allowed(%s)", (book["id"],)).fetchone()[
                    0
                ]
                is True
            )
        with psycopg.connect(ADMIN_URL) as conn:
            conn.execute(
                "UPDATE accounting.book_review_delegations SET ends_at=%s WHERE id=%s",
                ((now - timedelta(seconds=1)).isoformat(), "delegation-" + suffix),
            )
        with psycopg.connect(APP_URL) as conn:
            conn.execute("SET ROLE accounting_app")
            conn.execute("SET search_path TO accounting,pg_catalog")
            conn.execute(
                "SELECT set_config('app.user_id',%s,false),set_config('app.firm_id',%s,false)",
                (delegate.user_id, owner.firm_id),
            )
            assert (
                conn.execute("SELECT accounting.book_review_allowed(%s)", (book["id"],)).fetchone()[
                    0
                ]
                is False
            )
    finally:
        db.close()
