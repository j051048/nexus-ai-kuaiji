"""FSC17 stable vehicle identity for periodic assets."""

import sqlite3
from types import SimpleNamespace

import pytest

from app.accounting.directory import DirectoryService
from app.accounting.domain import AccountingError, Actor
from app.accounting.models import CompanyCreate, FirmCreate
from app.accounting.service import AccountingService
from app.ledgers.models import TransportSubjectCreate, TransportSubjectUpdate
from app.ledgers.service import LedgersService
from app.periodic.service import PeriodicService
from app.periodic.links import (
    AssetVehicleLinkRevoke,
    AssetVehicleLinkService,
    AssetVehicleLinkWrite,
)
from test_business import business  # noqa: F401


def _asset(case, name="京A资产"):
    with case.db.transaction() as conn:
        ledger_id = conn.execute("SELECT ledger_id FROM ledger_periods WHERE book_id=?", (case.book["id"],)).fetchone()["ledger_id"]
        accounts = list(conn.execute(
            "SELECT a.id,v.version,v.code FROM ledger_accounts a "
            "JOIN ledger_account_versions v ON v.account_id=a.id AND v.version=a.current_version "
            "WHERE a.ledger_id=? AND v.status='active' ORDER BY v.code",
            (ledger_id,),
        ))
        if len(accounts) < 2:
            for code, name, category, normal in (
                ("1602", "累计折旧", "asset", "credit"),
                ("6602.02", "管理费用-折旧", "expense", "debit"),
            ):
                account_id = f"acct-{code}"
                conn.execute(
                    "INSERT INTO ledger_accounts(id,firm_id,company_id,ledger_id,current_version,status,created_by,created_at) "
                    "VALUES(?,?,?,?,1,'active',?,?)",
                    (account_id, case.manager.firm_id, case.book["company_id"], ledger_id, case.manager.user_id, "2026-01-01T00:00:00+00:00"),
                )
                conn.execute(
                    "INSERT INTO ledger_account_versions(id,account_id,ledger_id,firm_id,company_id,version,code,name,"
                    "level,is_leaf,category,normal_balance,effective_from,status,reason,created_by,created_at) "
                    "VALUES(?,?,?,?,?,1,?,?,1,1,?,?, '2026-01','active','FSC17 测试科目',?,?)",
                    (f"acctv-{code}", account_id, ledger_id, case.manager.firm_id, case.book["company_id"], code, name, category, normal, case.manager.user_id, "2026-01-01T00:00:00+00:00"),
                )
            accounts = list(conn.execute(
                "SELECT a.id,v.version,v.code FROM ledger_accounts a "
                "JOIN ledger_account_versions v ON v.account_id=a.id AND v.version=a.current_version "
                "WHERE a.ledger_id=? AND v.status='active' ORDER BY v.code",
                (ledger_id,),
            ))
        expense, accum = next(r for r in accounts if r["code"] == "6602.02"), next(r for r in accounts if r["code"] == "1602")
        asset_id = f"asset-{name}"
        conn.execute(
            "INSERT INTO periodic_assets(id,book_id,kind,name,cost_cents,residual_cents,"
            "life_months,start_period,accumulated_cents,expense_account_id,expense_account_version,"
            "accum_account_id,accum_account_version,status,created_by,created_at) "
            "VALUES(?,?,'fixed',?,120000,0,12,'2026-10',0,?,?,?,?,'active',?,?)",
            (asset_id, case.book["id"], name, expense["id"], expense["version"], accum["id"], accum["version"], "owner", "2026-10-01T00:00:00+00:00"),
        )
    return asset_id


def _vehicle(case, external="VIN-FSC17", name="京A·FSC17"):
    return LedgersService(case.db).create_transport_subject(
        case.manager,
        case.book["id"],
        TransportSubjectCreate(kind="vehicle", external_id=external, display_name=name),
    )


def test_link_vehicle_uses_stable_id_and_survives_version_change(business):
    case = business
    service = AssetVehicleLinkService(case.db)
    asset_id = _asset(case)
    vehicle = _vehicle(case)

    linked = service.link(case.manager, asset_id, AssetVehicleLinkWrite(vehicle_external_id=vehicle["external_id"]))
    assert linked["status"] == "active"
    assert linked["source_version"] == 1
    assert linked["vehicle_name"] == "京A·FSC17"

    again = service.link(case.manager, asset_id, AssetVehicleLinkWrite(vehicle_external_id=vehicle["external_id"]))
    assert again["id"] == linked["id"]
    assert service.for_vehicle(case.manager, case.book["id"], "VIN-FSC17")["items"][0]["asset_id"] == asset_id

    LedgersService(case.db).update_transport_subject(
        case.manager, case.book["id"], vehicle["id"],
        TransportSubjectUpdate(expected_version=1, display_name="京A·FSC17-换牌", active=True, reason="车辆换牌"),
    )
    stale = service.summary(case.manager, asset_id)
    assert stale["status"] == "active"
    assert "版本" in stale["stale_reason"] or "指纹" in stale["stale_reason"]
    assert stale["vehicle_name"] == "京A·FSC17-换牌"

    deferred = PeriodicService(case.db).generate_asset(case.manager, asset_id)
    with case.db.transaction() as conn:
        line = conn.execute(
            "SELECT aux_json,source_ref FROM periodic_draft_lines WHERE draft_id=? ORDER BY line_no LIMIT 1",
            (deferred["draft"]["id"],),
        ).fetchone()
    assert '"vehicle_external_id": "VIN-FSC17"' in line["aux_json"]
    assert line["source_ref"].startswith("asset:") and line["source_ref"].endswith(";vehicle:VIN-FSC17@v1")

    revoked = service.revoke(case.manager, asset_id, AssetVehicleLinkRevoke(reason="车辆退役"))
    assert revoked["status"] == "revoked"
    assert revoked["revoke_reason"] == "车辆退役"
    with pytest.raises(AccountingError) as error:
        service.revoke(case.manager, asset_id, AssetVehicleLinkRevoke(reason="再次撤销"))
    assert error.value.status == 404


def test_link_is_company_scoped_and_rejects_employee_and_other_vehicle(business):
    case = business
    service = AssetVehicleLinkService(case.db)
    asset_id = _asset(case)
    vehicle = _vehicle(case, "VIN-A")
    second_vehicle = _vehicle(case, "VIN-B", "京A·B")

    with pytest.raises(AccountingError) as error:
        service.link(case.actors["employee"], asset_id, AssetVehicleLinkWrite(vehicle_external_id="VIN-A"))
    assert error.value.status == 404

    service.link(case.manager, asset_id, AssetVehicleLinkWrite(vehicle_external_id="VIN-A"))
    with pytest.raises(AccountingError) as error:
        service.link(case.manager, asset_id, AssetVehicleLinkWrite(vehicle_external_id="VIN-B"))
    assert error.value.status == 409
    assert "其他车辆" in str(error.value)

    # The link is not a book/company grant: a manager from another company cannot read it.
    other = case.other_book["id"]
    assert service.for_vehicle(case.manager, other, "VIN-A")["items"] == []
