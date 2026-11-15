from __future__ import annotations

import pytest

from app.accounting.directory import DirectoryService
from app.accounting.domain import AccountingError
from app.accounting.models import CompanyCreate, FirmCreate
from app.tax.pilot import DEPENDENCIES, PilotBoundaryService
from test_business import business  # noqa: F401  (pytest fixture)

CONFIG = {"authority_mode": "internal", "source_of_truth": "客户确认的月度资料", "notes": "边界台账测试"}
TP_CONFIG = {
    "authority_mode": "tplus_voucher",
    "tplus_version": "T+Cloud 3.0",
    "template_sha256": "a" * 64,
    "voucher_word": "记",
}


def deps(status="unresolved", evidence=""):
    return [
        {"code": code, "label": label, "required": True, "status": status, "evidence_ref": evidence}
        for code, label in DEPENDENCIES
    ]


def http_deps(status="unresolved", evidence=""):
    return [
        {
            "code": code,
            "status": status,
            "owner_role": "客户会计",
            "evidence_ref": evidence,
            "impact": "",
            "note": "",
        }
        for code, _label in DEPENDENCIES
    ]


def pilot(case):
    return PilotBoundaryService(case.db)


def test_unconfirmed_profile_cannot_preview_and_unresolved_cannot_confirm(business):
    service = pilot(business)
    ready = service.readiness(business.manager, business.company["id"])
    assert ready["exists"] is False
    assert len(ready["blockers"]) == 10

    draft = service.update(business.manager, business.company["id"], 0, CONFIG, deps())
    assert draft["version"] == 1 and draft["status"] == "draft"
    with pytest.raises(AccountingError) as error:
        service.preview(business.manager, business.company["id"])
    assert error.value.status == 409
    with pytest.raises(AccountingError) as error:
        service.confirm(business.manager, business.company["id"], 1)
    assert error.value.status == 409


def test_confirm_then_preview_is_bound_and_draft_change_invalidates_it(business):
    service = pilot(business)
    draft = service.update(business.manager, business.company["id"], 0, CONFIG, deps("confirmed", "客户邮件 2026-11-01"))
    confirmed = service.confirm(business.manager, business.company["id"], draft["version"])
    assert confirmed["status"] == "confirmed"
    preview = service.preview(business.manager, business.company["id"])
    assert service.validate_preview(business.manager, business.company["id"], preview["id"])["valid"] is True

    next_draft = service.update(business.manager, business.company["id"], 1, CONFIG, deps("confirmed", "客户邮件 2026-11-02"))
    assert next_draft["version"] == 2
    with pytest.raises(AccountingError) as error:
        service.validate_preview(business.manager, business.company["id"], preview["id"])
    assert error.value.status == 409

    service.confirm(business.manager, business.company["id"], 2)
    with pytest.raises(AccountingError) as error:
        service.validate_preview(business.manager, business.company["id"], preview["id"])
    assert error.value.status == 409


def test_only_manager_can_change_or_confirm(business):
    service = pilot(business)
    with pytest.raises(AccountingError) as error:
        service.update(business.actors["accountant"], business.company["id"], 0, CONFIG, deps())
    assert error.value.status in {403, 404}
    draft = service.update(business.manager, business.company["id"], 0, CONFIG, deps())
    with pytest.raises(AccountingError) as error:
        service.confirm(business.actors["accountant"], business.company["id"], draft["version"])
    assert error.value.status in {403, 404}


def test_cross_firm_company_is_not_found(business):
    other_owner = "other-owner"
    business.directory.register_identity(
        type("I", (), {"user_id": other_owner, "display_name": other_owner, "email": "other@example.test"})()
    )
    other_firm = business.directory.create_firm(other_owner, FirmCreate(name="另一家事务所"))
    from app.accounting.domain import Actor

    other_manager = Actor(other_owner, other_firm["id"])
    other_company = business.directory.create_company(other_manager, CompanyCreate(name="外部企业"))
    service = pilot(business)
    with pytest.raises(AccountingError) as error:
        service.update(business.manager, other_company["id"], 0, CONFIG, deps())
    assert error.value.status in {403, 404}


def test_tplus_mode_requires_version_and_template(business):
    service = pilot(business)
    with pytest.raises(AccountingError) as error:
        service.update(business.manager, business.company["id"], 0, {"authority_mode": "tplus_voucher"}, deps())
    assert error.value.status == 422
    valid = service.update(business.manager, business.company["id"], 0, TP_CONFIG, deps())
    assert valid["authority_mode"] == "tplus_voucher"


def test_missing_confirmed_dependency_blocks_confirm(business):
    service = pilot(business)
    items = deps()
    items[0]["status"] = "confirmed"
    items[0]["evidence_ref"] = "客户邮件 2026-11-01"
    service.update(business.manager, business.company["id"], 0, CONFIG, items)
    with pytest.raises(AccountingError) as error:
        service.confirm(business.manager, business.company["id"], 1)
    assert error.value.status == 409
    assert "832.19" in str(error.value)


def test_http_pilot_boundary_contract(tmp_path):
    from fastapi.testclient import TestClient

    from app.main import create_app

    app = create_app(tmp_path / "pilot-http.db", seed=True)
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 45991)) as client:
        token = client.get("/api/session").json()["token"]
        headers = {"Authorization": "Bearer " + token}
        with app.state.service.db.transaction() as conn:
            company = conn.execute("SELECT id FROM companies LIMIT 1").fetchone()["id"]
        ready = client.get(f"/api/tax/pilot/companies/{company}/readiness", headers=headers)
        assert ready.status_code == 200, ready.text
        assert ready.json()["exists"] is False

        body = {"expected_version": 0, "config": CONFIG, "dependencies": http_deps()}
        draft = client.post(f"/api/tax/pilot/companies/{company}/draft", headers=headers, json=body)
        assert draft.status_code == 201, draft.text

        confirm = client.post(
            f"/api/tax/pilot/companies/{company}/confirm",
            headers=headers,
            json={"expected_version": 1},
        )
        assert confirm.status_code == 409

        body["expected_version"] = 1
        body["dependencies"] = http_deps("confirmed", "客户邮件 2026-11-01")
        second = client.post(f"/api/tax/pilot/companies/{company}/draft", headers=headers, json=body)
        assert second.status_code == 201, second.text
        confirm = client.post(
            f"/api/tax/pilot/companies/{company}/confirm",
            headers=headers,
            json={"expected_version": 2},
        )
        assert confirm.status_code == 200, confirm.text
        preview = client.post(
            f"/api/tax/pilot/companies/{company}/preview", headers=headers, json={}
        )
        assert preview.status_code == 201, preview.text
        valid = client.post(
            f"/api/tax/pilot/companies/{company}/previews/{preview.json()['id']}/validate",
            headers=headers,
        )
        assert valid.status_code == 200 and valid.json()["valid"] is True
