"""FSC17 stable vehicle-asset links on PostgreSQL."""

from __future__ import annotations

import os
from uuid import uuid4

import pytest

from app.accounting.database import Database
from app.accounting.domain import AccountingError, Actor
from app.accounting.models import BookCreate
from app.accounting.service import AccountingService
from app.ledgers.models import TransportSubjectCreate, TransportSubjectUpdate
from app.ledgers.service import LedgersService
from app.periodic.links import AssetVehicleLinkService, AssetVehicleLinkWrite
from app.periodic.models import DepreciableAsset
from app.periodic.service import PeriodicService
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
        pytest.skip("FSC17 PG requires an explicit isolated PostgreSQL runner")
    target = verify_suite_target(target)
    assert (ADMIN_URL, APP_URL) == (target["admin"], target["app"])
    migrate(target["admin"])
    database = Database(target["app"])
    yield database, psycopg
    database.close()


def _accounts(conn, book, firm_id, user_id):
    ledger_id = conn.execute(
        "SELECT ledger_id FROM accounting.ledger_periods WHERE book_id=%s", (book["id"],)
    ).fetchone()[0]
    for code, name, category, normal in (
        ("1602", "累计折旧", "asset", "credit"),
        ("6602.02", "管理费用-折旧", "expense", "debit"),
    ):
        account_id, version_id = f"acct-{code}-{uuid4().hex[:8]}", uuid4().hex
        conn.execute(
            "INSERT INTO accounting.ledger_accounts(id,firm_id,company_id,ledger_id,current_version,status,created_by,created_at) "
            "VALUES(%s,%s,%s,%s,1,'active',%s,now())",
            (account_id, firm_id, book["company_id"], ledger_id, user_id),
        )
        conn.execute(
            "INSERT INTO accounting.ledger_account_versions(id,account_id,ledger_id,firm_id,company_id,version,code,name,level,is_leaf,category,normal_balance,effective_from,status,reason,created_by,created_at) "
            "VALUES(%s,%s,%s,%s,%s,1,%s,%s,1,1,%s,%s,'2026-01','active','FSC17 测试科目',%s,now())",
            (version_id, account_id, ledger_id, firm_id, book["company_id"], code, name, category, normal, user_id),
        )
    rows = conn.execute(
        "SELECT a.id,v.version,v.code FROM accounting.ledger_accounts a "
        "JOIN accounting.ledger_account_versions v ON v.account_id=a.id AND v.version=a.current_version "
        "WHERE a.ledger_id=%s AND v.code IN ('1602','6602.02')",
        (ledger_id,),
    ).fetchall()
    by_code = {row[2]: (row[0], row[1]) for row in rows}
    return by_code


def test_pg_asset_vehicle_link_is_company_scoped_and_traceable(pg):
    db, psycopg = pg
    suffix = uuid4().hex[:12]
    firm = "fsc17-firm-" + suffix
    manager = Actor(user_id=f"fsc17-owner-{suffix}", firm_id=firm)
    outsider = Actor(user_id=f"fsc17-out-{suffix}", firm_id="fsc17-other-" + suffix)
    with psycopg.connect(ADMIN_URL) as conn:
        for actor in (manager, outsider):
            conn.execute(
                "INSERT INTO accounting.users(id,name,email) VALUES(%s,%s,%s)",
                (actor.user_id, "FSC17", f"{actor.user_id}@example.test"),
            )
        conn.execute(
            "INSERT INTO accounting.firms(id,name,created_by) VALUES(%s,%s,%s)",
            (firm, "虚构FSC17事务所", manager.user_id),
        )
        conn.execute(
            "INSERT INTO accounting.memberships(firm_id,user_id,role) VALUES(%s,%s,'owner')",
            (firm, manager.user_id),
        )
        conn.execute(
            "INSERT INTO accounting.firms(id,name,created_by) VALUES(%s,%s,%s)",
            (outsider.firm_id, "虚构FSC17外部所", outsider.user_id),
        )
        conn.execute(
            "INSERT INTO accounting.memberships(firm_id,user_id,role) VALUES(%s,%s,'owner')",
            (outsider.firm_id, outsider.user_id),
        )
    book = AccountingService(db).create_book(manager, BookCreate(name="FSC17 合成账套", period="2026-10"))
    with psycopg.connect(ADMIN_URL) as conn:
        accounts = _accounts(conn, book, manager.firm_id, manager.user_id)
    ledger = LedgersService(db)
    vehicle = ledger.create_transport_subject(
        manager, book["id"], TransportSubjectCreate(kind="vehicle", external_id="PG-VIN-1", display_name="京A·PG17")
    )
    periodic = PeriodicService(db)
    asset = periodic.create_asset(
        manager,
        DepreciableAsset(
            book_id=book["id"], kind="fixed", name="PG 合成车辆资产", cost="1200.00",
            residual="0.00", life_months=12, start_period="2026-10",
            expense_account_id=accounts["6602.02"][0], expense_account_version=accounts["6602.02"][1],
            accum_account_id=accounts["1602"][0], accum_account_version=accounts["1602"][1],
            reason="FSC17 合成资产依据",
        ),
    )
    links = AssetVehicleLinkService(db)
    linked = links.link(manager, asset["id"], AssetVehicleLinkWrite(vehicle_external_id="PG-VIN-1"))
    assert linked["status"] == "active" and linked["vehicle_name"] == "京A·PG17"
    assert links.for_vehicle(manager, book["id"], "PG-VIN-1")["items"][0]["asset_id"] == asset["id"]

    with pytest.raises(AccountingError) as denied:
        links.summary(outsider, asset["id"])
    assert denied.value.status in {403, 404}

    ledger.update_transport_subject(
        manager, book["id"], vehicle["id"],
        TransportSubjectUpdate(expected_version=1, display_name="京A·PG17-换牌", active=True, reason="PG 换牌"),
    )
    assert "版本" in links.summary(manager, asset["id"])["stale_reason"] or "指纹" in links.summary(manager, asset["id"])["stale_reason"]

    draft = periodic.generate_asset(manager, asset["id"])
    with psycopg.connect(ADMIN_URL) as conn:
        row = conn.execute(
            "SELECT aux_json::jsonb->>'vehicle_external_id',source_ref FROM accounting.periodic_draft_lines "
            "WHERE draft_id=%s ORDER BY line_no LIMIT 1",
            (draft["draft"]["id"],),
        ).fetchone()
    assert row[0] == "PG-VIN-1"
    assert row[1].endswith(";vehicle:PG-VIN-1@v1")
