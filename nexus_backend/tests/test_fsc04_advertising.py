"""FSC04 second domain: advertising revenue drafts consume the shared import kernel."""

import hashlib

import pytest

from app.accounting.domain import Actor, AccountingError
from app.fsc.advertising import AdvertisingDraftWrite, AdvertisingRevenueService
from app.fsc.contracts import ContractWrite, FscContractService
from app.fsc.imports import FscImportService, ImportEnvelopeWrite
from app.fsc.profiles import FscProfileService, ProfileConfirm, ProfileWrite, REQUIRED
from test_business import business  # noqa: F401


def confirmed_profile(case, company_id):
    confirmations = {
        key: {"status": "confirmed", "owner": "财务负责人", "evidence_ref": f"客户确认/{key}",
              "confirmed_at": "2026-11-01T09:00:00+08:00"}
        for key in REQUIRED
    }
    payload = {key: None for key in REQUIRED}
    payload["_confirmations"] = confirmations
    profile = FscProfileService(case.db).create_draft(
        case.manager, company_id, ProfileWrite(industry="广告", payload=payload),
    )
    return FscProfileService(case.db).confirm(case.manager, profile["id"], ProfileConfirm(expected_version=1))


def second_manager(case):
    user_id = "fsc04-adv-second"
    with case.db.transaction(write=True, actor=case.manager) as conn:
        conn.execute("INSERT OR IGNORE INTO users(id,name,active) VALUES(?,?,1)", (user_id, "第二管理员"))
        conn.execute("INSERT OR REPLACE INTO memberships(firm_id,user_id,role) VALUES(?,?,'admin')",
                     (case.manager.firm_id, user_id))
    return Actor(user_id, case.manager.firm_id)


def active_contract(case, company_id):
    service = FscContractService(case.db)
    draft = service.create_draft(case.manager, company_id, ContractWrite(
        industry_code="广告",
        processor_code="advertising_project_revenue",
        capabilities=["advertising_project_import", "advertising_revenue_draft"],
        source_contracts=[{"code": "ad_project", "object": "project_revenue", "fields": ["external_id", "date", "amount"], "unit": "CNY"}],
        evidence_policy={"source_original": "required"},
    ))
    reviewer = second_manager(case)
    service.confirm(reviewer, draft["id"], draft["version"])
    return service.activate(case.manager, draft["id"], draft["version"])


def envelope(book_id, version=1, sha="adv"):
    return ImportEnvelopeWrite(
        source_system="advertising_project",
        source_object="project_revenue",
        external_id="ADV-P1",
        source_version=version,
        filename="广告项目收入.xlsx",
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        sha256=hashlib.sha256(sha.encode()).hexdigest(),
        size_bytes=1024,
        mapping_version="map-v1",
        rule_version="rule-v1",
        unit="CNY",
        currency="CNY",
        rows=[
            {"external_id": "A1", "date": "2026-10-02", "amount_cents": 660000, "quantity_milli": 0,
             "unit": "CNY", "currency": "CNY", "target_kind": "project", "target_id": "P1"},
            {"external_id": "A2", "date": "2026-10-03", "amount_cents": 340000, "quantity_milli": 0,
             "unit": "CNY", "currency": "CNY", "target_kind": "project", "target_id": "P1"},
        ],
    )


def test_advertising_draft_requires_confirmed_envelope_and_active_contract(business):
    case = business
    company_id = case.company["id"]
    confirmed_profile(case, company_id)
    active_contract(case, company_id)
    imports = FscImportService(case.db)
    draft = imports.import_envelope(case.manager, case.book["id"], envelope(case.book["id"]))
    service = AdvertisingRevenueService(case.db)
    with pytest.raises(AccountingError) as pending:
        service.generate(case.manager, case.book["id"], AdvertisingDraftWrite(envelope_id=draft["id"]))
    assert pending.value.status == 409
    imports.confirm(case.manager, draft["id"], 1)
    output = service.generate(case.manager, case.book["id"], AdvertisingDraftWrite(envelope_id=draft["id"]))
    assert output["total_cents"] == 1_000_000
    assert output["lines"] == [{"project_id": "P1", "amount_cents": 1_000_000, "row_numbers": [1, 2]}]
    replay = service.generate(case.manager, case.book["id"], AdvertisingDraftWrite(envelope_id=draft["id"]))
    assert replay["id"] == output["id"] and replay["replayed"] is True
    items = service.list(case.manager, case.book["id"])["items"]
    assert len(items) == 1 and items[0]["stale"] is False
    imports.import_envelope(case.manager, case.book["id"], envelope(case.book["id"], version=2, sha="adv-v2"))
    stale = service.list(case.manager, case.book["id"])["items"][0]
    assert stale["stale"] is True
    assert stale["stale_reason"] == "导入信封已被新版本替代或失效"


def test_advertising_draft_rejects_missing_contract_and_unscoped_actor(business):
    case = business
    imports = FscImportService(case.db)
    no_contract = imports.import_envelope(case.manager, case.other_book["id"], envelope(case.other_book["id"]))
    imports.confirm(case.manager, no_contract["id"], 1)
    with pytest.raises(AccountingError) as missing:
        AdvertisingRevenueService(case.db).generate(
            case.manager, case.other_book["id"], AdvertisingDraftWrite(envelope_id=no_contract["id"]),
        )
    assert missing.value.status == 409
    stranger = Actor(case.manager.user_id, "other-firm")
    with pytest.raises(AccountingError) as cross:
        AdvertisingRevenueService(case.db).list(stranger, case.book["id"])
    assert cross.value.status in {403, 404}
