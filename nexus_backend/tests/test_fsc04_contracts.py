"""FSC04: industry contracts are versioned, reviewable data, not executable code."""

import sqlite3

import pytest

from app.accounting.domain import Actor, AccountingError
from app.fsc.contracts import ContractWrite, FscContractService
from app.fsc.profiles import FscProfileService, ProfileConfirm, ProfileWrite, REQUIRED
from test_business import business  # noqa: F401


def contract_payload(**overrides):
    data = {
        "industry_code": "出租车",
        "processor_code": "taxi_fee",
        "capabilities": ["taxi_fee_import", "taxi_bank_allocation"],
        "source_contracts": [{
            "code": "taxi_fee_ledger",
            "object": "fee_rows",
            "fields": ["external_id", "date", "amount", "quantity", "driver_external_id"],
            "unit": "CNY",
        }],
        "evidence_policy": {"source_original": "required", "review": "independent"},
    }
    data.update(overrides)
    return ContractWrite(**data)


def confirmed_profile(case, company_id, industry="出租车"):
    confirmations = {
        key: {"status": "confirmed", "owner": "财务负责人", "evidence_ref": f"客户确认/{key}",
              "confirmed_at": "2026-11-01T09:00:00+08:00"}
        for key in REQUIRED
    }
    payload = {key: None for key in REQUIRED}
    payload["_confirmations"] = confirmations
    profile = FscProfileService(case.db).create_draft(
        case.manager, company_id, ProfileWrite(industry=industry, payload=payload),
    )
    return FscProfileService(case.db).confirm(case.manager, profile["id"], ProfileConfirm(expected_version=1))


def second_manager(case):
    user_id = "fsc04-second-manager"
    with case.db.transaction(write=True, actor=case.manager) as conn:
        conn.execute("INSERT OR IGNORE INTO users(id,name,active) VALUES(?,?,1)", (user_id, "第二管理员"))
        conn.execute("INSERT OR REPLACE INTO memberships(firm_id,user_id,role) VALUES(?,?,'admin')",
                     (case.manager.firm_id, user_id))
    return Actor(user_id, case.manager.firm_id)


def test_contract_requires_independent_confirmation_then_activation(business):
    case = business
    company_id = case.company["id"]
    confirmed_profile(case, company_id)
    service = FscContractService(case.db)
    draft = service.create_draft(case.manager, company_id, contract_payload())
    assert draft["status"] == "draft"
    with pytest.raises(AccountingError) as blocked:
        service.activate(case.manager, draft["id"], 1)
    assert blocked.value.status == 409
    with pytest.raises(AccountingError) as self_confirm:
        service.confirm(case.manager, draft["id"], 1)
    assert self_confirm.value.status == 409
    reviewer = second_manager(case)
    confirmed = service.confirm(reviewer, draft["id"], 1)
    assert confirmed["status"] == "confirmed"
    active = service.activate(case.manager, draft["id"], 1)
    assert active["status"] == "active"
    assert active["processor_code"] == "taxi_fee"
    active_rows = service.active_matrix(case.manager)["items"]
    assert [row["id"] for row in active_rows] == [draft["id"]]
    with pytest.raises(AccountingError) as duplicate:
        service.activate(case.manager, draft["id"], 1)
    assert duplicate.value.status == 409
    second = service.create_draft(case.manager, company_id, contract_payload(industry_code="出租车"))
    service.confirm(reviewer, second["id"], second["version"])
    service.activate(case.manager, second["id"], second["version"])
    rows = service.list(case.manager, company_id)["items"]
    assert [row["status"] for row in rows].count("active") == 1
    assert next(row for row in rows if row["id"] == draft["id"])["status"] == "superseded"
    third = service.create_draft(case.manager, company_id, contract_payload())
    with sqlite3.connect(case.db.path) as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE fsc_industry_contracts SET status='active' WHERE id=?", (third["id"],))

    matrix = service.capability_matrix(case.manager)
    company = next(item for item in matrix["items"] if item["company_id"] == company_id)
    assert "taxi_fee_import" in company["enabled"]
    assert "advertising_project_import" in company["unsupported"]
    assert company["contract_version"] == second["version"]
    with pytest.raises(AccountingError) as denied:
        service.capability_matrix(case.actors["employee"])
    assert denied.value.status in {403, 404}


def test_contract_rejects_executable_config_and_unconfirmed_profile(business):
    case = business
    service = FscContractService(case.db)
    with pytest.raises(AccountingError) as processor:
        service.create_draft(case.manager, case.company["id"], contract_payload(processor_code="os.system"))
    assert processor.value.status == 422
    with pytest.raises(AccountingError) as forbidden:
        service.create_draft(case.manager, case.company["id"], contract_payload(source_contracts=[{
            "code": "bad", "fields": ["amount"], "sql": "select 1",
        }]))
    assert forbidden.value.status == 422

    draft = service.create_draft(case.manager, case.other["id"], contract_payload())
    reviewer = second_manager(case)
    service.confirm(reviewer, draft["id"], 1)
    with pytest.raises(AccountingError) as missing_profile:
        service.activate(case.manager, draft["id"], 1)
    assert missing_profile.value.status == 409

    stranger = Actor(case.manager.user_id, "other-firm")
    with pytest.raises(AccountingError) as cross_firm:
        service.list(stranger, case.company["id"])
    assert cross_firm.value.status in {403, 404}
    assert service.active_matrix(stranger)["items"] == []
