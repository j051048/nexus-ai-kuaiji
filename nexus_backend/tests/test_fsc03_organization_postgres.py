"""FSC03: organization projection is firm-scoped and read-only on PostgreSQL."""

from __future__ import annotations

import os
from uuid import uuid4

import pytest

from app.accounting.database import Database
from app.accounting.domain import AccountingError
from app.accounting.models import BookCreate
from app.accounting.service import AccountingService, Actor
from app.fsc.organization import FscOrganizationService
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
        pytest.skip("FSC03 PG requires an explicit isolated PostgreSQL runner")
    target = verify_suite_target(target)
    assert (ADMIN_URL, APP_URL) == (target["admin"], target["app"])
    migrate(target["admin"])
    database = Database(target["app"])
    yield database, psycopg
    database.close()


def test_pg_organization_projection_is_firm_scoped(pg):
    db, psycopg = pg
    suffix = uuid4().hex[:12]
    firm = "fsc03-firm-" + suffix
    manager = Actor(user_id=f"fsc03-manager-{suffix}", firm_id=firm)
    employee = Actor(user_id=f"fsc03-operator-{suffix}", firm_id=firm)
    outsider = Actor(user_id=f"fsc03-outsider-{suffix}", firm_id="fsc03-other-" + suffix)
    with psycopg.connect(ADMIN_URL) as conn:
        for actor, role in ((manager, "owner"), (employee, "operator"), (outsider, "owner")):
            conn.execute(
                "INSERT INTO accounting.users(id,name,email) VALUES(%s,%s,%s)",
                (actor.user_id, role, f"{actor.user_id}@example.test"),
            )
        for firm_id, owner in ((firm, manager.user_id), (outsider.firm_id, outsider.user_id)):
            conn.execute(
                "INSERT INTO accounting.firms(id,name,created_by) VALUES(%s,%s,%s)",
                (firm_id, "虚构FSC03事务所", owner),
            )
            conn.execute(
                "INSERT INTO accounting.memberships(firm_id,user_id,role) VALUES(%s,%s,'owner')",
                (firm_id, owner),
            )
        conn.execute(
            "INSERT INTO accounting.memberships(firm_id,user_id,role) VALUES(%s,%s,'operator')",
            (firm, employee.user_id),
        )
    book = AccountingService(db).create_book(manager, BookCreate(name="虚构FSC03账套", period="2026-10"))
    service = FscOrganizationService(db)
    body = service.matrix(manager)
    assert body["company_count"] >= 1
    company = next(item for item in body["companies"] if item["company_id"] == book["company_id"])
    plan_book = next(item for item in company["books"] if item["book_id"] == book["id"])
    assert plan_book["plan"] is None
    assert manager.user_id in plan_book["book_roles"]
    assert any(warning["kind"] == "missing_service_plan" for warning in company["warnings"])

    with pytest.raises(AccountingError) as denied:
        service.matrix(employee)
    assert denied.value.status in {403, 404}
    outside_body = service.matrix(outsider)
    assert all(item["company_id"] != book["company_id"] for item in outside_body["companies"])
