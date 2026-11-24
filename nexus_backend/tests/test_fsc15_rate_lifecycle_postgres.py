"""FSC15: PostgreSQL proof for versioned repair-rate lifecycle."""

from __future__ import annotations

import os
from uuid import uuid4

import pytest

from app.accounting.database import Database
from app.accounting.domain import AccountingError
from app.accounting.models import BookCreate
from app.accounting.service import AccountingService, Actor
from app.tax.costs import RepairRateDeactivate, RepairRateDraft, RepairRateService
from scripts.pg_suite_target import load_suite_target, verify_suite_target

ADMIN_URL = os.getenv("ACCOUNTING_TEST_POSTGRES_URL")
APP_URL = os.getenv("ACCOUNTING_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    os.getenv("ACCOUNTING_AK05_ISOLATED_TARGET") != "1",
    reason="需要显式 AK05 四角色隔离 PostgreSQL 运行器",
)


@pytest.fixture(scope="module")
def pg():
    import psycopg
    from scripts.migrate_accounting import migrate

    target = load_suite_target()
    if target is None:
        pytest.skip("FSC15 PG requires an explicit isolated PostgreSQL runner")
    target = verify_suite_target(target)
    assert (ADMIN_URL, APP_URL) == (target["admin"], target["app"])
    migrate(target["admin"])
    database = Database(target["app"])
    yield database, psycopg
    database.close()


@pytest.fixture
def case(pg):
    db, psycopg = pg
    suffix = uuid4().hex[:12]
    firm = "fsc15-firm-" + suffix
    maker = Actor(user_id=f"fsc15-maker-{suffix}", firm_id=firm)
    reviewer = Actor(user_id=f"fsc15-reviewer-{suffix}", firm_id=firm)
    with psycopg.connect(ADMIN_URL) as conn:
        for actor, role in ((maker, "owner"), (reviewer, "reviewer")):
            conn.execute(
                "INSERT INTO accounting.users(id,name,email) VALUES(%s,%s,%s)",
                (actor.user_id, role, f"fsc15-{role}-{suffix}@example.test"),
            )
        conn.execute(
            "INSERT INTO accounting.firms(id,name,created_by) VALUES(%s,%s,%s)",
            (firm, "虚构FSC15事务所", maker.user_id),
        )
        conn.execute(
            "INSERT INTO accounting.memberships(firm_id,user_id,role) VALUES(%s,%s,'owner')",
            (firm, maker.user_id),
        )
        conn.execute(
            "INSERT INTO accounting.memberships(firm_id,user_id,role) VALUES(%s,%s,'reviewer')",
            (firm, reviewer.user_id),
        )
    book = AccountingService(db).create_book(
        maker, BookCreate(name="虚构FSC15账套", period="2026-10")
    )
    with psycopg.connect(ADMIN_URL) as conn:
        conn.execute(
            "INSERT INTO accounting.book_access(book_id,firm_id,user_id,role) VALUES(%s,%s,%s,'reviewer')",
            (book["id"], firm, reviewer.user_id),
        )
    return db, maker, reviewer, book


def _draft(vehicle_type: str, start: str = "2026-10", end: str = "2026-12") -> RepairRateDraft:
    return RepairRateDraft(
        vehicle_type=vehicle_type,
        rate_cents=5000,
        valid_from=start,
        valid_to=end,
        coverage_scope="基础保养（不含轮胎）",
        source_ref="客户核定费率/维修工单",
        unit="per_vehicle",
        currency="CNY",
        tax_included=False,
        reason="客户核定 2026 年基础保养费率",
    )


def test_pg_rate_lifecycle_requires_independent_confirmation(case):
    db, maker, reviewer, book = case
    service = RepairRateService(db)
    vehicle_type = "出租车-PG-" + uuid4().hex[:8]
    draft = service.create_draft(maker, book["id"], _draft(vehicle_type))
    assert draft["status"] == "draft" and draft["version"] == 1

    with pytest.raises(AccountingError) as denied:
        service.confirm(maker, book["id"], draft["id"], 1)
    assert denied.value.status == 409

    confirmed = service.confirm(reviewer, book["id"], draft["id"], 1)
    assert confirmed["status"] == "confirmed"
    active = service.activate(maker, book["id"], draft["id"], 1)
    assert active["status"] == "active"
    history = service.history(maker, book["id"], active["logical_id"])
    assert [row["status"] for row in history["items"]] == ["active"]

    inactive = service.deactivate(
        maker,
        book["id"],
        draft["id"],
        RepairRateDeactivate(expected_version=1, reason="客户停止使用该车型费率"),
    )
    assert inactive["status"] == "inactive"


def test_pg_rate_lifecycle_rejects_overlapping_active_rate(case):
    db, maker, reviewer, book = case
    service = RepairRateService(db)
    vehicle_type = "出租车-PG-" + uuid4().hex[:8]
    first = service.create_draft(maker, book["id"], _draft(vehicle_type, "2026-10", "2026-12"))
    service.confirm(reviewer, book["id"], first["id"], 1)
    service.activate(maker, book["id"], first["id"], 1)

    second = service.create_draft(maker, book["id"], _draft(vehicle_type, "2026-11", "2026-12"))
    service.confirm(reviewer, book["id"], second["id"], 1)
    with pytest.raises(AccountingError) as overlap:
        service.activate(maker, book["id"], second["id"], 1)
    assert overlap.value.status == 409


def test_pg_rate_lifecycle_accepts_cross_year_window(case):
    db, maker, reviewer, book = case
    service = RepairRateService(db)
    vehicle_type = "出租车-PG-" + uuid4().hex[:8]
    draft = service.create_draft(maker, book["id"], _draft(vehicle_type, "2026-12", "2027-02"))
    service.confirm(reviewer, book["id"], draft["id"], 1)
    service.activate(maker, book["id"], draft["id"], 1)
    assert service.resolve(maker, book["id"], vehicle_type, "2027-01")["id"] == draft["id"]
