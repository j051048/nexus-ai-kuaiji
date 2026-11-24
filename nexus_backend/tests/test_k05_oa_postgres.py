"""K05 PostgreSQL: OA snapshot and material request stay scoped and immutable."""
from __future__ import annotations

import os
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.accounting.database import Database
from app.accounting.domain import Actor
from app.accounting.models import BookCreate
from app.accounting.service import AccountingService
from app.cases.oa_snapshots import OALine, OAMaterialRequestWrite, OASnapshotService, OASnapshotWrite

pytestmark = pytest.mark.skipif(
    os.getenv("ACCOUNTING_AK05_ISOLATED_TARGET") != "1",
    reason="需要显式 AK05 四角色隔离 PostgreSQL 运行器",
)

ADMIN_URL = os.getenv("ACCOUNTING_TEST_POSTGRES_URL")
APP_URL = os.getenv("ACCOUNTING_TEST_DATABASE_URL")


@pytest.fixture(scope="module")
def pg():
    from scripts.pg_suite_target import load_suite_target, verify_suite_target
    from scripts.migrate_accounting import migrate

    target = load_suite_target()
    if target is None:
        pytest.skip("K05 PG requires an explicit isolated PostgreSQL runner")
    target = verify_suite_target(target)
    assert (ADMIN_URL, APP_URL) == (target["admin"], target["app"])
    migrate(target["admin"])
    database = Database(target["app"])
    yield database
    database.close()


@pytest.fixture
def case(pg):
    import psycopg

    suffix = uuid4().hex[:12]
    firm = "k05-firm-" + suffix
    maker = Actor(user_id=f"k05-maker-{suffix}", firm_id=firm)
    with psycopg.connect(ADMIN_URL) as conn:
        conn.execute("INSERT INTO accounting.users(id,name,email) VALUES(%s,'maker',%s)", (maker.user_id, f"{maker.user_id}@example.test"))
        conn.execute("INSERT INTO accounting.firms(id,name,created_by) VALUES(%s,'虚构K05事务所',%s)", (firm, maker.user_id))
        conn.execute("INSERT INTO accounting.memberships(firm_id,user_id,role) VALUES(%s,%s,'owner')", (firm, maker.user_id))
    book = AccountingService(pg).create_book(maker, BookCreate(name="虚构K05账套", period="2026-10"))
    return SimpleNamespace(db=pg, manager=maker, book=book)


def test_snapshot_and_material_request_are_versioned_and_immutable(case):
    import psycopg

    service = OASnapshotService(case.db)
    body = OASnapshotWrite(
        oa_event_id="OA-PG-1", oa_version=1, period="2026-10", title="OA 已审只补材料",
        approved_by="OA 审批人", approved_at="2026-10-12T09:00:00+08:00",
        lines=[OALine(account_code="6602.03", amount_cents=12500)],
    )
    snap = service.register(case.manager, case.book["id"], body)
    again = service.register(case.manager, case.book["id"], body)
    assert again["id"] == snap["id"] and again["fingerprint"] == snap["fingerprint"]
    request = service.create_material_request(
        case.manager, case.book["id"],
        OAMaterialRequestWrite(snapshot_id=snap["id"], title="只补 OA 材料", reason="OA 审批后只收材料"),
    )
    duplicate = service.create_material_request(
        case.manager, case.book["id"],
        OAMaterialRequestWrite(snapshot_id=snap["id"], title="只补 OA 材料", reason="OA 审批后只收材料"),
    )
    assert duplicate["id"] == request["id"]
    assert len(service.list_material_requests(case.manager, case.book["id"])["items"]) == 1
    with psycopg.connect(ADMIN_URL) as conn, pytest.raises(psycopg.errors.CheckViolation):
        conn.execute("UPDATE accounting.oa_approval_snapshots SET title='篡改' WHERE id=%s", (snap["id"],))
