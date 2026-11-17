"""FSC16: PostgreSQL proof for repair package contracts and quota ledger."""

from __future__ import annotations

import os
import threading
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest

from app.accounting.database import Database
from app.accounting.domain import AccountingError
from app.accounting.models import BookCreate
from app.accounting.service import AccountingService, Actor
from app.tax.packages import (
    PackageConsume,
    PackageContractDraft,
    PackageRelease,
    RepairPackageService,
)
from scripts.pg_suite_target import load_suite_target, verify_suite_target

ADMIN_URL = os.getenv("ACCOUNTING_TEST_POSTGRES_URL")
APP_URL = os.getenv("ACCOUNTING_TEST_DATABASE_URL")
pytestmark = []



@pytest.fixture(scope="module")
def pg():
    import psycopg
    from scripts.migrate_accounting import migrate

    target = load_suite_target()
    if target is None:
        pytest.skip("FSC16 PG requires an explicit isolated PostgreSQL runner")
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
    firm = "fsc16-firm-" + suffix
    maker = Actor(user_id=f"fsc16-maker-{suffix}", firm_id=firm)
    reviewer = Actor(user_id=f"fsc16-reviewer-{suffix}", firm_id=firm)
    with psycopg.connect(ADMIN_URL) as conn:
        for actor, role in ((maker, "owner"), (reviewer, "reviewer")):
            conn.execute(
                "INSERT INTO accounting.users(id,name,email) VALUES(%s,%s,%s)",
                (actor.user_id, role, f"fsc16-{role}-{suffix}@example.test"),
            )
        conn.execute(
            "INSERT INTO accounting.firms(id,name,created_by) VALUES(%s,%s,%s)",
            (firm, "虚构FSC16事务所", maker.user_id),
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
        maker, BookCreate(name="虚构FSC16账套", period="2026-10")
    )
    with psycopg.connect(ADMIN_URL) as conn:
        conn.execute(
            "INSERT INTO accounting.book_access(book_id,firm_id,user_id,role) VALUES(%s,%s,%s,'reviewer')",
            (book["id"], firm, reviewer.user_id),
        )
    return db, maker, reviewer, book


def _contract(contract_no: str, vehicles: list[str] | None = None) -> PackageContractDraft:
    return PackageContractDraft(
        contract_no=contract_no,
        coverage_scope="出租车基础保养包干（不含轮胎与事故维修）",
        vehicle_external_ids=vehicles or ["V1", "V2"],
        valid_from="2026-10",
        valid_to="2026-12",
        limit_cents=10000,
        policy="contract_cumulative",
        reason="客户核定年度包干合同",
    )


def _consume(key: str, amount: int = 6000, period: str = "2026-10") -> PackageConsume:
    return PackageConsume(
        source_key=key,
        source_version=1,
        period=period,
        amount_cents=amount,
        vehicle_external_id="V1",
        idempotency_key=key,
    )


def _active_contract(case, service: RepairPackageService):
    db, maker, reviewer, book = case
    contract = service.create_draft(maker, book["id"], _contract("PKG-PG-" + uuid4().hex[:8]))
    with pytest.raises(AccountingError) as denied:
        service.confirm(maker, book["id"], contract["id"], 1)
    assert denied.value.status == 409
    service.confirm(reviewer, book["id"], contract["id"], 1)
    return service.activate(maker, book["id"], contract["id"], 1)


def test_pg_package_quota_consume_release_and_idempotency(case):
    db, maker, reviewer, book = case
    service = RepairPackageService(db)
    contract = _active_contract(case, service)

    first = service.consume(maker, book["id"], contract["id"], _consume("consume-1"))
    assert first["amount_cents"] == 6000
    replay = service.consume(maker, book["id"], contract["id"], _consume("consume-1"))
    assert replay["replayed"] is True
    with pytest.raises(AccountingError) as over:
        service.consume(maker, book["id"], contract["id"], _consume("consume-2", 5000))
    assert over.value.status == 409

    summary = service.summary(reviewer, book["id"], contract["id"])
    assert summary["consumed_cents"] == 6000
    assert summary["remaining_cents"] == 4000

    release = service.release(
        maker,
        book["id"],
        first["id"],
        PackageRelease(amount_cents=3000, reason="客户退回部分包干服务", idempotency_key="release-1"),
    )
    assert release["kind"] == "release"
    summary = service.summary(reviewer, book["id"], contract["id"])
    assert summary["consumed_cents"] == 3000
    assert summary["remaining_cents"] == 7000

    with pytest.raises(AccountingError) as too_much:
        service.release(
            maker,
            book["id"],
            first["id"],
            PackageRelease(amount_cents=4000, reason="超额释放测试", idempotency_key="release-2"),
        )
    assert too_much.value.status == 409


def test_pg_package_rejects_out_of_scope_vehicle_and_cross_book(case):
    db, maker, reviewer, book = case
    service = RepairPackageService(db)
    contract = _active_contract(case, service)

    with pytest.raises(AccountingError) as outside:
        service.consume(
            maker,
            book["id"],
            contract["id"],
            PackageConsume(
                source_key="outside",
                source_version=1,
                period="2026-10",
                amount_cents=100,
                vehicle_external_id="V9",
                idempotency_key="outside",
            ),
        )
    assert outside.value.status == 409

    other = AccountingService(db).create_book(
        maker, BookCreate(name="虚构FSC16第二账套", period="2026-10")
    )
    with pytest.raises(AccountingError) as missing:
        service.summary(maker, other["id"], contract["id"])
    assert missing.value.status == 404


def test_pg_package_rejects_cross_firm_access(case):
    db, maker, reviewer, book = pg_case = case
    import psycopg
    from uuid import uuid4

    suffix = uuid4().hex[:12]
    firm_b = "fsc16-other-firm-" + suffix
    actor_b = Actor(user_id=f"fsc16-other-{suffix}", firm_id=firm_b)
    with psycopg.connect(ADMIN_URL) as conn:
        conn.execute(
            "INSERT INTO accounting.users(id,name,email) VALUES(%s,%s,%s)",
            (actor_b.user_id, "other", f"fsc16-other-{suffix}@example.test"),
        )
        conn.execute(
            "INSERT INTO accounting.firms(id,name,created_by) VALUES(%s,%s,%s)",
            (firm_b, "虚构FSC16外部事务所", actor_b.user_id),
        )
        conn.execute(
            "INSERT INTO accounting.memberships(firm_id,user_id,role) VALUES(%s,%s,'owner')",
            (firm_b, actor_b.user_id),
        )
    other_book = AccountingService(db).create_book(
        actor_b, BookCreate(name="虚构FSC16外部账套", period="2026-10")
    )
    service = RepairPackageService(db)
    contract = _active_contract(pg_case, service)
    with pytest.raises(AccountingError) as denied:
        service.summary(actor_b, book["id"], contract["id"])
    assert denied.value.status == 404
    with pytest.raises(AccountingError) as denied_list:
        service.list_contracts(actor_b, book["id"])
    assert denied_list.value.status == 404
    assert service.list_contracts(actor_b, other_book["id"])["items"] == []


def test_pg_concurrent_package_consumes_do_not_exceed_quota(case):
    db, maker, reviewer, book = case
    service = RepairPackageService(db)
    contract = _active_contract(case, service)
    barrier = threading.Barrier(2)

    def attempt(key: str):
        barrier.wait(timeout=5)
        try:
            return ("ok", service.consume(maker, book["id"], contract["id"], _consume(key, 6000))["amount_cents"])
        except AccountingError as error:
            return ("error", error.status)

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(attempt, ("concurrent-a", "concurrent-b")))
    assert sorted(outcomes) == [("error", 409), ("ok", 6000)]
    summary = service.summary(reviewer, book["id"], contract["id"])
    assert summary["consumed_cents"] == 6000
    assert summary["remaining_cents"] == 4000
